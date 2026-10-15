# Requirements: k8s-native recorder with metadata change events

## Intent
Move the recorder from "one long-running pod per channel" to a Kubernetes
native design, and record what each stream is about over time: when a stream
changes category (for example a singing segment under `Music` moving to a
game), the change is captured with its position in the recording and exported
as a wide event or log line.

## Functional requirements

### FR-1 Declarative channels
- Channels are `TwitchChannel` resources (`spec.login`, `quality`, `priority`,
  `paused`, `container`, `bandwidth`).
- `kubectl get twitchchannels` shows login, phase, and current category.
- **Accept:** CRD installs; the chart renders one resource per `channels` entry;
  CRD defaults fill unset fields. (k3s run)

### FR-2 One Job per live broadcast
- A controller checks every channel's live state with one Helix call per 100
  channels and creates one Job per live broadcast, named
  `rec-<login>-<stream id>`, owned by the channel.
- A second create for the same broadcast is a no-op (HTTP 409).
- **Accept:** reconciler run on k3s creates exactly one Job per broadcast.

### FR-3 Record the whole broadcast
- The Job records until the broadcast ends. If streamlink exits while the same
  broadcast is still live, the Job starts the next part immediately.
- On SIGTERM the Job stops streamlink, finalizes what it has, and exits 75 if
  the broadcast is still live so the Job controller runs it again.
- **Accept:** CLI tests and record smoke test (parts, SIGTERM exit 75).

### FR-4 Metadata change detection
- While recording, poll the stream every `metadataInterval` seconds (default
  30) and detect changes of category, title, tags (as a set), language, and
  mature flag.
- **Accept:** a change from `Music` to `VALORANT` yields one
  `stream.metadata_changed` event with `category.before.*`, `category.*`,
  `change.fields`, and `recording.offset_ms`.

### FR-5 Export as wide event or log
- Every event is one flat JSON object with full context (channel, stream,
  recording, category, pod, node, service version, schema version).
- Sinks: `stdout` JSON lines (default, for Loki/Alloy/Vector), `log` (logfmt
  line in the app log), and always a per-recording `*.events.jsonl` sidecar.
- One `recording.session` event per session summarizes it (outcome, duration,
  bytes, categories visited, change counts, exit codes).
- **Accept:** event tests; record smoke output.

### FR-6 Chapters from category changes (part of ACC-76)
- MKV output gets one chapter per run of the same category; a
  `chapters.json` sidecar has the same chapters.
- After a crash, chapters are rebuilt from the `events.jsonl` sidecar.
- **Accept:** ffprobe/ffmetadata on a real remux shows `Music` then `VALORANT`.

### FR-7 Safe finalize (ACC-15)
- The raw capture is deleted only when ffmpeg exits 0 and the output is
  non-empty; otherwise it stays in `recorded/` and the event says
  `finalize_failed`.

### FR-8 Concurrency and bandwidth (part of ACC-18)
- `operator.maxConcurrentRecordings` caps running recordings; higher
  `priority` gets free slots first; running recordings are never preempted.
- Per-channel `bandwidth` sets `kubernetes.io/ingress-bandwidth` on the pod.
- Deferred channels show `phase: Deferred` and a `RecordingDeferred` event.

### FR-9 Visible state (part of ACC-19)
- Status: `phase` (Offline, Live, Recording, Deferred, Paused, Failed),
  `streamId`, `categoryName`, `title`, `tags`, `activeJob`.
- Kubernetes Events on the channel: `StreamOnline`, `StreamOffline`,
  `RecordingScheduled`, `RecordingDeferred`, `RecordingFailed`,
  `MetadataChanged`.

### FR-10 Backward compatibility
- `operator.enabled: false` (default) renders exactly the previous manifests.
- `twitch-recorder.py -u/-q/-l/--disable-ffmpeg` and `config.py` keep working
  and keep producing MP4 by default.

## Non-functional requirements
| ID | Requirement | How it is met |
|---|---|---|
| NFR-1 | Twitch API cost O(channels/100) per poll for the controller, plus one call per active recording per `metadataInterval` | batch `get_streams`; Helix allows 800 req/min |
| NFR-2 | Losing an event never stops a recording | sinks are best effort; poll errors counted, not raised |
| NFR-3 | Idempotent scheduling without leader election | deterministic Job names, 409 tolerated, `Recreate` rollout |
| NFR-4 | Least privilege | namespace Role; controller non-root, read-only root FS; recording pods get no API token |
| NFR-5 | No secrets in values when an external store is used | `secrets.existingSecret` |
| NFR-6 | Python 3.8+ for bare-metal users, 3.10 in the image | tests on 3.8, 3.10, 3.13 |
| NFR-7 | Metadata change precision: one poll interval plus Helix cache delay (tens of seconds up to a few minutes) | documented; EventSub follow-up for near real time |
| NFR-8 | Events load into the planned stack without a custom parser | flat keys; `schema` field for versioning |

## Traceability to Linear
| Linear | Covered here |
|---|---|
| ACC-15 safe delete | FR-7 |
| ACC-16 MKV | FR-6 (MKV default in operator mode) |
| ACC-18 traffic shaping | FR-8 (cap, priority, bandwidth); message queue not adopted, see ADR-001 |
| ACC-19 status UI | FR-9 (status + Events; UI itself is a follow-up) |
| ACC-20 monitoring | FR-5 (events); metrics are a follow-up |
| ACC-76 chapters sidecar | FR-6 |
| ACC-114 category-change event type | FR-4, FR-5 |
| ACC-34 / ACC-141 lakehouse | NFR-8, sidecars |
