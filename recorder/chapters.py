"""Chapters derived from the metadata timeline.

A chapter starts at offset 0 and at every category change, so a stream that
goes from ``Music`` (singing) to ``VALORANT`` gets two chapters. The same
timeline is in the ``*.events.jsonl`` sidecar, so chapters can be rebuilt
after a crash from that file alone.

The ``chapters.json`` layout follows ACC-76; the MKV gets the same chapters
through an FFMETADATA1 file at finalize time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class TimelinePoint:
    offset_ms: int
    category_id: str
    category_name: str
    title: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


def build_chapters(timeline: Iterable[TimelinePoint], duration_ms: int) -> list[dict[str, Any]]:
    """One chapter per run of the same category, in offset order."""
    starts: list[TimelinePoint] = []
    for point in sorted(timeline, key=lambda p: p.offset_ms):
        if starts and (point.category_id, point.category_name) == (starts[-1].category_id, starts[-1].category_name):
            continue
        starts.append(point)
    chapters = []
    for i, point in enumerate(starts):
        start = max(0, point.offset_ms)
        end = starts[i + 1].offset_ms if i + 1 < len(starts) else max(duration_ms, start)
        chapters.append({
            "index": i,
            "start_ms": start,
            "end_ms": end,
            "title": point.category_name or "Unknown",
            "category_id": point.category_id,
            "stream_title": point.title,
            "tags": list(point.tags),
        })
    return chapters


def chapters_document(stream_id: str, recorded_at: str, chapters: list[dict[str, Any]]) -> dict[str, Any]:
    return {"stream_id": stream_id, "recorded_at": recorded_at, "chapters": chapters}


def _ffmeta_escape(value: str) -> str:
    out = []
    for c in value:
        if c in "=;#\\\n":
            out.append("\\" + c)
        else:
            out.append(c)
    return "".join(out)


def to_ffmetadata(chapters: list[dict[str, Any]]) -> str:
    lines = [";FFMETADATA1"]
    for ch in chapters:
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={ch['start_ms']}",
            f"END={ch['end_ms']}",
            f"title={_ffmeta_escape(ch['title'])}",
        ]
    return "\n".join(lines) + "\n"


def _point_from_event(event: dict[str, Any]) -> TimelinePoint:
    return TimelinePoint(
        offset_ms=int(event.get("recording.offset_ms") or 0),
        category_id=str(event.get("category.id") or ""),
        category_name=str(event.get("category.name") or ""),
        title=str(event.get("stream.title") or ""),
        tags=tuple(event.get("stream.tags") or ()),
    )


def read_events_file(path: str) -> tuple[list[TimelinePoint], dict[str, Any]]:
    """Timeline and the ``recording.started`` event from an events sidecar.

    Lines that are not valid JSON (e.g. a write cut short by a crash) are skipped.
    """
    timeline: list[TimelinePoint] = []
    started: dict[str, Any] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            name = event.get("event")
            if name == "recording.started":
                started = event
                timeline.append(_point_from_event(event))
            elif name == "stream.metadata_changed":
                timeline.append(_point_from_event(event))
    return timeline, started
