"""TwitchChannel specs and the recording Job built for a live broadcast.

The Job's shape (image, volumes, resources, security context, scheduling)
comes from a template the Helm chart renders, so the chart stays the single
place to customize pods. The controller only stamps identity onto a copy:
name, labels, owner, and the channel arguments.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from typing import Any

from recorder.controller import (COMPONENT, GROUP, KIND, LABEL_CHANNEL, LABEL_MANAGED_BY, LABEL_STREAM_ID,
                                 VERSION)
from recorder.metadata import StreamMetadata

BANDWIDTH_ANNOTATION = "kubernetes.io/ingress-bandwidth"
RECORDER_CONTAINER = "recorder"


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    namespace: str
    uid: str
    login: str
    quality: str = "best"
    priority: int = 0
    paused: bool = False
    container: str = "mkv"
    bandwidth: str | None = None

    @classmethod
    def from_resource(cls, obj: dict[str, Any]) -> "ChannelSpec":
        meta, spec = obj.get("metadata", {}), obj.get("spec", {})
        return cls(
            name=meta["name"],
            namespace=meta.get("namespace", ""),
            uid=meta.get("uid", ""),
            login=str(spec.get("login") or meta["name"]).lower(),
            quality=spec.get("quality", "best"),
            priority=int(spec.get("priority", 0)),
            paused=bool(spec.get("paused", False)),
            container=spec.get("container", "mkv"),
            bandwidth=spec.get("bandwidth") or None,
        )


def dns_label(value: str) -> str:
    """Lower-case RFC 1123 label: Twitch logins may contain underscores."""
    label = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return label or "x"


def job_name(login: str, stream_id: str) -> str:
    """Deterministic per broadcast, so a second create is a harmless 409."""
    name = f"rec-{dns_label(login)}-{dns_label(stream_id)}"
    if len(name) <= 63:
        return name
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return f"{name[:54].rstrip('-')}-{digest}"


def _recorder_container(pod_spec: dict[str, Any]) -> dict[str, Any]:
    containers = pod_spec.get("containers") or []
    if not containers:
        raise ValueError("Job template has no containers")
    for c in containers:
        if c.get("name") == RECORDER_CONTAINER:
            return c
    return containers[0]


def build_job(channel: ChannelSpec, stream: StreamMetadata, template: dict[str, Any]) -> dict[str, Any]:
    job = copy.deepcopy(template)
    job["apiVersion"], job["kind"] = "batch/v1", "Job"
    identity = {
        LABEL_MANAGED_BY: COMPONENT,
        LABEL_CHANNEL: dns_label(channel.login),
        LABEL_STREAM_ID: dns_label(stream.stream_id),
        "app.kubernetes.io/component": "recording",
    }

    meta = job.setdefault("metadata", {})
    meta["name"] = job_name(channel.login, stream.stream_id)
    meta["namespace"] = channel.namespace
    meta["labels"] = {**meta.get("labels", {}), **identity}
    meta["annotations"] = {
        **meta.get("annotations", {}),
        f"{GROUP}/login": channel.login,
        f"{GROUP}/category": stream.category_name,
        f"{GROUP}/title": stream.title[:256],
    }
    # Deleting the TwitchChannel stops its recording (garbage collection).
    meta["ownerReferences"] = [{
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": KIND,
        "name": channel.name,
        "uid": channel.uid,
        "controller": True,
        "blockOwnerDeletion": False,
    }]

    pod_template = job.setdefault("spec", {}).setdefault("template", {})
    pod_meta = pod_template.setdefault("metadata", {})
    pod_meta["labels"] = {**pod_meta.get("labels", {}), **identity}
    if channel.bandwidth:
        # Shapes this pod's download rate; needs the CNI bandwidth plugin (bundled with k3s flannel).
        pod_meta["annotations"] = {**pod_meta.get("annotations", {}), BANDWIDTH_ANNOTATION: channel.bandwidth}

    container = _recorder_container(pod_template.setdefault("spec", {}))
    container["args"] = [*container.get("args", []),
                         "--login", channel.login,
                         "--quality", channel.quality,
                         "--stream-id", stream.stream_id,
                         "--container", channel.container]
    return job
