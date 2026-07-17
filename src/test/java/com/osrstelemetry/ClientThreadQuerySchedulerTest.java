package com.osrstelemetry;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.Test;

public class ClientThreadQuerySchedulerTest
{
	@Test
	public void identicalActiveRequestsCoalesceIntoOneClientThreadJob()
	{
		ManualClock clock = new ManualClock();
		ManualDispatcher dispatcher = new ManualDispatcher();
		ClientThreadQueryScheduler scheduler = new ClientThreadQueryScheduler(dispatcher, clock);
		AtomicInteger executions = new AtomicInteger();

		ClientThreadQueryScheduler.Submission<String> first = scheduler.submit(
				"world_model", "same", 100L, () -> "value-" + executions.incrementAndGet());
		ClientThreadQueryScheduler.Submission<String> second = scheduler.submit(
				"world_model", "same", 100L, () -> "unused");

		assertEquals(1, dispatcher.size());
		dispatcher.runNext();
		assertEquals("value-1", first.await().value());
		assertEquals("value-1", second.await().value());
		assertEquals(1, executions.get());
		Map<String, Object> diagnostics = scheduler.diagnostics(second, second.await());
		assertEquals(2L, diagnostics.get("submittedCount"));
		assertEquals(1L, diagnostics.get("executedCount"));
		assertEquals(1L, diagnostics.get("coalescedCount"));
		assertEquals(1, diagnostics.get("maxDepth"));
		assertTrue((Boolean) diagnostics.get("requestCoalesced"));
	}

	@Test
	public void coalescingExtendsSharedWorkButNotTheOlderCallersDeadline()
	{
		ManualClock clock = new ManualClock();
		ManualDispatcher dispatcher = new ManualDispatcher();
		ClientThreadQueryScheduler scheduler = new ClientThreadQueryScheduler(dispatcher, clock);
		ClientThreadQueryScheduler.Submission<String> first = scheduler.submit(
				"world_model", "same-source", 20L, () -> "fresh-at-execution");
		clock.advanceMillis(10L);
		ClientThreadQueryScheduler.Submission<String> second = scheduler.submit(
				"world_model", "same-source", 30L, () -> "unused");
		clock.advanceMillis(15L);

		dispatcher.runNext();

		assertEquals(ClientThreadQueryScheduler.Status.TIMED_OUT, first.await().status());
		assertEquals(ClientThreadQueryScheduler.Status.SUCCESS, second.await().status());
		assertEquals("fresh-at-execution", second.await().value());
		Map<String, Object> diagnostics = scheduler.diagnostics(first, first.await());
		assertEquals(1L, diagnostics.get("timedOutCount"));
		assertEquals(1L, diagnostics.get("coalescedCount"));
	}

	@Test
	public void newestDistinctPendingRequestSupersedesOlderPendingRequest()
	{
		ManualClock clock = new ManualClock();
		ManualDispatcher dispatcher = new ManualDispatcher();
		ClientThreadQueryScheduler scheduler = new ClientThreadQueryScheduler(dispatcher, clock);
		List<String> executed = new ArrayList<>();

		ClientThreadQueryScheduler.Submission<String> active = scheduler.submit(
				"world_model", "a", 100L, () -> record(executed, "a"));
		ClientThreadQueryScheduler.Submission<String> superseded = scheduler.submit(
				"world_model", "b", 100L, () -> record(executed, "b"));
		ClientThreadQueryScheduler.Submission<String> newest = scheduler.submit(
				"tile_projection", "c", 100L, () -> record(executed, "c"));

		assertEquals(ClientThreadQueryScheduler.Status.SUPERSEDED, superseded.await().status());
		assertEquals(1, dispatcher.size());
		dispatcher.runNext();
		assertEquals(1, dispatcher.size());
		dispatcher.runNext();

		assertEquals(ClientThreadQueryScheduler.Status.SUCCESS, active.await().status());
		assertEquals(ClientThreadQueryScheduler.Status.SUCCESS, newest.await().status());
		assertEquals(List.of("a", "c"), executed);
		Map<String, Object> diagnostics = scheduler.diagnostics(newest, newest.await());
		assertEquals(2, diagnostics.get("maxDepth"));
		assertEquals(1L, diagnostics.get("supersededCount"));
		assertEquals(2L, diagnostics.get("executedCount"));
	}

	@Test
	public void expiredQueuedWorkNeverTouchesClientState()
	{
		ManualClock clock = new ManualClock();
		ManualDispatcher dispatcher = new ManualDispatcher();
		ClientThreadQueryScheduler scheduler = new ClientThreadQueryScheduler(dispatcher, clock);
		AtomicInteger executions = new AtomicInteger();
		ClientThreadQueryScheduler.Submission<Integer> submission = scheduler.submit(
				"world_model", "expired", 25L, executions::incrementAndGet);

		clock.advanceMillis(25L);
		dispatcher.runNext();
		ClientThreadQueryScheduler.Result<Integer> result = submission.await();

		assertEquals(ClientThreadQueryScheduler.Status.EXPIRED, result.status());
		assertFalse(result.succeeded());
		assertEquals(0, executions.get());
		Map<String, Object> diagnostics = scheduler.diagnostics(submission, result);
		assertEquals(1L, diagnostics.get("expiredBeforeExecutionCount"));
		assertEquals(0L, diagnostics.get("executedCount"));
	}

