"""The reconcile loop.

Each tick: list TwitchChannels, ask Helix which of them are live (one call per
100 channels), list our Jobs, create a Job for every live broadcast that has
none (within the concurrency cap), and write what it saw to each channel's
status.

Who reports what:

- The recording Job owns ``stream.*`` wide events, because only it knows the
  offset in the file where a category or title changed.
- The controller reflects the latest metadata into ``status`` and emits
  Kubernetes Events (``kubectl describe twitchchannel``), including
  ``MetadataChanged``, so people can see a change from Music to a game
  without a log query. Its own wide events are ``recording.scheduled``,
  ``recording.deferred`` and one ``controller.reconciled`` per tick.
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from typing import Any, Callable

from recorder.controller import COMPONENT, GROUP, KIND, LABEL_CHANNEL, LABEL_MANAGED_BY, PLURAL, VERSION
from recorder.controller.jobs import ChannelSpec, build_job
from recorder.controller.kube import ApiError, KubeClient
from recorder.controller.scheduler import JobInfo, jobs_for, plan
from recorder.events import EventEmitter
from recorder.metadata import StreamMetadata, diff
from recorder.twitch import HelixClient

log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _without_nulls(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def snapshot_from_status(login: str, status: dict[str, Any]) -> StreamMetadata | None:
    if not status.get("streamId"):
        return None
    return StreamMetadata(
        stream_id=status["streamId"], user_id="", user_login=login,
        category_id=status.get("categoryId") or "", category_name=status.get("categoryName") or "",
        title=status.get("title") or "", tags=tuple(status.get("tags") or ()),
        language=status.get("language") or "", is_mature=status.get("isMature"),
    )


class Reconciler:
    def __init__(self, kube: KubeClient, helix: HelixClient, namespace: str, job_template: dict[str, Any],
                 emitter: EventEmitter, max_concurrent: int = 0, now: Callable[[], str] = _utcnow):
        self.kube = kube
        self.helix = helix
        self.namespace = namespace
        self.job_template = job_template
        self.emitter = emitter
        self.max_concurrent = max_concurrent
        self.now = now

    def _k8s_event(self, resource: dict[str, Any], reason: str, message: str, type_: str = "Normal") -> None:
        meta = resource["metadata"]
        now = self.now()
        body = {
            "apiVersion": "v1",
            "kind": "Event",
            "metadata": {"generateName": f"{meta['name']}.", "namespace": self.namespace},
            "involvedObject": {"apiVersion": f"{GROUP}/{VERSION}", "kind": KIND, "name": meta["name"],
                               "namespace": self.namespace, "uid": meta.get("uid", "")},
            "reason": reason,
            "message": message[:1024],
            "type": type_,
            "source": {"component": COMPONENT},
            "reportingComponent": COMPONENT,
            "reportingInstance": os.environ.get("POD_NAME", COMPONENT),
            "firstTimestamp": now,
            "lastTimestamp": now,
            "count": 1,
        }
        try:
            self.kube.create_event(self.namespace, body)
        except (ApiError, OSError) as e:
            log.warning("Could not record %s event for %s: %s", reason, meta["name"], e)

    def tick(self) -> dict[str, Any]:
        started = time.monotonic()
        resources = self.kube.list_custom(GROUP, VERSION, self.namespace, PLURAL)
        channels = [ChannelSpec.from_resource(r) for r in resources]
        logins = [c.login for c in channels if not c.paused]
        live = self.helix.get_streams(logins) if logins else {}
        jobs = [JobInfo.from_job(j) for j in self.kube.list_jobs(self.namespace, f"{LABEL_MANAGED_BY}={COMPONENT}")]

        p = plan(channels, live, jobs, self.max_concurrent)
        by_name = {r["metadata"]["name"]: r for r in resources}
        created = []
        for ch, stream in p.create:
            job = build_job(ch, stream, self.job_template)
            try:
                self.kube.create_job(self.namespace, job)
            except ApiError as e:
                if e.status != 409:
                    log.error("Could not create Job for %s: %s", ch.login, e)
                    continue
            created.append(job["metadata"]["name"])
            jobs.append(JobInfo(job["metadata"]["name"], job["metadata"]["labels"][LABEL_CHANNEL],
                                stream.stream_id, False, False))
            self._k8s_event(by_name[ch.name], "RecordingScheduled",
                            f"Job {job['metadata']['name']} for {stream.category_name or 'unknown category'}")
            self.emitter.emit("recording.scheduled", **stream.event_fields(), **{
                "k8s.job.name": job["metadata"]["name"], "channel.priority": ch.priority})

        deferred = {ch.name for ch in p.deferred}
        for ch in channels:
            self._sync_status(by_name[ch.name], ch, live.get(ch.login), jobs_for(ch, jobs), ch.name in deferred)

        summary = {
            "controller.channels": len(channels),
            "controller.live": len(live),
            "controller.recordings_running": p.running + len(created),
            "controller.recordings_created": len(created),
            "controller.recordings_deferred": len(p.deferred),
            "controller.max_concurrent": self.max_concurrent,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        self.emitter.emit("controller.reconciled", **summary)
        return summary

    def _sync_status(self, resource: dict[str, Any], ch: ChannelSpec, stream: StreamMetadata | None,
                     jobs: list[JobInfo], deferred: bool) -> None:
        old = resource.get("status") or {}
        running = [j for j in jobs if not j.finished]
        failed_for_stream = [j for j in jobs if j.failed and stream and j.stream_id == stream.stream_id]
        if ch.paused:
            phase = "Paused"
        elif stream is None:
            phase = "Offline"
        elif running:
            phase = "Recording"
        elif deferred:
            phase = "Deferred"
        elif failed_for_stream:
            phase = "Failed"
        else:
            phase = "Live"

        new = dict(old)
        new.update({
            "phase": phase,
            "live": stream is not None,
            "activeJob": running[-1].name if running else None,
            "observedGeneration": resource["metadata"].get("generation"),
        })
        if stream is not None:
            new.update({
                "streamId": stream.stream_id,
                "categoryId": stream.category_id,
                "categoryName": stream.category_name,
                "title": stream.title,
                "tags": list(stream.tags),
                "language": stream.language,
                "isMature": stream.is_mature,
                "startedAt": stream.started_at or None,
            })

        previous = snapshot_from_status(ch.login, old)
        if stream is not None and previous is not None and previous.stream_id == stream.stream_id:
            change = diff(previous, stream)
            if change is not None:
                new["lastMetadataChangeAt"] = self.now()
                self._k8s_event(resource, "MetadataChanged", change.summary())
        if old.get("live") != new["live"]:
            reason = "StreamOnline" if stream else "StreamOffline"
            self._k8s_event(resource, reason, stream.category_name if stream else "stream ended")
        if phase != old.get("phase"):
            if phase == "Deferred":
                self._k8s_event(resource, "RecordingDeferred",
                                f"{self.max_concurrent} recordings already running", "Warning")
                self.emitter.emit("recording.deferred", **stream.event_fields(), **{
                    "channel.priority": ch.priority, "controller.max_concurrent": self.max_concurrent})
            elif phase == "Failed":
                self._k8s_event(resource, "RecordingFailed",
                                f"Job {failed_for_stream[-1].name} failed; not retrying this broadcast", "Warning")

        # Only write when something changed. A merge patch drops null fields, so compare without them.
        if _without_nulls(new) != _without_nulls(old):
            try:
                self.kube.patch_custom_status(GROUP, VERSION, self.namespace, PLURAL, ch.name, new)
            except ApiError as e:
                log.warning("Could not update status of %s: %s", ch.name, e)


def run_forever(reconciler: Reconciler, interval: float, stop, heartbeat_path: str | None = None) -> None:
    while not stop.is_set():
        try:
            reconciler.tick()
        except Exception as e:  # noqa: BLE001 - keep reconciling; the next tick may succeed
            log.exception("Reconcile failed: %s", e)
        if heartbeat_path:
            # Proves the loop is turning, not that Twitch answered: a Twitch outage
            # should show up in events and logs, not as a liveness restart loop.
            with open(heartbeat_path, "w", encoding="utf-8") as f:
                f.write(_utcnow())
        stop.wait(interval)
