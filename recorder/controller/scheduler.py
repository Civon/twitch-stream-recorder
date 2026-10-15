"""Which live channels get a recording Job now, and which wait for a slot.

``max_concurrent`` caps recordings running at once so the WAN link is not
saturated (ACC-18). Higher ``priority`` channels get free slots first; a
running recording is never stopped to make room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from recorder.controller import LABEL_CHANNEL, LABEL_STREAM_ID
from recorder.controller.jobs import ChannelSpec, dns_label, job_name
from recorder.metadata import StreamMetadata


@dataclass(frozen=True)
class JobInfo:
    name: str
    channel: str
    stream_id: str
    finished: bool
    failed: bool

    @classmethod
    def from_job(cls, obj: dict[str, Any]) -> "JobInfo":
        meta, status = obj.get("metadata", {}), obj.get("status") or {}
        labels = meta.get("labels") or {}
        conditions = {c.get("type"): c.get("status") for c in status.get("conditions") or []}
        failed = conditions.get("Failed") == "True"
        finished = failed or conditions.get("Complete") == "True"
        return cls(meta.get("name", ""), labels.get(LABEL_CHANNEL, ""), labels.get(LABEL_STREAM_ID, ""),
                   finished, failed)


@dataclass
class Plan:
    create: list[tuple[ChannelSpec, StreamMetadata]] = field(default_factory=list)
    deferred: list[ChannelSpec] = field(default_factory=list)
    running: int = 0


def plan(channels: Iterable[ChannelSpec], live: dict[str, StreamMetadata], jobs: Iterable[JobInfo],
         max_concurrent: int = 0) -> Plan:
    jobs = list(jobs)
    existing = {j.name for j in jobs}
    running = sum(1 for j in jobs if not j.finished)
    candidates = []
    for ch in channels:
        stream = live.get(ch.login)
        if ch.paused or stream is None or job_name(ch.login, stream.stream_id) in existing:
            continue
        candidates.append((ch, stream))
    candidates.sort(key=lambda c: (-c[0].priority, c[0].login))

    slots = len(candidates) if max_concurrent <= 0 else max(0, max_concurrent - running)
    return Plan(create=candidates[:slots], deferred=[ch for ch, _ in candidates[slots:]], running=running)


def jobs_for(channel: ChannelSpec, jobs: Iterable[JobInfo]) -> list[JobInfo]:
    label = dns_label(channel.login)
    return [j for j in jobs if j.channel == label]
