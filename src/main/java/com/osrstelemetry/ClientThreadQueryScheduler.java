package com.osrstelemetry;

import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

/**
 * Bounded newest-wins admission in front of RuneLite client-thread queries.
 *
 * <p>Only the active job is dispatched. One newer distinct job may wait behind
 * it; a still newer distinct job supersedes that pending job before it reaches
 * the client thread. Identical active or pending keys share the same work.</p>
 */
final class ClientThreadQueryScheduler implements AutoCloseable
{
	static final String DIAGNOSTICS_SCHEMA = "client_thread_query_diagnostics.v1";
	private static final int TIMING_SAMPLE_LIMIT = 64;

	@FunctionalInterface
	interface Dispatcher
	{
		void dispatch(Runnable runnable);
	}

	@FunctionalInterface
	interface NanoClock
	{
		long nanoTime();
	}

	enum Status
	{
		SUCCESS,
		TIMED_OUT,
		EXPIRED,
		SUPERSEDED,
		LATE,
		FAILED,
		INTERRUPTED,
		CLOSED
	}

	private final Dispatcher dispatcher;
	private final NanoClock clock;
	private Job<?> active;
	private Job<?> pending;
	private boolean closed;
	private long submittedCount;
	private long executedCount;
	private long coalescedCount;
	private long supersededCount;
	private long timedOutCount;
	private long expiredBeforeExecutionCount;
	private long lateResultCount;
	private long failedCount;
	private long interruptedCount;
	private int maxDepth;
	private long lastQueueWaitNanos = -1L;
	private long maxQueueWaitNanos = -1L;
	private long lastExecutionNanos = -1L;
	private long maxExecutionNanos = -1L;
	private final ArrayDeque<Long> queueWaitSamplesNanos = new ArrayDeque<>();
	private final ArrayDeque<Long> executionSamplesNanos = new ArrayDeque<>();

	ClientThreadQueryScheduler(Dispatcher dispatcher)
	{
		this(dispatcher, System::nanoTime);
	}

	ClientThreadQueryScheduler(Dispatcher dispatcher, NanoClock clock)
	{
		this.dispatcher = Objects.requireNonNull(dispatcher, "dispatcher");
		this.clock = Objects.requireNonNull(clock, "clock");
	}

	<T> Submission<T> submit(
			String lane,
			String requestKey,
			long timeoutMillis,
			Callable<T> work)
	{
		String normalizedLane = requireText(lane, "lane");
		String normalizedKey = requireText(requestKey, "requestKey");
		if (timeoutMillis <= 0L)
		{
			throw new IllegalArgumentException("timeoutMillis must be positive");
		}
		Callable<T> requiredWork = Objects.requireNonNull(work, "work");
		long submittedNanos = clock.nanoTime();
		long timeoutNanos = TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
		long callerDeadlineNanos = saturatedAdd(submittedNanos, timeoutNanos);
		Job<T> created = new Job<>(
				normalizedLane,
				normalizedKey,
				requiredWork,
				submittedNanos,
				callerDeadlineNanos);
		Job<?> dispatch = null;
		Job<?> superseded = null;
		Submission<T> submission;
		synchronized (this)
		{
			submittedCount++;
			if (closed)
			{
				created.future.complete(Result.closed());
				return new Submission<>(this, created, callerDeadlineNanos, timeoutMillis, false);
			}

			if (matches(active, normalizedLane, normalizedKey))
			{
				@SuppressWarnings("unchecked")
				Job<T> shared = (Job<T>) active;
				shared.deadlineNanos = Math.max(shared.deadlineNanos, callerDeadlineNanos);
				coalescedCount++;
				return new Submission<>(this, shared, callerDeadlineNanos, timeoutMillis, true);
			}
			if (matches(pending, normalizedLane, normalizedKey))
			{
				@SuppressWarnings("unchecked")
				Job<T> shared = (Job<T>) pending;
				shared.deadlineNanos = Math.max(shared.deadlineNanos, callerDeadlineNanos);
				coalescedCount++;
				return new Submission<>(this, shared, callerDeadlineNanos, timeoutMillis, true);
			}

			if (active == null)
			{
				active = created;
				dispatch = created;
			}
			else
			{
				if (pending != null)
				{
					superseded = pending;
					supersededCount++;
				}
				pending = created;
			}
			updateMaxDepthLocked();
			submission = new Submission<>(this, created, callerDeadlineNanos, timeoutMillis, false);
		}

		if (superseded != null)
		{
			completeUnchecked(superseded, Result.superseded());
		}
		if (dispatch != null)
		{
			dispatch(dispatch);
		}
		return submission;
	}

