package com.osrstelemetry;

import com.google.gson.Gson;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.FontMetrics;
import java.awt.Graphics2D;
import java.awt.Polygon;
import java.awt.Rectangle;
import java.awt.Shape;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import javax.inject.Inject;
import net.runelite.api.Client;
import net.runelite.api.DecorativeObject;
import net.runelite.api.GameObject;
import net.runelite.api.GroundObject;
import net.runelite.api.Perspective;
import net.runelite.api.Point;
import net.runelite.api.Scene;
import net.runelite.api.Tile;
import net.runelite.api.TileObject;
import net.runelite.api.WallObject;
import net.runelite.api.WorldView;
import net.runelite.api.coords.LocalPoint;
import net.runelite.api.coords.WorldPoint;
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
	private final Client client;
	private OverlayDebugState cachedState;
	private Path cachedPath;
	private long lastReadMillis;

	@Inject
	TelemetryDebugOverlay(TelemetryConfig config, TelemetryPlugin plugin, Gson gson, Client client)
	{
		this.config = config;
		this.plugin = plugin;
		this.gson = gson;
		this.client = client;
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
			line2 = statusLine(state, config.telemetryDebugOverlayGeometryMode());
		}
		String line3 = null;
		if (state != null && config.telemetryDebugOverlayShowCollisionWindow() && state.collisionWindow != null)
		{
			line3 = "collision window " + (Boolean.TRUE.equals(state.collisionWindow.available) ? "available" : "unknown")
					+ " | radius " + valueOrUnknown(state.collisionWindow.radius);
		}
		String line4 = null;
		if (state != null && config.telemetryDebugOverlayShowLatestEvent()
				&& state.latestEventSummary != null && !state.latestEventSummary.isBlank())
		{
			String eventLine = "event tick " + valueOrUnknown(state.latestEventTick) + ": " + truncate(state.latestEventSummary, 70);
			if (line3 == null)
			{
				line3 = eventLine;
			}
			else
			{
				line4 = eventLine;
			}
		}

		FontMetrics metrics = graphics.getFontMetrics();
		int width = Math.max(metrics.stringWidth(line1), metrics.stringWidth(line2));
		if (line3 != null)
		{
			width = Math.max(width, metrics.stringWidth(line3));
		}
		if (line4 != null)
		{
			width = Math.max(width, metrics.stringWidth(line4));
		}
		int height = line4 == null ? (line3 == null ? 42 : 58) : 74;
		graphics.setColor(PANEL_BACKGROUND);
		graphics.fillRoundRect(x - 4, y - metrics.getAscent() - 4, width + 12, height, 6, 6);
		graphics.setColor(TEXT_COLOR);
		graphics.drawString(line1, x, y);
		graphics.drawString(line2, x, y + 16);
		if (line3 != null)
		{
			graphics.drawString(line3, x, y + 32);
		}
		if (line4 != null)
		{
			graphics.drawString(line4, x, y + 48);
		}
	}

	private void drawTargets(Graphics2D graphics, OverlayDebugState state, TelemetryDebugOverlayMode mode)
	{
		List<OverlayTarget> targets = drawableTargets(state);
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
		if (!Boolean.TRUE.equals(target.onScreen) && mode != TelemetryDebugOverlayMode.ALL && !hasProjectionIdentity(target))
		{
			return false;
		}
		if (mode == TelemetryDebugOverlayMode.REACHABILITY && target.directReachability == null && target.reachability == null)
		{
			return false;
		}
		if ("none".equals(drawableGeometrySource(target)))
		{
			return false;
		}
		return true;
	}

	private void drawTargetShape(Graphics2D graphics, OverlayTarget target, Color color)
	{
		TelemetryDebugOverlayGeometryMode geometryMode = config.telemetryDebugOverlayGeometryMode();
		float strokeWidth = Boolean.TRUE.equals(target.isBest) || Boolean.TRUE.equals(target.selected) || "selected_target".equals(target.markerType) ? 3.0f : 2.0f;
		graphics.setStroke(new BasicStroke(strokeWidth));
		LiveProjection liveProjection = liveProjectionFor(target);
		boolean liveShapeDrawn = false;
		boolean storedGeometryDrawn = false;
		boolean livePointDrawn = false;

		if (liveProjection.shape != null)
		{
			drawShape(graphics, liveProjection.shape, color, true, strokeWidth);
			liveShapeDrawn = true;
		}

		if (!liveShapeDrawn && geometryMode == TelemetryDebugOverlayGeometryMode.ALL_GEOMETRY_DEBUG)
		{
			if (config.telemetryDebugOverlayShowClickableHull())
			{
				storedGeometryDrawn |= drawPolygon(graphics, firstPolygon(target.clickableHull, target.clickboxPolygon), color, true, strokeWidth);
			}
			storedGeometryDrawn |= drawPolygon(graphics, target.convexHull, color, false, 1.5f);
			if (config.telemetryDebugOverlayShowCanvasTilePolygon())
			{
				storedGeometryDrawn |= drawPolygon(graphics, target.canvasTilePolygon, color, false, 1.0f);
			}
		}
		else if (!liveShapeDrawn)
		{
			storedGeometryDrawn = drawPolygon(graphics, primaryPolygon(target, geometryMode), color, true, strokeWidth);
		}

		if (config.telemetryDebugOverlayShowCanvasTilePolygon()
				&& geometryMode == TelemetryDebugOverlayGeometryMode.TILE_POLYGON
				&& !hasPolygon(primaryPolygon(target, geometryMode)))
		{
			storedGeometryDrawn |= drawPolygon(graphics, target.canvasTilePolygon, color, false, 1.5f);
		}

		if (!liveShapeDrawn && !storedGeometryDrawn && shouldDrawBounds(target, geometryMode))
		{
			graphics.setColor(color);
			graphics.setStroke(new BasicStroke(strokeWidth));
			graphics.draw(new Rectangle(
					round(target.bounds.x),
					round(target.bounds.y),
					Math.max(1, round(target.bounds.width)),
					Math.max(1, round(target.bounds.height))));
			storedGeometryDrawn = true;
		}
		if (config.telemetryDebugOverlayShowAimPoints() && shouldPreferLiveProjectionPoint(target, liveProjection))
		{
			if (!(isPathTileMarker(target) && liveProjection.shape != null))
			{
				drawAimPoint(graphics, liveProjection.point.getX(), liveProjection.point.getY());
				livePointDrawn = true;
			}
		}
		if (config.telemetryDebugOverlayShowAimPoints()
				&& !livePointDrawn
				&& target.aimPoint != null
				&& !(isPathTileMarker(target) && liveShapeDrawn))
		{
			drawAimPoint(graphics, round(target.aimPoint.canvasX), round(target.aimPoint.canvasY));
		}
	}

	private void drawAimPoint(Graphics2D graphics, int x, int y)
	{
		graphics.drawLine(x - 5, y, x + 5, y);
		graphics.drawLine(x, y - 5, x, y + 5);
		graphics.fillOval(x - 2, y - 2, 4, 4);
	}

	private boolean shouldDrawBounds(OverlayTarget target, TelemetryDebugOverlayGeometryMode geometryMode)
	{
		if (!config.telemetryDebugOverlayShowBounds() || target.bounds == null)
		{
			return false;
		}
		return geometryMode == TelemetryDebugOverlayGeometryMode.BOUNDS
				|| geometryMode == TelemetryDebugOverlayGeometryMode.HULL_AND_BOUNDS
				|| geometryMode == TelemetryDebugOverlayGeometryMode.ALL_GEOMETRY_DEBUG
				|| !hasPolygon(primaryPolygon(target, geometryMode));
	}

	private List<List<Double>> primaryPolygon(OverlayTarget target, TelemetryDebugOverlayGeometryMode geometryMode)
	{
		if (target == null || geometryMode == TelemetryDebugOverlayGeometryMode.AIM_ONLY || geometryMode == TelemetryDebugOverlayGeometryMode.BOUNDS)
		{
			return null;
		}
		if (geometryMode == TelemetryDebugOverlayGeometryMode.TILE_POLYGON)
		{
			if (config.telemetryDebugOverlayShowCanvasTilePolygon() && hasPolygon(target.canvasTilePolygon))
			{
				return polygonPoints(target.canvasTilePolygon);
			}
			return firstPolygon(target.clickableHull, target.clickboxPolygon, target.convexHull);
		}
		if (!config.telemetryDebugOverlayShowClickableHull())
		{
			return null;
		}
		return firstPolygon(target.clickableHull, target.clickboxPolygon, target.convexHull, target.canvasTilePolygon);
	}

	@SafeVarargs
	private static List<List<Double>> firstPolygon(Object... polygons)
	{
		if (polygons == null)
		{
			return null;
		}
		for (Object polygon : polygons)
		{
			List<List<Double>> normalized = polygonPoints(polygon);
			if (hasPolygon(normalized))
			{
				return normalized;
			}
		}
		return null;
	}

	static String fallbackGeometrySource(OverlayTarget target)
	{
		if (target == null)
		{
			return "none";
		}
		if (hasPolygon(target.clickableHull))
		{
			return "clickableHull";
		}
		if (hasPolygon(target.clickboxPolygon))
		{
			return "clickboxPolygon";
		}
		if (hasPolygon(target.convexHull))
		{
			return "convexHull";
		}
		if (hasPolygon(target.canvasTilePolygon))
		{
			return "canvasTilePolygon";
		}
		if (target.bounds != null)
		{
			return "bounds";
		}
		if (target.aimPoint != null)
		{
			return "aimPoint";
		}
		return "none";
	}

	static String drawableGeometrySource(OverlayTarget target)
	{
		String fallback = fallbackGeometrySource(target);
		if (!"none".equals(fallback))
		{
			return fallback;
		}
		if (hasProjectionIdentity(target))
		{
			if (isPathTileMarker(target))
			{
				return "live_tile_polygon";
			}
			return "live_tile_fallback";
		}
		return "none";
	}

	static boolean hasProjectionIdentity(OverlayTarget target)
	{
		if (target == null)
		{
			return false;
		}
		boolean hasWorld = target.worldX != null && target.worldY != null && target.plane != null;
		boolean hasScene = target.sceneX != null && target.sceneY != null && target.plane != null;
		boolean hasLocal = target.localX != null && target.localY != null && target.plane != null;
		return hasWorld || hasScene || hasLocal;
	}

	private static boolean hasPolygon(Object value)
	{
		return hasPolygon(polygonPoints(value));
	}

	private static boolean hasPolygon(List<List<Double>> points)
	{
		if (points == null || points.size() < 3)
		{
			return false;
		}
		for (List<Double> point : points)
		{
			if (point == null || point.size() < 2 || point.get(0) == null || point.get(1) == null)
			{
				return false;
			}
		}
		return true;
	}

	@SuppressWarnings("unchecked")
	private static List<List<Double>> polygonPoints(Object value)
	{
		if (value instanceof Map)
		{
			Map<?, ?> map = (Map<?, ?>) value;
			Object points = map.get("points");
			if (points != null)
			{
				return polygonPoints(points);
			}
			Object xs = map.get("x");
			Object ys = map.get("y");
			Object n = map.get("n");
			if (xs instanceof List && ys instanceof List)
			{
				List<?> xList = (List<?>) xs;
				List<?> yList = (List<?>) ys;
				int count = Math.min(xList.size(), yList.size());
				if (n instanceof Number)
				{
					count = Math.min(count, ((Number) n).intValue());
				}
				List<List<Double>> pointList = new ArrayList<>();
				for (int index = 0; index < count; index++)
				{
					Double x = numberValue(xList.get(index));
					Double y = numberValue(yList.get(index));
					if (x == null || y == null)
					{
						return null;
					}
					pointList.add(List.of(x, y));
				}
				return pointList;
			}
			Double x = numberValue(map.get("x"));
			Double y = numberValue(map.get("y"));
			if (x != null && y != null)
			{
				return List.of(List.of(x, y));
			}
			return null;
		}

		if (!(value instanceof List))
		{
			return null;
		}

		List<?> raw = (List<?>) value;
		List<List<Double>> points = new ArrayList<>();
		for (Object item : raw)
		{
			if (item instanceof Map)
			{
				Map<?, ?> map = (Map<?, ?>) item;
				Double x = numberValue(map.get("x"));
				Double y = numberValue(map.get("y"));
				if (x == null || y == null)
				{
					return null;
				}
				points.add(List.of(x, y));
			}
			else if (item instanceof List)
			{
				List<?> pair = (List<?>) item;
				if (pair.size() < 2)
				{
					return null;
				}
				Double x = numberValue(pair.get(0));
				Double y = numberValue(pair.get(1));
				if (x == null || y == null)
				{
					return null;
				}
				points.add(List.of(x, y));
			}
			else
			{
				return null;
			}
		}
		return points;
	}

	private static Double numberValue(Object value)
	{
		return value instanceof Number ? ((Number) value).doubleValue() : null;
	}

	private boolean drawPolygon(Graphics2D graphics, List<List<Double>> points, Color color, boolean fill, float strokeWidth)
	{
		if (!hasPolygon(points))
		{
			return false;
		}
		Polygon polygon = new Polygon();
		for (List<Double> point : points)
		{
			polygon.addPoint(round(point.get(0)), round(point.get(1)));
		}
		if (fill)
		{
			graphics.setColor(withAlpha(color, 42));
			graphics.fillPolygon(polygon);
		}
		graphics.setStroke(new BasicStroke(strokeWidth));
		graphics.setColor(color);
		graphics.drawPolygon(polygon);
		return true;
	}

	private boolean drawPolygon(Graphics2D graphics, Object points, Color color, boolean fill, float strokeWidth)
	{
		return drawPolygon(graphics, polygonPoints(points), color, fill, strokeWidth);
	}

	private boolean drawShape(Graphics2D graphics, Shape shape, Color color, boolean fill, float strokeWidth)
	{
		if (shape == null)
		{
			return false;
		}
		if (fill)
		{
			graphics.setColor(withAlpha(color, 42));
			graphics.fill(shape);
		}
		graphics.setStroke(new BasicStroke(strokeWidth));
		graphics.setColor(color);
		graphics.draw(shape);
		return true;
	}

	private Color withAlpha(Color color, int alpha)
	{
		return new Color(color.getRed(), color.getGreen(), color.getBlue(), Math.max(0, Math.min(255, alpha)));
	}

	private void drawTargetLabel(Graphics2D graphics, OverlayTarget target, Color color)
	{
		int x = 0;
		int y = 0;
		LiveProjection liveProjection = liveProjectionFor(target);
		if (shouldPreferLiveProjectionPoint(target, liveProjection))
		{
			x = liveProjection.point.getX() + 7;
			y = liveProjection.point.getY() - 7;
		}
		else if (target.aimPoint != null)
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

	private boolean shouldPreferLiveProjectionPoint(OverlayTarget target, LiveProjection liveProjection)
	{
		if (liveProjection == null || liveProjection.point == null)
		{
			return false;
		}
		if (liveProjection.shape != null)
		{
			return true;
		}
		if (liveProjection.projectionMode != null && liveProjection.projectionMode.startsWith("live_object"))
		{
			return true;
		}
		return "none".equals(fallbackGeometrySource(target)) && target.aimPoint == null && target.bounds == null;
	}

	private LiveProjection liveProjectionFor(OverlayTarget target)
	{
		LiveProjection objectProjection = liveObjectProjectionFor(target);
		if (objectProjection != null)
		{
			return objectProjection;
		}
		LiveProjection tileProjection = liveTilePolygonProjectionFor(target);
		if (tileProjection != null)
		{
			return tileProjection;
		}
		Point tilePoint = liveTilePointForTarget(target);
		if (tilePoint != null)
		{
			return new LiveProjection(tilePoint, null, "live_tile_fallback");
		}
		return new LiveProjection(null, null, target != null && target.aimPoint != null ? "last_known_aim" : "label_only");
	}

	private LiveProjection liveObjectProjectionFor(OverlayTarget target)
	{
		TileObject object = resolveTileObject(target);
		if (object == null)
		{
			return null;
		}

		try
		{
			Shape clickbox = object.getClickbox();
			if (clickbox != null)
			{
				return new LiveProjection(centerOf(clickbox), clickbox, "live_object_clickbox");
			}
		}
		catch (RuntimeException ignored)
		{
			// Fall through to lighter geometry and stale-marker fallback.
		}

		try
		{
			Shape convexHull = tileObjectConvexHull(object);
			if (convexHull != null)
			{
				return new LiveProjection(centerOf(convexHull), convexHull, "live_object_geometry");
			}
		}
		catch (RuntimeException ignored)
		{
			// Fall through to object canvas location.
		}

		try
		{
			Point canvasLocation = object.getCanvasLocation();
			if (canvasLocation != null)
			{
				return new LiveProjection(canvasLocation, null, "live_object_bounds");
			}
		}
		catch (RuntimeException ignored)
		{
			// Fall through to tile projection.
		}

		return null;
	}

	private Point centerOf(Shape shape)
	{
		if (shape == null)
		{
			return null;
		}
		Rectangle bounds = shape.getBounds();
		return new Point(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
	}

	private TileObject resolveTileObject(OverlayTarget target)
	{
		if (target == null || client == null || !"sceneObject".equals(target.targetType))
		{
			return null;
		}
		Tile tile = tileForTarget(target);
		if (tile == null)
		{
			return null;
		}

		TileObject exact = firstMatchingObject(target, tile);
		if (exact != null)
		{
			return exact;
		}

		return null;
	}

	private TileObject firstMatchingObject(OverlayTarget target, Tile tile)
	{
		TileObject wallObject = tile.getWallObject();
		if (matchesTileObject(target, wallObject, "WALL_OBJECT"))
		{
			return wallObject;
		}
		TileObject groundObject = tile.getGroundObject();
		if (matchesTileObject(target, groundObject, "GROUND_OBJECT"))
		{
			return groundObject;
		}
		TileObject decorativeObject = tile.getDecorativeObject();
		if (matchesTileObject(target, decorativeObject, "DECORATIVE_OBJECT"))
		{
			return decorativeObject;
		}
		GameObject[] gameObjects = tile.getGameObjects();
		if (gameObjects == null)
		{
			return null;
		}
		for (GameObject gameObject : gameObjects)
		{
			if (matchesTileObject(target, gameObject, "GAME_OBJECT"))
			{
				return gameObject;
			}
		}
		return null;
	}

	private boolean matchesTileObject(OverlayTarget target, TileObject object, String kind)
	{
		if (object == null)
		{
			return false;
		}
		String key = sceneObjectKey(kind, object, orientationFor(kind, object));
		if (target.objectKey != null && target.objectKey.equals(key))
		{
			return true;
		}
		if (target.id != null && object.getId() != round(target.id))
		{
			return false;
		}
		Long objectHash = objectHash(object);
		if (target.hash != null && objectHash != null && objectHash.longValue() != Math.round(target.hash))
		{
			return false;
		}
		if (target.id != null || target.hash != null)
		{
			return true;
		}
		return target.objectKey == null || target.objectKey.isBlank();
	}

	private Tile tileForTarget(OverlayTarget target)
	{
		try
		{
			Scene scene = client.getScene();
			if (scene == null || scene.getTiles() == null)
			{
				return null;
			}
			Tile[][][] tiles = scene.getTiles();
			int plane = target.plane == null ? client.getPlane() : round(target.plane);
			int sceneX;
			int sceneY;
			if (target.sceneX != null && target.sceneY != null)
			{
				sceneX = round(target.sceneX);
				sceneY = round(target.sceneY);
			}
			else
			{
				LocalPoint localPoint = localPointForTarget(target);
				if (localPoint == null)
				{
					return null;
				}
				sceneX = localPoint.getSceneX();
				sceneY = localPoint.getSceneY();
			}
			if (plane < 0 || plane >= tiles.length || tiles[plane] == null || sceneX < 0 || sceneX >= tiles[plane].length)
			{
				return null;
			}
			Tile[] column = tiles[plane][sceneX];
			if (column == null || sceneY < 0 || sceneY >= column.length)
			{
				return null;
			}
			return column[sceneY];
		}
		catch (RuntimeException ignored)
		{
			return null;
		}
	}

	private LiveProjection liveTilePolygonProjectionFor(OverlayTarget target)
	{
		if (!isPathTileMarker(target) || client == null)
		{
			return null;
		}
		try
		{
			LocalPoint localPoint = localPointForTarget(target);
			if (localPoint == null)
			{
				return null;
			}
			int plane = target.plane == null ? client.getPlane() : round(target.plane);
			if (plane != client.getPlane())
			{
				return null;
			}
			Polygon tilePolygon = Perspective.getCanvasTilePoly(client, localPoint);
			if (tilePolygon == null)
			{
				return null;
			}
			return new LiveProjection(centerOf(tilePolygon), tilePolygon, "live_tile_polygon");
		}
		catch (RuntimeException ignored)
		{
			return null;
		}
	}

	private Point liveTilePointForTarget(OverlayTarget target)
	{
		if (target == null || client == null)
		{
			return null;
		}
		try
		{
			LocalPoint localPoint = localPointForTarget(target);
			if (localPoint == null)
			{
				return null;
			}
			int plane = target.plane == null ? client.getPlane() : round(target.plane);
			return Perspective.localToCanvas(client, localPoint, plane);
		}
		catch (RuntimeException ignored)
		{
			return null;
		}
	}

	private LocalPoint localPointForTarget(OverlayTarget target)
	{
		if (target == null || client == null)
		{
			return null;
		}
		if (target.worldX != null && target.worldY != null)
		{
			return LocalPoint.fromWorld(client, round(target.worldX), round(target.worldY));
		}
		if (target.localX != null && target.localY != null)
		{
			return new LocalPoint(round(target.localX), round(target.localY));
		}
		return null;
	}

	private Shape tileObjectConvexHull(TileObject object)
	{
		if (object instanceof GameObject)
		{
			return ((GameObject) object).getConvexHull();
		}
		if (object instanceof WallObject)
		{
			return ((WallObject) object).getConvexHull();
		}
		if (object instanceof DecorativeObject)
		{
			return ((DecorativeObject) object).getConvexHull();
		}
		if (object instanceof GroundObject)
		{
			return ((GroundObject) object).getConvexHull();
		}
		return null;
	}

	private int orientationFor(String kind, TileObject object)
	{
		if (object instanceof GameObject)
		{
			return ((GameObject) object).getOrientation();
		}
		if (object instanceof WallObject)
		{
			return ((WallObject) object).getOrientationA();
		}
		return -1;
	}

	private Long objectHash(TileObject object)
	{
		try
		{
			return object == null ? null : object.getHash();
		}
		catch (RuntimeException ignored)
		{
			return null;
		}
	}

	private Point worldLocationToSceneLocation(TileObject object)
	{
		WorldPoint worldLocation = object.getWorldLocation();
		WorldView worldView = object.getWorldView();
		if (worldLocation == null || worldView == null)
		{
			return null;
		}
		return new Point(worldLocation.getX() - worldView.getBaseX(), worldLocation.getY() - worldView.getBaseY());
	}

	private String sceneObjectKey(String kind, TileObject object, int orientation)
	{
		if (object == null)
		{
			return kind + ":missing";
		}
		WorldPoint worldLocation = object.getWorldLocation();
		Point sceneLocation = worldLocationToSceneLocation(object);
		Long hash = objectHash(object);
		int plane = worldLocation == null ? object.getPlane() : worldLocation.getPlane();
		int worldX = worldLocation == null ? -1 : worldLocation.getX();
		int worldY = worldLocation == null ? -1 : worldLocation.getY();
		int sceneX = sceneLocation == null ? -1 : sceneLocation.getX();
		int sceneY = sceneLocation == null ? -1 : sceneLocation.getY();
		return plane + ":" + worldX + ":" + worldY + ":" + sceneX + ":" + sceneY + ":" + kind + ":" + object.getId() + ":" + (hash == null ? "nohash" : hash) + ":" + orientation;
	}

	static boolean isPathTileMarker(OverlayTarget target)
	{
		if (target == null)
		{
			return false;
		}
		if (isPathTileMarkerType(target.markerType))
		{
			return true;
		}
		return "waypoint".equals(target.markerType)
				&& target.markerId != null
				&& target.markerId.startsWith("next_waypoint_tile:");
	}

	static boolean isPathTileMarkerType(String markerType)
	{
		return "destination_tile".equals(markerType)
				|| "final_approach_tile".equals(markerType)
				|| "next_waypoint_tile".equals(markerType)
				|| "predicted_path_tile".equals(markerType)
				|| "path_blocked".equals(markerType)
				|| "path_unknown".equals(markerType);
	}

	private String formatLabel(OverlayTarget target)
	{
		if (target.overlayLabel != null && !target.overlayLabel.isBlank())
		{
			return target.overlayLabel;
		}
		if (target.label != null && !target.label.isBlank())
		{
			return target.label;
		}
		StringBuilder label = new StringBuilder();
		label.append(target.name == null ? valueOrUnknown(target.classId) : target.name);
		if (target.distanceTiles != null)
		{
			label.append(" d").append(trimNumber(target.distanceTiles));
		}
		String reachability = target.directReachability == null ? target.reachability : target.directReachability;
		if (config.telemetryDebugOverlayShowReachability() && reachability != null)
		{
			label.append(" ").append(reachabilityToken(reachability));
		}
		String liveState = target.targetLiveState == null ? target.liveness : target.targetLiveState;
		if (liveState != null)
		{
			label.append(" ").append(readableLiveState(liveState));
		}
		return label.toString();
	}

	private Color colorFor(OverlayTarget target)
	{
		if ("warning".equals(target.markerType))
		{
			return RED;
		}
		if (Boolean.TRUE.equals(target.selected) || "selected_target".equals(target.markerType))
		{
			return GREEN;
		}
		if ("backup_candidate".equals(target.markerType))
		{
			return YELLOW;
		}
		return colorFor(target.directReachability == null ? target.reachability : target.directReachability,
				target.targetLiveState == null ? target.liveness : target.targetLiveState);
	}

	static List<OverlayTarget> drawableTargets(OverlayDebugState state)
	{
		if (state == null)
		{
			return Collections.emptyList();
		}
		if (state.intentState != null && state.intentState.markers != null && !state.intentState.markers.isEmpty())
		{
			return state.intentState.markers;
		}
		if (state.markers != null && !state.markers.isEmpty())
		{
			return state.markers;
		}
		return state.targets == null ? Collections.emptyList() : state.targets;
	}

	static String statusLine(OverlayDebugState state, TelemetryDebugOverlayGeometryMode geometryMode)
	{
		if (state == null)
		{
			return "Waiting for overlay_debug_state.json";
		}
		List<OverlayTarget> intentMarkers = intentMarkers(state);
		boolean intentActive = !intentMarkers.isEmpty();
		int targetCount = state.summary == null ? (intentActive ? intentMarkers.size() : 0) : safeInt(state.summary.targetsWritten, intentActive ? intentMarkers.size() : 0);
		int hullCount = state.summary == null ? countClickableHullTargets(intentMarkers) : safeInt(state.summary.clickableHullTargets, countClickableHullTargets(intentMarkers));
		int hullLimit = state.summary == null ? 0 : safeInt(state.summary.hullLimit, 0);
		int geometryCap = state.summary == null ? 0 : safeInt(state.summary.compactLiveGeometryMaxRefs, 0);
		StringBuilder line = new StringBuilder();
		line.append("tick ").append(valueOrUnknown(state.latestTick))
				.append(" | ").append(valueOrUnknown(state.profile))
				.append(" | targets ").append(targetCount);
		if (intentActive)
		{
			line.append(" | selected ").append(boolToken(selectedMarkerCount(intentMarkers) > 0))
					.append(" | backups ").append(backupMarkerCount(intentMarkers))
					.append(" | hulls ").append(hullCount).append("/").append(targetCount)
					.append(" | legacy best ").append(boolToken(state.summary == null ? null : state.summary.bestHullAvailable))
					.append(" legacy nearest ").append(boolToken(state.summary == null ? null : state.summary.nearestHullAvailable));
		}
		else
		{
			line.append(" | hulls ").append(hullCount).append("/").append(targetCount)
					.append(" | best ").append(boolToken(state.summary == null ? null : state.summary.bestHullAvailable))
					.append(" nearest ").append(boolToken(state.summary == null ? null : state.summary.nearestHullAvailable));
		}
		line.append(" cap ").append(geometryCap).append("/").append(hullLimit)
				.append(" | ").append(geometryMode);
		return line.toString();
	}

	private static List<OverlayTarget> intentMarkers(OverlayDebugState state)
	{
		if (state == null || state.intentState == null || state.intentState.markers == null)
		{
			return Collections.emptyList();
		}
		return state.intentState.markers;
	}

	private static int selectedMarkerCount(List<OverlayTarget> markers)
	{
		int count = 0;
		for (OverlayTarget marker : markers)
		{
			if (marker != null && ("selected_target".equals(marker.markerType) || Boolean.TRUE.equals(marker.selected)))
			{
				count++;
			}
		}
		return count;
	}

	private static int backupMarkerCount(List<OverlayTarget> markers)
	{
		int count = 0;
		for (OverlayTarget marker : markers)
		{
			if (marker != null && "backup_candidate".equals(marker.markerType))
			{
				count++;
			}
		}
		return count;
	}

	private static int countClickableHullTargets(List<OverlayTarget> markers)
	{
		int count = 0;
		for (OverlayTarget marker : markers)
		{
			if (marker != null && (hasPolygon(marker.clickableHull) || hasPolygon(marker.clickboxPolygon)))
			{
				count++;
			}
		}
		return count;
	}

	static Color colorFor(String reachability, String live)
	{
		String liveState = live == null ? "" : live;
		String reachabilityState = reachability == null ? "" : reachability;
		if ("depleted_or_stump".equals(liveState) || "recently_despawned".equals(liveState) || "stale".equals(liveState))
		{
			return GRAY;
		}
		if ("blocked".equals(reachabilityState))
		{
			return RED;
		}
		if ("reachable".equals(reachabilityState))
		{
			return GREEN;
		}
		if ("unknown".equals(reachabilityState) || "live_assumed".equals(liveState) || "unknown".equals(liveState))
		{
			return YELLOW;
		}
		return GREEN;
	}

	static String reachabilityToken(String value)
	{
		if ("reachable".equals(value))
		{
			return "R";
		}
		if ("blocked".equals(value))
		{
			return "BLOCK";
		}
		if ("unknown".equals(value))
		{
			return "?";
		}
		return value;
	}

	static String readableLiveState(String value)
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

	private static int safeInt(Double value, int fallback)
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

	private static String valueOrUnknown(Object value)
	{
		return value == null ? "unknown" : String.valueOf(value);
	}

	private static String boolToken(Boolean value)
	{
		if (value == null)
		{
			return "?";
		}
		return value ? "yes" : "no";
	}

	private String truncate(String value, int maxLength)
	{
		if (value == null || value.length() <= maxLength)
		{
			return value;
		}
		return value.substring(0, Math.max(0, maxLength - 3)) + "...";
	}

	static class OverlayDebugState
	{
		String schema;
		Double latestTick;
		String profile;
		String latestEventSummary;
		Double latestEventTick;
		Double warningEventCount;
		Double lastEventTick;
		OverlaySummary summary;
		OverlayIntentState intentState;
		List<OverlayTarget> markers;
		List<OverlayTarget> targets;
		CollisionWindow collisionWindow;
	}

	static class OverlayIntentState
	{
		String schema;
		String activeTask;
		String activeIntent;
		String status;
		List<OverlayTarget> markers;
	}

	static class OverlaySummary
	{
		Double candidateCount;
		Double targetsWritten;
		Boolean budgetExceeded;
		Double writeFailures;
		String latestEventSummary;
		Double latestEventTick;
		Double warningEventCount;
		Double lastEventTick;
		Double hullLimit;
		Boolean bestHullAvailable;
		Boolean nearestHullAvailable;
		Double compactLiveGeometryMaxRefs;
		Double clickableHullTargets;
		Double clickboxPolygonTargets;
		Double convexHullTargets;
		Double canvasTilePolygonTargets;
		Double boundsOnlyTargets;
		Double aimOnlyTargets;
	}

	static class OverlayTarget
	{
		String markerId;
		String markerVersion;
		String targetKey;
		String classId;
		String markerType;
		String label;
		String reason;
		String source;
		String targetType;
		Boolean selected;
		String role;
		Double priority;
		String name;
		Double id;
		Double hash;
		String objectKey;
		String kind;
		String layer;
		Double worldX;
		Double worldY;
		Double plane;
		Double sceneX;
		Double sceneY;
		Double localX;
		Double localY;
		Double tick;
		Double distanceTiles;
		Boolean onScreen;
		Boolean geometryAvailable;
		String qualityTier;
		Double qualityScore;
		String targetLiveState;
		String liveness;
		String livenessInterpretation;
		String directReachability;
		String reachability;
		Double reachabilityConfidence;
		Boolean targetInCollisionWindow;
		Double pathLengthTiles;
		Boolean isBest;
		String overlayLabel;
		String overlayColor;
		String geometrySource;
		String projectionMode;
		Boolean projectionStale;
		String projectionFallbackReason;
		Boolean clickableHullAvailable;
		String clickableHullMissingReason;
		LabelParts labelParts;
		AimPoint aimPoint;
		Bounds bounds;
		Object clickableHull;
		Object clickboxPolygon;
		Object convexHull;
		Object canvasTilePolygon;
	}

	private static class LiveProjection
	{
		private final Point point;
		private final Shape shape;
		private final String projectionMode;

		private LiveProjection(Point point, Shape shape, String projectionMode)
		{
			this.point = point;
			this.shape = shape;
			this.projectionMode = projectionMode;
		}
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

	static class LabelParts
	{
		Double distance;
		String reachability;
		String liveness;
		String livenessInterpretation;
		String quality;
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
