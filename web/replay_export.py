"""Export completed task graph execution as PNG frames and an animated GIF."""

from __future__ import annotations

import math
import os
import re
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


COMPLETED_STATUS = "completed"
NODE_W = 210
NODE_H = 86
LEVEL_GAP = 150
NODE_GAP = 310
MARGIN_X = 80
MARGIN_Y = 92
FRAME_BG = "#f6f7fa"
PANEL_BG = "#ffffff"
LINE = "#d8dee8"
INK = "#182233"
MUTED = "#667386"
STATUS_COLORS = {
    "pending": "#8a95a3",
    "running": "#0f7b8d",
    "suspended": "#946200",
    "waiting_human": "#7a4aa0",
    "completed": "#208451",
    "failed": "#bf3b42",
    "cancelled": "#bf3b42",
    "terminated": "#bf3b42",
    "timed_out": "#bf3b42",
}


class ReplayExportError(ValueError):
    """Raised when a task cannot be exported as replay media."""


def export_task_replay(
    task: dict[str, Any],
    *,
    output_dir: str | None = None,
    frame_duration_ms: int = 900,
) -> dict[str, Any]:
    """Create replay PNG frames and an animated GIF for a completed task."""

    status = str(task.get("status") or "").lower()
    if status != COMPLETED_STATUS:
        raise ReplayExportError(
            f"Replay export is only available for completed tasks; current status is {status or 'unknown'}."
        )
    state = task.get("state") if isinstance(task.get("state"), dict) else {}
    nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
    if not nodes:
        raise ReplayExportError("Task has no graph state to replay.")

    destination = _output_dir(output_dir)
    task_id = str(task.get("task_id") or task.get("workflow_id") or state.get("graph_id") or "task")
    slug = _safe_slug(task_id)
    replay_dir = destination / f"{slug}-replay-{time.strftime('%Y%m%d-%H%M%S')}"
    frames_dir = replay_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    snapshots = build_replay_snapshots(task)
    images: list[Image.Image] = []
    frame_paths: list[str] = []
    for index, snapshot in enumerate(snapshots):
        image = render_replay_frame(snapshot)
        frame_path = frames_dir / f"frame_{index:03d}.png"
        image.save(frame_path)
        frame_paths.append(str(frame_path))
        images.append(image)

    gif_path = replay_dir / f"{slug}_replay.gif"
    duration = max(200, int(frame_duration_ms))
    images[0].save(
        gif_path,
        save_all=True,
        append_images=images[1:],
        duration=duration,
        loop=0,
        optimize=False,
    )
    for image in images:
        image.close()

    return {
        "ok": True,
        "task_id": task_id,
        "status": status,
        "output_dir": str(replay_dir),
        "gif_path": str(gif_path),
        "frame_count": len(snapshots),
        "frame_duration_ms": duration,
        "frames_dir": str(frames_dir),
        "frame_paths": frame_paths,
    }