	private void dispatch(Job<?> job)
	{
		try
		{
			dispatcher.dispatch(() -> run(job));
		}
		catch (RuntimeException e)
		{
			Job<?> next;
			synchronized (this)
			{
				if (active != job)
				{
					return;
				}
				failedCount++;
				active = null;
				next = promoteLocked();
			}
			completeUnchecked(job, Result.failed(e, false, -1L, -1L));
			if (next != null)
			{
				dispatch(next);
			}
		}
	}

	private <T> void run(Job<T> job)
	{
		long startedNanos = clock.nanoTime();
		Job<?> next = null;
		boolean expired = false;
		synchronized (this)
		{
			if (closed || active != job)
			{
				return;
			}
			job.queueWaitNanos = elapsedNanos(job.submittedNanos, startedNanos);
			lastQueueWaitNanos = job.queueWaitNanos;
			maxQueueWaitNanos = Math.max(maxQueueWaitNanos, job.queueWaitNanos);
			recordSampleLocked(queueWaitSamplesNanos, job.queueWaitNanos);
			if (startedNanos >= job.deadlineNanos)
			{
				expiredBeforeExecutionCount++;
				active = null;
				next = promoteLocked();
				expired = true;
			}
			else
			{
				job.workExecuted = true;
				executedCount++;
			}
		}
		if (expired)
		{
			completeUnchecked(job, Result.expired(job.queueWaitNanos));
			if (next != null)
			{
				dispatch(next);
			}
			return;
		}

		T value = null;
		Throwable failure = null;
		try
		{
			value = job.work.call();
		}
		catch (Throwable e)
		{
			failure = e;
		}
		long completedNanos = clock.nanoTime();
		long executionNanos = elapsedNanos(startedNanos, completedNanos);
		Result<T> result;
		synchronized (this)
		{
			job.completedNanos = completedNanos;
			job.executionNanos = executionNanos;
			lastExecutionNanos = executionNanos;
			maxExecutionNanos = Math.max(maxExecutionNanos, executionNanos);
			recordSampleLocked(executionSamplesNanos, executionNanos);
			if (failure != null)
			{
				failedCount++;
				result = Result.failed(
						failure,
						true,
						job.queueWaitNanos,
						executionNanos);
			}
			else if (completedNanos > job.deadlineNanos)
			{
				lateResultCount++;
				result = Result.late(job.queueWaitNanos, executionNanos);
			}
			else
			{
				result = Result.success(value, job.queueWaitNanos, executionNanos);
			}
			if (active == job)
			{
				active = null;
			}
			next = promoteLocked();
		}
		job.future.complete(result);
		if (next != null)
		{
			dispatch(next);
		}
	}

	private synchronized Result<?> recordWaitTimeout(Submission<?> submission)
	{
		if (submission.markTimeoutRecorded())
		{
			timedOutCount++;
		}
		return Result.timedOut(
				submission.job.workExecuted,
				submission.job.queueWaitNanos,
				submission.job.executionNanos);
	}

	private synchronized void recordWaitInterrupted(Submission<?> submission)
	{
		if (submission.markInterruptRecorded())
		{
			interruptedCount++;
		}
	}

	private synchronized long remainingNanos(long deadlineNanos)
	{
		return Math.max(0L, deadlineNanos - clock.nanoTime());
	}