	@Test
	public void callerTimeoutIsCountedOnceAndExpiredWorkIsSkippedLater()
	{
		ManualClock clock = new ManualClock();
		ManualDispatcher dispatcher = new ManualDispatcher();
		ClientThreadQueryScheduler scheduler = new ClientThreadQueryScheduler(dispatcher, clock);
		AtomicInteger executions = new AtomicInteger();
		ClientThreadQueryScheduler.Submission<Integer> submission = scheduler.submit(
				"world_model", "timeout", 20L, executions::incrementAndGet);

		clock.advanceMillis(21L);
		assertEquals(ClientThreadQueryScheduler.Status.TIMED_OUT, submission.await().status());
		assertEquals(ClientThreadQueryScheduler.Status.TIMED_OUT, submission.await().status());
		dispatcher.runNext();

		assertEquals(0, executions.get());
		Map<String, Object> diagnostics = scheduler.diagnostics(submission, submission.await());
		assertEquals(1L, diagnostics.get("timedOutCount"));
		assertEquals(1L, diagnostics.get("expiredBeforeExecutionCount"));
	}

	@Test
	public void lateClientThreadResultIsMeasuredButNotAcceptedAsSuccess()
	{
		ManualClock clock = new ManualClock();
		ManualDispatcher dispatcher = new ManualDispatcher();
		ClientThreadQueryScheduler scheduler = new ClientThreadQueryScheduler(dispatcher, clock);
		ClientThreadQueryScheduler.Submission<String> submission = scheduler.submit(
				"world_model", "late", 20L, () -> {
					clock.advanceMillis(21L);
					return "stale-value";
				});

		dispatcher.runNext();
		ClientThreadQueryScheduler.Result<String> result = submission.await();

		assertEquals(ClientThreadQueryScheduler.Status.LATE, result.status());
		assertFalse(result.succeeded());
		assertNull(result.value());
		Map<String, Object> diagnostics = scheduler.diagnostics(submission, result);
		assertEquals(1L, diagnostics.get("lateResultCount"));
		assertEquals(1L, diagnostics.get("executedCount"));
	}

	@Test
	public void queueAndExecutionTimingAreReportedSeparately()
	{
		ManualClock clock = new ManualClock();
		ManualDispatcher dispatcher = new ManualDispatcher();
		ClientThreadQueryScheduler scheduler = new ClientThreadQueryScheduler(dispatcher, clock);
		ClientThreadQueryScheduler.Submission<String> submission = scheduler.submit(
				"tile_projection", "timed", 100L, () -> {
					clock.advanceMillis(7L);
					return "ok";
				});

		clock.advanceMillis(5L);
		dispatcher.runNext();
		ClientThreadQueryScheduler.Result<String> result = submission.await();
		Map<String, Object> diagnostics = scheduler.diagnostics(submission, result);

		assertEquals(ClientThreadQueryScheduler.Status.SUCCESS, result.status());
		assertEquals(5.0, (Double) diagnostics.get("queueWaitMillis"), 0.0);
		assertEquals(7.0, (Double) diagnostics.get("executionMillis"), 0.0);
		assertEquals(5.0, (Double) diagnostics.get("maxQueueWaitMillis"), 0.0);
		assertEquals(7.0, (Double) diagnostics.get("maxExecutionMillis"), 0.0);
		assertEquals(1, diagnostics.get("queueWaitSampleCount"));
		assertEquals(5.0, (Double) diagnostics.get("queueWaitP50Millis"), 0.0);
		assertEquals(5.0, (Double) diagnostics.get("queueWaitP95Millis"), 0.0);
		assertEquals(1, diagnostics.get("executionSampleCount"));
		assertEquals(7.0, (Double) diagnostics.get("executionP50Millis"), 0.0);
		assertEquals(7.0, (Double) diagnostics.get("executionP95Millis"), 0.0);
	}

	@Test
	public void closeFailsQueuedSubmissionsClosedWithoutExecutingThem()
	{
		ManualClock clock = new ManualClock();
		ManualDispatcher dispatcher = new ManualDispatcher();
		ClientThreadQueryScheduler scheduler = new ClientThreadQueryScheduler(dispatcher, clock);
		AtomicInteger executions = new AtomicInteger();
		ClientThreadQueryScheduler.Submission<Integer> active = scheduler.submit(
				"world_model", "active", 100L, executions::incrementAndGet);
		ClientThreadQueryScheduler.Submission<Integer> pending = scheduler.submit(
				"world_model", "pending", 100L, executions::incrementAndGet);

		scheduler.close();
		dispatcher.runNext();

		assertEquals(ClientThreadQueryScheduler.Status.CLOSED, active.await().status());
		assertEquals(ClientThreadQueryScheduler.Status.CLOSED, pending.await().status());
		assertEquals(0, executions.get());
	}

	private static String record(List<String> values, String value)
	{
		values.add(value);
		return value;
	}

	private static final class ManualClock implements ClientThreadQueryScheduler.NanoClock
	{
		private long nowNanos;

		@Override
		public long nanoTime()
		{
			return nowNanos;
		}

		private void advanceMillis(long millis)
		{
			nowNanos += TimeUnit.MILLISECONDS.toNanos(millis);
		}
	}

	private static final class ManualDispatcher implements ClientThreadQueryScheduler.Dispatcher
	{
		private final List<Runnable> queued = new ArrayList<>();

		@Override
		public void dispatch(Runnable runnable)
		{
			queued.add(runnable);
		}

		private int size()
		{
			return queued.size();
		}

		private void runNext()
		{
			queued.remove(0).run();
		}
	}
}
