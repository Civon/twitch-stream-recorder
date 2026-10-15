# U2 controller: functional and NFR design, code summary

## Modules
| Module | Responsibility |
|---|---|
| `recorder/controller/kube.py` | REST client: in-cluster token (re-read per call) and CA, or `kubectl proxy` via `K8S_API_URL`; list CRs, patch status (merge patch), list/create Jobs, create Events |
| `recorder/controller/jobs.py` | `ChannelSpec` from a CR; `job_name()`; `build_job()` stamps name, labels, annotations, ownerReference, bandwidth annotation and arguments onto a copy of the Helm template |
| `recorder/controller/scheduler.py` | `JobInfo` from Job conditions; `plan()` -> create / deferred, priority first, cap on unfinished Jobs, never preempts |
| `recorder/controller/reconciler.py` | `tick()`: list, batch Helix, plan, create, sync status, Kubernetes Events, `controller.reconciled` wide event; `run_forever()` with heartbeat file |
| `recorder/controller/__main__.py` | env configuration, SIGTERM |

## Status phases
| Phase | When |
|---|---|
| Paused | `spec.paused` |
| Offline | not live |
| Recording | an unfinished Job exists for the channel |
| Deferred | live, no Job, cap reached |
| Failed | live, this broadcast's Job failed (backoff exhausted); not retried |
| Live | live, no Job yet (transient) |

Status is patched only when it changed (null-insensitive comparison), so an
idle cluster gets no writes. Kubernetes Events are recorded on transitions:
`StreamOnline`, `StreamOffline`, `RecordingScheduled`, `RecordingDeferred`,
`RecordingFailed`, `MetadataChanged`.

## NFR design
- Idempotency: deterministic Job names; HTTP 409 on create is success.
- Availability: a failed tick is logged and retried next interval. The
  heartbeat for the liveness probe is written every loop, not only on success,
  so a Twitch outage does not turn into a restart loop.
- API cost: one list of CRs, one list of Jobs (label selector), one Helix call
  per 100 channels per tick.
- Security: namespace Role (read CRs, patch status, create Jobs and Events);
  runs as UID 65534 with a read-only root filesystem.