	synchronized Map<String, Object> diagnostics(Submission<?> submission, Result<?> result)
	{
		Map<String, Object> diagnostics = new LinkedHashMap<>();
		diagnostics.put("schema", DIAGNOSTICS_SCHEMA);
		diagnostics.put("lane", submission.job.lane);
		diagnostics.put("requestStatus", result.status.name());
		diagnostics.put("requestCoalesced", submission.coalesced);
		diagnostics.put("workExecuted", result.workExecuted);
		diagnostics.put("timeoutMillis", submission.timeoutMillis);
		diagnostics.put("queueWaitMillis", millisOrNull(result.queueWaitNanos));
		diagnostics.put("executionMillis", millisOrNull(result.executionNanos));
		diagnostics.put("activeRequestCount", active == null ? 0 : 1);
		diagnostics.put("pendingRequestCount", pending == null ? 0 : 1);
		diagnostics.put("maxDepth", maxDepth);
		diagnostics.put("submittedCount", submittedCount);
		diagnostics.put("executedCount", executedCount);
		diagnostics.put("coalescedCount", coalescedCount);
		diagnostics.put("supersededCount", supersededCount);
		diagnostics.put("timedOutCount", timedOutCount);
		diagnostics.put("expiredBeforeExecutionCount", expiredBeforeExecutionCount);
		diagnostics.put("lateResultCount", lateResultCount);
		diagnostics.put("failedCount", failedCount);
		diagnostics.put("interruptedCount", interruptedCount);
		diagnostics.put("lastQueueWaitMillis", millisOrNull(lastQueueWaitNanos));
		diagnostics.put("maxQueueWaitMillis", millisOrNull(maxQueueWaitNanos));
		diagnostics.put("queueWaitSampleCount", queueWaitSamplesNanos.size());
		diagnostics.put("queueWaitP50Millis", percentileMillisOrNull(queueWaitSamplesNanos, 0.50));
		diagnostics.put("queueWaitP95Millis", percentileMillisOrNull(queueWaitSamplesNanos, 0.95));
		diagnostics.put("lastExecutionMillis", millisOrNull(lastExecutionNanos));
		diagnostics.put("maxExecutionMillis", millisOrNull(maxExecutionNanos));
		diagnostics.put("executionSampleCount", executionSamplesNanos.size());
		diagnostics.put("executionP50Millis", percentileMillisOrNull(executionSamplesNanos, 0.50));
		diagnostics.put("executionP95Millis", percentileMillisOrNull(executionSamplesNanos, 0.95));
		return diagnostics;
	}

	static Map<String, Object> unavailableDiagnostics(String lane, String status)
	{
		Map<String, Object> diagnostics = new LinkedHashMap<>();
		diagnostics.put("schema", DIAGNOSTICS_SCHEMA);
		diagnostics.put("lane", lane);
		diagnostics.put("requestStatus", status);
		diagnostics.put("requestCoalesced", false);
		diagnostics.put("workExecuted", false);
		diagnostics.put("timeoutMillis", null);
		diagnostics.put("queueWaitMillis", null);
		diagnostics.put("executionMillis", null);
		diagnostics.put("activeRequestCount", 0);
		diagnostics.put("pendingRequestCount", 0);
		diagnostics.put("maxDepth", 0);
		diagnostics.put("submittedCount", 0L);
		diagnostics.put("executedCount", 0L);
		diagnostics.put("coalescedCount", 0L);
		diagnostics.put("supersededCount", 0L);
		diagnostics.put("timedOutCount", 0L);
		diagnostics.put("expiredBeforeExecutionCount", 0L);
		diagnostics.put("lateResultCount", 0L);
		diagnostics.put("failedCount", 0L);
		diagnostics.put("interruptedCount", 0L);
		diagnostics.put("lastQueueWaitMillis", null);
		diagnostics.put("maxQueueWaitMillis", null);
		diagnostics.put("queueWaitSampleCount", 0);
		diagnostics.put("queueWaitP50Millis", null);
		diagnostics.put("queueWaitP95Millis", null);
		diagnostics.put("lastExecutionMillis", null);
		diagnostics.put("maxExecutionMillis", null);
		diagnostics.put("executionSampleCount", 0);
		diagnostics.put("executionP50Millis", null);
		diagnostics.put("executionP95Millis", null);
		return diagnostics;
	}