def build_replay_snapshots(task: dict[str, Any]) -> list[dict[str, Any]]:
    state = deepcopy(task.get("state") or {})
    final_nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
    final_edges = state.get("edges") if isinstance(state.get("edges"), list) else []
    instances = _all_instances(state, final_nodes)
    events = _events_from_instances(instances)
    if not events:
        events = [{"kind": "final", "node_id": None, "instance": None, "order": 1, "timestamp": None}]

    node_by_id = {str(node.get("id")): deepcopy(node) for node in final_nodes if node.get("id")}
    runtime_nodes = {
        node_id: _pending_node(node)
        for node_id, node in node_by_id.items()
    }
    snapshots: list[dict[str, Any]] = [
        _snapshot_from_runtime(state, runtime_nodes, final_edges, "Initial pending graph", 0, len(events))
    ]

    completed_nodes: set[str] = set()
    started_nodes: set[str] = set()
    visit_counts: dict[str, int] = {node_id: 0 for node_id in runtime_nodes}
    for index, event in enumerate(events, start=1):
        node_id = event.get("node_id")
        instance = event.get("instance") or {}
        if node_id and node_id in runtime_nodes:
            started_nodes.add(node_id)
            visit_counts[node_id] = max(visit_counts.get(node_id, 0), int(instance.get("visit") or 1))
            node = runtime_nodes[node_id]
            node["visits"] = visit_counts[node_id]
            node["current_instance_id"] = instance.get("id") or node.get("current_instance_id")
            if event["kind"] == "start":
                node["status"] = _start_status(instance.get("kind"))
                node["summary"] = instance.get("summary") or f"Started {instance.get('kind') or 'node'}"
            else:
                final_node = node_by_id.get(node_id, {})
                node["status"] = str(instance.get("status") or final_node.get("status") or "completed")
                node["summary"] = instance.get("summary") or final_node.get("summary") or node["status"]
                if node["status"] == COMPLETED_STATUS:
                    completed_nodes.add(node_id)
                for key in (
                    "backend",
                    "agent_name",
                    "a2a_task_id",
                    "elapsed_seconds",
                    "heartbeat_count",
                    "max_visits",
                ):
                    if final_node.get(key) is not None:
                        node[key] = final_node.get(key)
        title = _event_title(event, index, len(events))
        snapshots.append(
            _snapshot_from_runtime(
                state,
                runtime_nodes,
                final_edges,
                title,
                index,
                len(events),
                started_nodes=started_nodes,
                completed_nodes=completed_nodes,
            )
        )

    final_runtime = {node_id: deepcopy(node) for node_id, node in node_by_id.items()}
    snapshots.append(
        _snapshot_from_runtime(
            state,
            final_runtime,
            final_edges,
            f"Final state: {task.get('status', 'completed')}",
            len(events) + 1,
            len(events) + 1,
            started_nodes=set(final_runtime),
            completed_nodes=set(final_runtime),
            final_frame=True,
        )
    )
    return _dedupe_snapshots(snapshots)


def render_replay_frame(snapshot: dict[str, Any]) -> Image.Image:
    state = snapshot["state"]
    layout = _compute_layout(state)
    width = max(1120, max((point[0] + NODE_W + MARGIN_X for point in layout.values()), default=1120))
    height = max(780, max((point[1] + NODE_H + MARGIN_Y for point in layout.values()), default=780))
    image = Image.new("RGB", (int(width), int(height)), FRAME_BG)
    draw = ImageDraw.Draw(image)
    fonts = _fonts()

    title = str(snapshot.get("title") or "Replay")
    subtitle = (
        f"{state.get('graph_name') or state.get('graph_id') or ''}  |  "
        f"frame {snapshot.get('index', 0)}/{snapshot.get('total', 0)}"
    )
    draw.text((MARGIN_X, 24), title, fill=INK, font=fonts["title"])
    draw.text((MARGIN_X, 55), subtitle, fill=MUTED, font=fonts["meta"])

    for edge in state.get("edges") or []:
        if edge.get("from") not in layout or edge.get("to") not in layout:
            continue
        _draw_edge(draw, edge, layout, fonts)
    for node in state.get("nodes") or []:
        point = layout.get(node.get("id"))
        if point:
            _draw_node(draw, node, point, fonts)
    return image


def _snapshot_from_runtime(
    base_state: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]],
    final_edges: list[dict[str, Any]],
    title: str,
    index: int,
    total: int,
    *,
    started_nodes: set[str] | None = None,
    completed_nodes: set[str] | None = None,
    final_frame: bool = False,
) -> dict[str, Any]:
    started_nodes = started_nodes or set()
    completed_nodes = completed_nodes or set()
    state = {
        "graph_id": base_state.get("graph_id"),
        "graph_name": base_state.get("graph_name"),
        "graph_type": base_state.get("graph_type", "control_flow"),
        "workflow_status": base_state.get("workflow_status"),
        "levels": deepcopy(base_state.get("levels") or []),
        "nodes": [deepcopy(node) for node in nodes_by_id.values()],
        "edges": [
            _edge_for_snapshot(edge, started_nodes, completed_nodes, final_frame=final_frame)
            for edge in final_edges
        ],
    }
    return {"title": title, "index": index, "total": total, "state": state}


