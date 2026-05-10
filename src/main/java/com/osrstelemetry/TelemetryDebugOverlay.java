package com.osrstelemetry;

import com.google.gson.Gson;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.FontMetrics;
import java.awt.Graphics2D;
import java.awt.Polygon;
import java.awt.Rectangle;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import javax.inject.Inject;
import net.runelite.client.ui.overlay.Overlay;
import net.runelite.client.ui.overlay.OverlayLayer;
import net.runelite.client.ui.overlay.OverlayPosition;
import net.runelite.client.ui.overlay.OverlayPriority;

public class TelemetryDebugOverlay extends Overlay
{
	static final String STATE_SCHEMA = "telemetry_overlay_debug_state.v1";
	private static final long READ_INTERVAL_MILLIS = 250L;
	private static final int MAX_TARGET_CAP = 200;
	private static final Color PANEL_BACKGROUND = new Color(20, 20, 20, 150);
	private static final Color TEXT_COLOR = new Color(245, 245, 245);
	private static final Color GREEN = new Color(66, 220, 110);
	private static final Color YELLOW = new Color(240, 205, 70);
	private static final Color RED = new Color(240, 85, 85);
	private static final Color GRAY = new Color(165, 165, 165);

	private final TelemetryConfig config;
	private final TelemetryPlugin plugin;
	private final Gson gson;
	private OverlayDebugState cachedState;
	private Path cachedPath;
	private long lastReadMillis;

	@Inject
	TelemetryDebugOverlay(TelemetryConfig config, TelemetryPlugin plugin, Gson gson)
	{
		this.config = config;
		this.plugin = plugin;
		this.gson = gson;
		setPosition(OverlayPosition.DYNAMIC);
		setLayer(OverlayLayer.ABOVE_SCENE);
		setPriority(OverlayPriority.LOW);
	}

	@Override
	public Dimension render(Graphics2D graphics)
	{
		if (!config.telemetryDebugOverlayEnabled())
		{
			return null;
		}

		OverlayDebugState state = loadStateIfNeeded();
		drawStatusPanel(graphics, state);
		if (state == null)
		{
			return null;
		}

		TelemetryDebugOverlayMode mode = config.telemetryDebugOverlayMode();
		if (mode != TelemetryDebugOverlayMode.SUMMARY)
		{
			drawTargets(graphics, state, mode);
		}

		return null;
	}

	private OverlayDebugState loadStateIfNeeded()
	{
		long now = System.currentTimeMillis();
		Path path = resolveStatePath();
		if (path == null)
		{
			return cachedState;
		}

		if (path.equals(cachedPath) && now - lastReadMillis < READ_INTERVAL_MILLIS)
		{
			return cachedState;
		}

		cachedPath = path;
		lastReadMillis = now;
		try
		{
			if (!Files.exists(path) || Files.size(path) == 0L)
			{
				return cachedState;
			}
			String text = Files.readString(path, StandardCharsets.UTF_8);
			OverlayDebugState parsed = gson.fromJson(text, OverlayDebugState.class);
			if (parsed != null && STATE_SCHEMA.equals(parsed.schema))
			{
				cachedState = parsed;
			}
		}
		catch (IOException | RuntimeException ignored)
		{
			return cachedState;
		}

		return cachedState;
	}

	private Path resolveStatePath()
	{
		String configured = config.telemetryDebugOverlayStatePath();
		if (configured != null && !configured.isBlank())
		{
			return Path.of(configured.trim());
		}
		return plugin.currentOverlayDebugStatePath();
	}