	@Override
	public void close()
	{
		Job<?> activeToClose;
		Job<?> pendingToClose;
		synchronized (this)
		{
			if (closed)
			{
				return;
			}
			closed = true;
			activeToClose = active;
			pendingToClose = pending;
			active = null;
			pending = null;
		}
		if (activeToClose != null)
		{
			completeUnchecked(activeToClose, Result.closed());
		}
		if (pendingToClose != null)
		{
			completeUnchecked(pendingToClose, Result.closed());
		}
	}

	private Job<?> promoteLocked()
	{
		if (closed || pending == null)
		{
			return null;
		}
		active = pending;
		pending = null;
		return active;
	}

	private void updateMaxDepthLocked()
	{
		int depth = (active == null ? 0 : 1) + (pending == null ? 0 : 1);
		maxDepth = Math.max(maxDepth, depth);
	}

	private static void recordSampleLocked(ArrayDeque<Long> samples, long nanos)
	{
		if (samples.size() >= TIMING_SAMPLE_LIMIT)
		{
			samples.removeFirst();
		}
		samples.addLast(nanos);
	}

	private static Double percentileMillisOrNull(ArrayDeque<Long> samples, double percentile)
	{
		if (samples.isEmpty())
		{
			return null;
		}
		long[] ordered = new long[samples.size()];
		int index = 0;
		for (Long sample : samples)
		{
			ordered[index++] = sample;
		}
		Arrays.sort(ordered);
		int rank = Math.max(0, (int) Math.ceil(percentile * ordered.length) - 1);
		return ordered[rank] / 1_000_000.0;
	}

	private static boolean matches(Job<?> job, String lane, String requestKey)
	{
		return job != null && job.lane.equals(lane) && job.requestKey.equals(requestKey);
	}

	private static String requireText(String value, String field)
	{
		if (value == null || value.isBlank())
		{
			throw new IllegalArgumentException(field + " must be non-empty");
		}
		return value;
	}

	private static long saturatedAdd(long value, long increment)
	{
		if (increment > 0L && value > Long.MAX_VALUE - increment)
		{
			return Long.MAX_VALUE;
		}
		return value + increment;
	}

	private static long elapsedNanos(long start, long end)
	{
		return Math.max(0L, end - start);
	}

	private static Double millisOrNull(long nanos)
	{
		return nanos < 0L ? null : nanos / 1_000_000.0;
	}

	@SuppressWarnings({"rawtypes", "unchecked"})
	private static void completeUnchecked(Job<?> job, Result<?> result)
	{
		((CompletableFuture) job.future).complete(result);
	}

	static final class Submission<T>
	{
		private final ClientThreadQueryScheduler scheduler;
		private final Job<T> job;
		private final long callerDeadlineNanos;
		private final long timeoutMillis;
		private final boolean coalesced;
		private boolean timeoutRecorded;
		private boolean interruptRecorded;

		private Submission(
				ClientThreadQueryScheduler scheduler,
				Job<T> job,
				long callerDeadlineNanos,
				long timeoutMillis,
				boolean coalesced)
		{
			this.scheduler = scheduler;
			this.job = job;
			this.callerDeadlineNanos = callerDeadlineNanos;
			this.timeoutMillis = timeoutMillis;
			this.coalesced = coalesced;
		}

		Result<T> await()
		{
			Result<T> completed = job.future.getNow(null);
			if (completed != null)
			{
				return resultForCaller(completed);
			}
			long remaining = scheduler.remainingNanos(callerDeadlineNanos);
			if (remaining <= 0L)
			{
				@SuppressWarnings("unchecked")
				Result<T> timedOut = (Result<T>) scheduler.recordWaitTimeout(this);
				return timedOut;
			}
			try
			{
				return resultForCaller(job.future.get(remaining, TimeUnit.NANOSECONDS));
			}
			catch (TimeoutException e)
			{
				@SuppressWarnings("unchecked")
				Result<T> timedOut = (Result<T>) scheduler.recordWaitTimeout(this);
				return timedOut;
			}
			catch (InterruptedException e)
			{
				Thread.currentThread().interrupt();
				scheduler.recordWaitInterrupted(this);
				return Result.interrupted(
						job.workExecuted,
						job.queueWaitNanos,
						job.executionNanos);
			}
			catch (ExecutionException e)
			{
				return Result.failed(
						e.getCause() == null ? e : e.getCause(),
						job.workExecuted,
						job.queueWaitNanos,
						job.executionNanos);
			}
		}

