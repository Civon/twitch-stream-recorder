"""Stream metadata snapshots and change detection.

A broadcast keeps its stream id while the streamer changes what it is about:
the category (e.g. ``Music`` for a singing segment, then ``VALORANT``), the
title, the tags, the language, or the mature flag. ``diff`` compares two
snapshots of the same broadcast and reports which of those changed, so the
recorder can emit a ``stream.metadata_changed`` event and cut a chapter there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TRACKED_FIELDS = ("category", "title", "tags", "language", "is_mature")


@dataclass(frozen=True)
class StreamMetadata:
    stream_id: str
    user_id: str
    user_login: str
    category_id: str = ""
    category_name: str = ""
    title: str = ""
    tags: tuple[str, ...] = ()
    language: str = ""
    is_mature: bool | None = None
    viewer_count: int | None = None
    started_at: str = ""

    @classmethod
    def from_helix(cls, data: dict[str, Any]) -> "StreamMetadata":
        """Build from one item of Helix ``GET /streams`` ``data``."""
        return cls(
            stream_id=str(data.get("id") or ""),
            user_id=str(data.get("user_id") or ""),
            user_login=str(data.get("user_login") or "").lower(),
            category_id=str(data.get("game_id") or ""),
            category_name=str(data.get("game_name") or ""),
            title=str(data.get("title") or ""),
            tags=tuple(data.get("tags") or ()),
            language=str(data.get("language") or ""),
            is_mature=data.get("is_mature"),
            viewer_count=data.get("viewer_count"),
            started_at=str(data.get("started_at") or ""),
        )

    def event_fields(self) -> dict[str, Any]:
        """The snapshot as flat wide-event fields."""
        return {
            "channel.login": self.user_login,
            "channel.id": self.user_id,
            "stream.id": self.stream_id,
            "stream.started_at": self.started_at or None,
            "category.id": self.category_id,
            "category.name": self.category_name,
            "stream.title": self.title,
            "stream.tags": list(self.tags),
            "stream.language": self.language,
            "stream.is_mature": self.is_mature,
            "stream.viewer_count": self.viewer_count,
        }


@dataclass(frozen=True)
class MetadataChange:
    fields: tuple[str, ...]
    before: StreamMetadata
    after: StreamMetadata

    def event_fields(self) -> dict[str, Any]:
        """Current snapshot plus a before/after pair for each changed field."""
        out = self.after.event_fields()
        out["change.fields"] = list(self.fields)
        if "category" in self.fields:
            out["category.before.id"] = self.before.category_id
            out["category.before.name"] = self.before.category_name
        if "title" in self.fields:
            out["stream.title.before"] = self.before.title
        if "tags" in self.fields:
            before, after = set(self.before.tags), set(self.after.tags)
            out["stream.tags.added"] = sorted(after - before)
            out["stream.tags.removed"] = sorted(before - after)
        if "language" in self.fields:
            out["stream.language.before"] = self.before.language
        if "is_mature" in self.fields:
            out["stream.is_mature.before"] = self.before.is_mature
        return out

    def summary(self) -> str:
        """Short human-readable text, e.g. for a Kubernetes Event message."""
        parts = []
        if "category" in self.fields:
            parts.append(f"category {self.before.category_name or '-'} -> {self.after.category_name or '-'}")
        if "title" in self.fields:
            parts.append(f"title {self.after.title!r}")
        if "tags" in self.fields:
            added = sorted(set(self.after.tags) - set(self.before.tags))
            removed = sorted(set(self.before.tags) - set(self.after.tags))
            parts.append(f"tags +{added} -{removed}")
        for name in ("language", "is_mature"):
            if name in self.fields:
                parts.append(f"{name} {getattr(self.before, name)} -> {getattr(self.after, name)}")
        return "; ".join(parts)


def _category_changed(before: StreamMetadata, after: StreamMetadata) -> bool:
    if before.category_id or after.category_id:
        return before.category_id != after.category_id
    return before.category_name != after.category_name


def diff(before: StreamMetadata, after: StreamMetadata) -> MetadataChange | None:
    """What changed between two snapshots, or None if nothing tracked did.

    Tags compare as a set: Twitch may reorder them without anyone editing.
    A different stream id is a new broadcast, not a metadata change; callers
    handle that case before calling this.
    """
    changed = []
    if _category_changed(before, after):
        changed.append("category")
    if before.title != after.title:
        changed.append("title")
    if set(before.tags) != set(after.tags):
        changed.append("tags")
    if before.language != after.language:
        changed.append("language")
    if before.is_mature != after.is_mature:
        changed.append("is_mature")
    if not changed:
        return None
    return MetadataChange(tuple(changed), before, after)
