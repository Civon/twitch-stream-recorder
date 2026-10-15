# Architecture decision records

## ADR-001 Controller + one Job per live broadcast
**Status:** Proposed (2026-10-15)

**Context.** Today each channel is an always-on Deployment with its own RWO
PVC. Most of the day those pods only poll Twitch. There is no global view, no
concurrency limit (ACC-18), and no status besides logs (ACC-19).

**Options.**
| | A. Keep Deployment per channel | B. CRD + controller + Job per broadcast | C. Poller + message queue (NATS) + KEDA ScaledJob |
|---|---|---|---|
| Idle cost | N pods always on | 1 small pod | poller + broker + KEDA |
| Twitch calls / poll | N | ceil(N/100) | ceil(N/100) |
| Concurrency cap, priority | no | yes, in the controller | yes, queue consumers |
| Status surface | logs | `kubectl get tch`, Events | queue + custom UI |
| New moving parts | none | CRD + controller | broker, KEDA, poller |
| Matches ACC-18 "message queue" wording | no | partly (cap + priority + bandwidth) | yes |

**Decision.** B. The Job is the natural unit for "record this broadcast",
Kubernetes gives retries, TTL cleanup, and failure policy for free, and the
CRD gives a status surface without building a UI. C adds a broker and KEDA for
a queue whose length is at most the number of live channels.

**Consequences.** The Job name `rec-<login>-<stream id>` makes scheduling
idempotent. Shared storage is needed so Jobs can land on any node (RWX).
Legacy mode stays available behind `operator.enabled: false`. If the queue
idea comes back (e.g. multi-site WAN shaping across Taipei and Taichung),
the controller's `plan()` is the seam to replace.

## ADR-002 Detect metadata changes by polling Helix inside the Job
**Status:** Proposed

**Context.** The request is to notice when a stream's category changes
(singing under `Music` to a game). Sources: Helix `GET /streams` (polling,
app token, data cached by Twitch for tens of seconds to minutes) or EventSub
`channel.update` (push; WebSocket transport needs a user access token,
webhook transport needs a public HTTPS callback).

**Decision.** Poll `GET /streams` for the channel every `metadataInterval`
seconds (default 30) from the recording Job. The Job is the only component
that knows the position in the file, so it owns the timeline. The controller
also sees metadata in its batch poll and reflects it into status and
Kubernetes Events, but does not emit `stream.*` wide events (no duplicates).

**Consequences.** Change timestamps are accurate to one interval plus Helix
cache delay. Changes during live-but-not-recorded periods (paused, deferred)
appear only as Kubernetes Events. EventSub is the follow-up that would make
the controller the single, near-real-time source.

## ADR-003 Wide events: JSON lines on stdout + per-recording sidecar
**Status:** Proposed

**Context.** "Export as wide event or log." The planned observability stack
is Loki for logs (ACC-165) and a Parquet/Iceberg cold path (ACC-34, ACC-141).

**Options.** OTLP log exporter (new dependency and collector endpoint);
custom webhook; JSON lines on stdout; Kubernetes Events only.

**Decision.** One flat JSON object per event on stdout (default sink),
collected by whatever ships pod logs. A `log` sink writes the same fields as
logfmt for log-file-only setups. Every recording also gets an append-only
`events.jsonl` sidecar beside the video, which is the input for chapters and
for crash recovery, and can be bulk-loaded into the lake. A schema id
(`twitch-recorder.event/v1`) versions the format.

**Consequences.** No new dependency or endpoint. Flat dotted keys map to Loki
`| json`, ClickHouse columns, or Parquet columns. OTLP or a webhook (e.g.
n8n) can be added later as another sink.

## ADR-004 Plain reconcile loop, no controller framework
**Status:** Proposed

**Decision.** A 30-second level-triggered loop over the Kubernetes REST API
with `requests` (about 60 lines), in the same image and language as the
recorder. Liveness has to be polled from Twitch anyway, so watches add
little. One replica with `Recreate`; correctness does not depend on a single
leader because Job names are deterministic.

**Rejected.** kopf (per-object timers would mean one Helix call per channel
unless batched by hand); Go controller-runtime (second language and image).

## ADR-005 API group and resources
**Status:** Proposed

**Decision.** Group `recorder.civon.github.io` (a domain the owner controls),
version `v1alpha1`, one kind `TwitchChannel` with a status subresource and
printer columns. No `Recording` CRD yet: the Job (with labels, annotations
and owner reference) is the per-broadcast object, and the wide events are the
durable history.

**Revisit when** recordings need to outlive Job TTL in the API (e.g. for a UI,
ACC-19) or need post-processing state (AV1 archive, ACC-17).