		private Result<T> resultForCaller(Result<T> result)
		{
			if (result.status == Status.SUCCESS
					&& job.completedNanos > callerDeadlineNanos)
			{
				@SuppressWarnings("unchecked")
				Result<T> timedOut = (Result<T>) scheduler.recordWaitTimeout(this);
				return timedOut;
			}
			return result;
		}

		private synchronized boolean markTimeoutRecorded()
		{
			if (timeoutRecorded)
			{
				return false;
			}
			timeoutRecorded = true;
			return true;
		}

		private synchronized boolean markInterruptRecorded()
		{
			if (interruptRecorded)
			{
				return false;
			}
			interruptRecorded = true;
			return true;
		}
	}

	static final class Result<T>
	{
		private final Status status;
		private final T value;
		private final Throwable failure;
		private final boolean workExecuted;
		private final long queueWaitNanos;
		private final long executionNanos;

		private Result(
				Status status,
				T value,
				Throwable failure,
				boolean workExecuted,
				long queueWaitNanos,
				long executionNanos)
		{
			this.status = status;
			this.value = value;
			this.failure = failure;
			this.workExecuted = workExecuted;
			this.queueWaitNanos = queueWaitNanos;
			this.executionNanos = executionNanos;
		}

		boolean succeeded()
		{
			return status == Status.SUCCESS;
		}

		Status status()
		{
			return status;
		}

		T value()
		{
			return value;
		}

		String failureSummary()
		{
			if (failure == null)
			{
				return status.name().toLowerCase();
			}
			String message = failure.getMessage();
			return failure.getClass().getSimpleName()
					+ (message == null || message.isBlank() ? "" : ": " + message);
		}

		private static <T> Result<T> success(T value, long queueWaitNanos, long executionNanos)
		{
			return new Result<>(Status.SUCCESS, value, null, true, queueWaitNanos, executionNanos);
		}

		private static <T> Result<T> timedOut(boolean workExecuted, long queueWaitNanos, long executionNanos)
		{
			return new Result<>(Status.TIMED_OUT, null, null, workExecuted, queueWaitNanos, executionNanos);
		}

		private static <T> Result<T> expired(long queueWaitNanos)
		{
			return new Result<>(Status.EXPIRED, null, null, false, queueWaitNanos, -1L);
		}

		private static <T> Result<T> superseded()
		{
			return new Result<>(Status.SUPERSEDED, null, null, false, -1L, -1L);
		}

		private static <T> Result<T> late(long queueWaitNanos, long executionNanos)
		{
			return new Result<>(Status.LATE, null, null, true, queueWaitNanos, executionNanos);
		}

		private static <T> Result<T> failed(
				Throwable failure,
				boolean workExecuted,
				long queueWaitNanos,
				long executionNanos)
		{
			return new Result<>(Status.FAILED, null, failure, workExecuted, queueWaitNanos, executionNanos);
		}

		private static <T> Result<T> interrupted(
				boolean workExecuted,
				long queueWaitNanos,
				long executionNanos)
		{
			return new Result<>(Status.INTERRUPTED, null, null, workExecuted, queueWaitNanos, executionNanos);
		}

		private static <T> Result<T> closed()
		{
			return new Result<>(Status.CLOSED, null, null, false, -1L, -1L);
		}
	}

	private static final class Job<T>
	{
		private final String lane;
		private final String requestKey;
		private final Callable<T> work;
		private final long submittedNanos;
		private long deadlineNanos;
		private final CompletableFuture<Result<T>> future = new CompletableFuture<>();
		private volatile long completedNanos = -1L;
		private volatile long queueWaitNanos = -1L;
		private volatile long executionNanos = -1L;
		private volatile boolean workExecuted;

		private Job(
				String lane,
				String requestKey,
				Callable<T> work,
				long submittedNanos,
				long deadlineNanos)
		{
			this.lane = lane;
			this.requestKey = requestKey;
			this.work = work;
			this.submittedNanos = submittedNanos;
			this.deadlineNanos = deadlineNanos;
		}
	}
}