	private void drawStatusPanel(Graphics2D graphics, OverlayDebugState state)
	{
		int x = 8;
		int y = 28;
		String line1 = "Telemetry Debug Overlay - read-only";
		String line2;
		if (state == null)
		{
			line2 = "Waiting for overlay_debug_state.json";
		}
		else
		{
			int targetCount = state.summary == null ? 0 : safeInt(state.summary.targetsWritten, 0);
			line2 = "tick " + valueOrUnknown(state.latestTick) + " | " + valueOrUnknown(state.profile) + " | targets " + targetCount;
		}
		String line3 = null;
		if (state != null && config.telemetryDebugOverlayShowCollisionWindow() && state.collisionWindow != null)
		{
			line3 = "collision window " + (Boolean.TRUE.equals(state.collisionWindow.available) ? "available" : "unknown")
					+ " | radius " + valueOrUnknown(state.collisionWindow.radius);
		}

		FontMetrics metrics = graphics.getFontMetrics();
		int width = Math.max(metrics.stringWidth(line1), metrics.stringWidth(line2));
		if (line3 != null)
		{
			width = Math.max(width, metrics.stringWidth(line3));
		}
		int height = line3 == null ? 42 : 58;
		graphics.setColor(PANEL_BACKGROUND);
		graphics.fillRoundRect(x - 4, y - metrics.getAscent() - 4, width + 12, height, 6, 6);
		graphics.setColor(TEXT_COLOR);
		graphics.drawString(line1, x, y);
		graphics.drawString(line2, x, y + 16);
		if (line3 != null)
		{
			graphics.drawString(line3, x, y + 32);
		}
	}

	private void drawTargets(Graphics2D graphics, OverlayDebugState state, TelemetryDebugOverlayMode mode)
	{
		List<OverlayTarget> targets = state.targets == null ? Collections.emptyList() : state.targets;
		int limit = Math.max(0, Math.min(MAX_TARGET_CAP, config.telemetryDebugOverlayMaxTargets()));
		int drawn = 0;
		for (OverlayTarget target : targets)
		{
			if (target == null || drawn >= limit)
			{
				break;
			}
			if (!shouldDrawTarget(target, mode))
			{
				continue;
			}
			Color color = colorFor(target);
			drawTargetShape(graphics, target, color);
			if (config.telemetryDebugOverlayShowLabels())
			{
				drawTargetLabel(graphics, target, color);
			}
			drawn++;
		}
	}

	private boolean shouldDrawTarget(OverlayTarget target, TelemetryDebugOverlayMode mode)
	{
		if (!Boolean.TRUE.equals(target.onScreen) && mode != TelemetryDebugOverlayMode.ALL)
		{
			return false;
		}
		if (mode == TelemetryDebugOverlayMode.REACHABILITY && target.directReachability == null)
		{
			return false;
		}
		if (target.aimPoint == null && target.bounds == null && target.clickboxPolygon == null && target.canvasTilePolygon == null)
		{
			return false;
		}
		return true;
	}

	private void drawTargetShape(Graphics2D graphics, OverlayTarget target, Color color)
	{
		graphics.setStroke(new BasicStroke(2.0f));
		graphics.setColor(color);
		drawPolygon(graphics, target.clickboxPolygon);
		drawPolygon(graphics, target.canvasTilePolygon);
		if (target.bounds != null)
		{
			graphics.draw(new Rectangle(
					round(target.bounds.x),
					round(target.bounds.y),
					Math.max(1, round(target.bounds.width)),
					Math.max(1, round(target.bounds.height))));
		}
		if (config.telemetryDebugOverlayShowAimPoints() && target.aimPoint != null)
		{
			int x = round(target.aimPoint.canvasX);
			int y = round(target.aimPoint.canvasY);
			graphics.drawLine(x - 5, y, x + 5, y);
			graphics.drawLine(x, y - 5, x, y + 5);
			graphics.fillOval(x - 2, y - 2, 4, 4);
		}
	}

	private void drawPolygon(Graphics2D graphics, List<List<Double>> points)
	{
		if (points == null || points.size() < 3)
		{
			return;
		}
		Polygon polygon = new Polygon();
		for (List<Double> point : points)
		{
			if (point == null || point.size() < 2 || point.get(0) == null || point.get(1) == null)
			{
				return;
			}
			polygon.addPoint(round(point.get(0)), round(point.get(1)));
		}
		graphics.drawPolygon(polygon);
	}