def _edge_for_snapshot(
    edge: dict[str, Any],
    started_nodes: set[str],
    completed_nodes: set[str],
    *,
    final_frame: bool,
) -> dict[str, Any]:
    item = deepcopy(edge)
    if final_frame:
        return item
    from_node = str(edge.get("from"))
    to_node = str(edge.get("to"))
    final_taken = int(edge.get("taken_count") or 0)
    final_skipped = int(edge.get("skipped_count") or 0)
    item["taken_count"] = final_taken if to_node in started_nodes else 0
    item["skipped_count"] = final_skipped if from_node in completed_nodes and to_node not in started_nodes else 0
    return item


def _all_instances(state: dict[str, Any], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    instances = state.get("instances") if isinstance(state.get("instances"), list) else []
    if instances:
        return [item for item in instances if isinstance(item, dict)]
    flattened: list[dict[str, Any]] = []
    for node in nodes:
        for item in node.get("instances") or []:
            if isinstance(item, dict):
                flattened.append(item)
    return flattened


def _events_from_instances(instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for order, instance in enumerate(instances):
        node_id = instance.get("node_id")
        if not node_id:
            continue
        started = _parse_time(instance.get("started_at"))
        finished = _parse_time(instance.get("finished_at"))
        events.append({
            "kind": "start",
            "node_id": str(node_id),
            "instance": instance,
            "timestamp": started,
            "order": order * 2,
        })
        events.append({
            "kind": "finish",
            "node_id": str(node_id),
            "instance": instance,
            "timestamp": finished or started,
            "order": order * 2 + 1,
        })
    return sorted(events, key=lambda item: (_time_sort_key(item.get("timestamp")), item["order"]))


def _event_title(event: dict[str, Any], index: int, total: int) -> str:
    instance = event.get("instance") or {}
    node_id = event.get("node_id") or "-"
    if event.get("kind") == "start":
        return f"Step {index}/{total}: {node_id} started ({instance.get('kind') or 'node'})"
    return f"Step {index}/{total}: {node_id} {instance.get('status') or 'finished'}"


def _pending_node(node: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(node)
    item["status"] = "pending"
    item["visits"] = 0
    item["current_instance_id"] = None
    item["summary"] = ""
    item["elapsed_seconds"] = 0
    item["heartbeat_count"] = 0
    return item


def _start_status(kind: Any) -> str:
    if str(kind).lower() == "human":
        return "waiting_human"
    return "running"


def _compute_layout(state: dict[str, Any]) -> dict[str, tuple[int, int]]:
    levels = _visual_levels(state)
    widest = max((len(level) for level in levels), default=1)
    width = max(980, widest * NODE_GAP + MARGIN_X * 2)
    layout: dict[str, tuple[int, int]] = {}
    for level_index, level in enumerate(levels):
        row_width = (len(level) - 1) * NODE_GAP
        start_x = int((width - row_width) / 2 - NODE_W / 2)
        for index, node_id in enumerate(level):
            layout[str(node_id)] = (start_x + index * NODE_GAP, MARGIN_Y + level_index * LEVEL_GAP)
    return layout


def _visual_levels(state: dict[str, Any]) -> list[list[str]]:
    levels = state.get("levels")
    node_ids = [str(node.get("id")) for node in state.get("nodes") or [] if node.get("id")]
    if isinstance(levels, list) and levels:
        normalized = [
            [str(node_id) for node_id in level if str(node_id) in node_ids]
            for level in levels
            if isinstance(level, list)
        ]
        flattened = {node_id for level in normalized for node_id in level}
        missing = [node_id for node_id in node_ids if node_id not in flattened]
        if missing:
            normalized.append(missing)
        return [level for level in normalized if level]
    return [node_ids]


def _draw_edge(
    draw: ImageDraw.ImageDraw,
    edge: dict[str, Any],
    layout: dict[str, tuple[int, int]],
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    x1, y1 = layout[str(edge.get("from"))]
    x2, y2 = layout[str(edge.get("to"))]
    start = (x1 + NODE_W // 2, y1 + NODE_H)
    end = (x2 + NODE_W // 2, y2)
    taken = int(edge.get("taken_count") or 0) > 0
    skipped = int(edge.get("skipped_count") or 0) > 0 and not taken
    conditional = bool(edge.get("when"))
    color = "#208451" if taken else ("#c77700" if conditional else "#8b97a7")
    if skipped:
        color = "#b9c0cb"
    width = 4 if taken else 2
    if end[1] <= start[1]:
        rail_x = min(start[0], end[0]) - 80
        points = [start, (rail_x, start[1]), (rail_x, end[1]), end]
        draw.line(points, fill=color, width=width, joint="curve")
        label_pos = (rail_x - 18, int((start[1] + end[1]) / 2))
    else:
        mid_y = int((start[1] + end[1]) / 2)
        points = [start, (start[0], mid_y), (end[0], mid_y), end]
        draw.line(points, fill=color, width=width, joint="curve")
        label_pos = (int((start[0] + end[0]) / 2) + 10, mid_y - 18)
    _draw_arrow(draw, points[-2], points[-1], color)
    label = " | ".join(
        part
        for part in (
            str(edge.get("label") or edge.get("when") or ""),
            f"taken {edge.get('taken_count')}" if edge.get("taken_count") else "",
            f"skip {edge.get('skipped_count')}" if edge.get("skipped_count") else "",
        )
        if part
    )
    if label:
        draw.text(label_pos, _truncate(label, 38), fill=color, font=fonts["small"])


def _draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], color: str) -> None:
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    length = 12
    spread = 0.45
    points = [
        end,
        (
            int(end[0] - length * math.cos(angle - spread)),
            int(end[1] - length * math.sin(angle - spread)),
        ),
        (
            int(end[0] - length * math.cos(angle + spread)),
            int(end[1] - length * math.sin(angle + spread)),
        ),
    ]
    draw.polygon(points, fill=color)


def _draw_node(
    draw: ImageDraw.ImageDraw,
    node: dict[str, Any],
    point: tuple[int, int],
    fonts: dict[str, ImageFont.ImageFont],
) -> None:
    x, y = point
    status = str(node.get("status") or "pending")
    color = STATUS_COLORS.get(status, STATUS_COLORS["pending"])
    fill = "#ffffff"
    outline = color
    draw.rounded_rectangle((x, y, x + NODE_W, y + NODE_H), radius=12, fill=fill, outline=outline, width=3)
    draw.rounded_rectangle((x + 12, y + 10, x + 96, y + 34), radius=7, fill=color)
    draw.text((x + 22, y + 15), status.upper()[:11], fill="#ffffff", font=fonts["small_bold"])
    title = str(node.get("label") or node.get("id") or "")
    draw.text((x + 14, y + 42), _truncate(title, 22), fill=INK, font=fonts["node"])
    meta = (
        f"{node.get('backend') or '-'} | v {node.get('visits') or 0}/{node.get('max_visits') or 1} "
        f"| hb {node.get('heartbeat_count') or 0}"
    )
    draw.text((x + 14, y + 65), _truncate(meta, 30), fill=MUTED, font=fonts["small"])


def _fonts() -> dict[str, ImageFont.ImageFont]:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    def font(size: int) -> ImageFont.ImageFont:
        for path in candidates:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        return ImageFont.load_default()

    return {
        "title": font(24),
        "node": font(16),
        "meta": font(14),
        "small": font(12),
        "small_bold": font(12),
    }


def _dedupe_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        signature = repr([
            (node.get("id"), node.get("status"), node.get("visits"))
            for node in snapshot["state"].get("nodes") or []
        ]) + repr([
            (edge.get("from"), edge.get("to"), edge.get("taken_count"), edge.get("skipped_count"))
            for edge in snapshot["state"].get("edges") or []
        ])
        if signature in seen and deduped:
            continue
        seen.add(signature)
        deduped.append(snapshot)
    return deduped


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _time_sort_key(value: datetime | None) -> tuple[int, float]:
    if value is None:
        return (1, 0.0)
    return (0, value.timestamp())


def _output_dir(output_dir: str | None) -> Path:
    if output_dir and output_dir.strip():
        return Path(output_dir).expanduser().resolve()
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        return Path(user_profile) / "Desktop" / "MAOS-replays"
    return Path("exports").resolve() / "replays"


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return slug[:120] or "task"


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"