	private void drawTargetLabel(Graphics2D graphics, OverlayTarget target, Color color)
	{
		int x = 0;
		int y = 0;
		if (target.aimPoint != null)
		{
			x = round(target.aimPoint.canvasX) + 7;
			y = round(target.aimPoint.canvasY) - 7;
		}
		else if (target.bounds != null)
		{
			x = round(target.bounds.x + target.bounds.width) + 4;
			y = round(target.bounds.y);
		}
		else
		{
			return;
		}

		String label = formatLabel(target);
		FontMetrics metrics = graphics.getFontMetrics();
		graphics.setColor(PANEL_BACKGROUND);
		graphics.fillRoundRect(x - 3, y - metrics.getAscent() - 3, metrics.stringWidth(label) + 8, metrics.getHeight() + 4, 5, 5);
		graphics.setColor(color);
		graphics.drawString(label, x, y);
	}

	private String formatLabel(OverlayTarget target)
	{
		StringBuilder label = new StringBuilder();
		label.append(target.classId == null ? valueOrUnknown(target.name) : target.classId);
		if (target.distanceTiles != null)
		{
			label.append(" d=").append(trimNumber(target.distanceTiles));
		}
		if (config.telemetryDebugOverlayShowReachability() && target.directReachability != null)
		{
			label.append(" ").append(target.directReachability);
		}
		if (target.targetLiveState != null)
		{
			label.append(" ").append(readableLiveState(target.targetLiveState));
		}
		return label.toString();
	}

	private Color colorFor(OverlayTarget target)
	{
		String live = target.targetLiveState == null ? "" : target.targetLiveState;
		String reachability = target.directReachability == null ? "" : target.directReachability;
		if ("depleted_or_stump".equals(live) || "recently_despawned".equals(live) || "stale".equals(live))
		{
			return GRAY;
		}
		if ("blocked".equals(reachability))
		{
			return RED;
		}
		if ("unknown".equals(reachability) || "live_assumed".equals(live) || "unknown".equals(live))
		{
			return YELLOW;
		}
		return GREEN;
	}

	private String readableLiveState(String value)
	{
		if ("live_assumed".equals(value))
		{
			return "assumed";
		}
		if ("depleted_or_stump".equals(value))
		{
			return "depleted";
		}
		if ("recently_despawned".equals(value))
		{
			return "gone";
		}
		return value;
	}

	private int safeInt(Double value, int fallback)
	{
		return value == null ? fallback : value.intValue();
	}

	private int round(Double value)
	{
		return value == null ? 0 : (int) Math.round(value);
	}

	private String trimNumber(Double value)
	{
		if (value == null)
		{
			return "unknown";
		}
		if (Math.rint(value) == value)
		{
			return String.valueOf(value.intValue());
		}
		return String.format(Locale.ROOT, "%.1f", value);
	}

	private String valueOrUnknown(Object value)
	{
		return value == null ? "unknown" : String.valueOf(value);
	}

	static class OverlayDebugState
	{
		String schema;
		Double latestTick;
		String profile;
		OverlaySummary summary;
		List<OverlayTarget> targets;
		CollisionWindow collisionWindow;
	}

	static class OverlaySummary
	{
		Double candidateCount;
		Double targetsWritten;
		Boolean budgetExceeded;
		Double writeFailures;
	}

	static class OverlayTarget
	{
		String classId;
		String name;
		Double id;
		Double worldX;
		Double worldY;
		Double plane;
		Double sceneX;
		Double sceneY;
		Double distanceTiles;
		Boolean onScreen;
		Boolean geometryAvailable;
		String qualityTier;
		Double qualityScore;
		String targetLiveState;
		String directReachability;
		Double reachabilityConfidence;
		Boolean targetInCollisionWindow;
		Double pathLengthTiles;
		AimPoint aimPoint;
		Bounds bounds;
		List<List<Double>> clickboxPolygon;
		List<List<Double>> canvasTilePolygon;
	}

	static class AimPoint
	{
		Double canvasX;
		Double canvasY;
		String source;
	}

	static class Bounds
	{
		Double x;
		Double y;
		Double width;
		Double height;
	}

	static class CollisionWindow
	{
		Boolean available;
		Double minSceneX;
		Double maxSceneX;
		Double minSceneY;
		Double maxSceneY;
		Double radius;
		Double playerSceneX;
		Double playerSceneY;
	}
}
