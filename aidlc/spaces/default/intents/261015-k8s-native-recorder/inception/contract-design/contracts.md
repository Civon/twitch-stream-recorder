# Contracts

## 1. Wide event schema `twitch-recorder.event/v1`

One JSON object per line. Keys are flat and dotted. Fields with no value are
omitted rather than sent as null. Arrays are lists of strings.

### Common fields (every event)
| Key | Example | Notes |
|---|---|---|
| `ts` | `2026-10-15T09:12:03.120Z` | UTC, milliseconds |
| `event` | `stream.metadata_changed` | see catalogue below |
| `schema` | `twitch-recorder.event/v1` | bump on breaking change |
| `service.name` | `twitch-stream-recorder` / `twitch-recorder-controller` | |
| `service.version` | `2.5` | image tag (`APP_VERSION`) |
| `k8s.namespace.name`, `k8s.pod.name`, `k8s.node.name` | | downward API; absent outside Kubernetes |

### Recording context (recording Job / watch mode)
| Key | Example |
|---|---|
| `recording.id` | `kana-40000000001-20261015T080005` |
| `recording.part` | `0` (increments when one broadcast needs several captures) |
| `recording.quality`, `recording.container` | `best`, `mkv` |
| `recording.offset_ms` | position in this part's file |
| `stream.offset_ms` | time since the broadcast started (Helix `started_at`) |

### Stream snapshot (any event about a stream)
`channel.login`, `channel.id`, `stream.id`, `stream.started_at`,
`category.id`, `category.name`, `stream.title`, `stream.tags`,
`stream.language`, `stream.is_mature`, `stream.viewer_count`.

### Catalogue
| Event | Emitted by | Extra fields |
|---|---|---|
| `recording.started` | Job | snapshot, `recording.file` |
| `stream.metadata_changed` | Job | snapshot (after), `change.fields`, `category.before.id/.name`, `stream.title.before`, `stream.tags.added/.removed`, `stream.language.before`, `stream.is_mature.before` (each only when that field changed) |
| `stream.restarted` | Job | snapshot of the new broadcast, `stream.id.before` |
| `recording.session` | Job | `outcome` (completed, finalize_failed, no_output), `recording.duration_ms`, `recording.output`, `recording.output_bytes`, `recording.file` (raw, when kept), `recording.stopped`, `streamlink.exit_code`, `ffmpeg.exit_code`, `session.categories`, `session.category_changes`, `session.title_changes`, `session.metadata_changes`, `session.metadata_poll_errors`, `category.first.name`, `category.last.name` |
| `recording.skipped` | Job | `reason` (offline, stream_mismatch) |
| `recording.recovered` | watch mode | `outcome`, `recording.output` |
| `recording.scheduled` | controller | snapshot, `k8s.job.name`, `channel.priority` |
| `recording.deferred` | controller | snapshot, `channel.priority`, `controller.max_concurrent` |
| `controller.reconciled` | controller | `controller.channels`, `.live`, `.recordings_running`, `.recordings_created`, `.recordings_deferred`, `.max_concurrent`, `duration_ms` |

### Example: singing to game
```json
{"ts":"2026-10-15T08:47:05.004Z","event":"stream.metadata_changed","schema":"twitch-recorder.event/v1",
 "service.name":"twitch-stream-recorder","service.version":"2.5","k8s.pod.name":"rec-kana-40000000001-x7k2p",
 "recording.id":"kana-40000000001-20261015T080005","recording.part":0,"recording.quality":"best","recording.container":"mkv",
 "channel.login":"kana","channel.id":"42","stream.id":"40000000001","category.id":"516575","category.name":"VALORANT",
 "stream.title":"Ranked","stream.tags":["FPS","English"],"stream.language":"en","stream.viewer_count":1234,
 "change.fields":["category","title","tags"],"category.before.id":"26936","category.before.name":"Music",
 "stream.title.before":"Singing stream","stream.tags.added":["FPS"],"stream.tags.removed":["Singing"],
 "recording.offset_ms":2820000,"stream.offset_ms":2825000}
```

### Queries
- LogQL: `{namespace="twitch-stream-recorder"} | json | event="stream.metadata_changed" | category_before_name="Music"`
- Time spent per category (from `recording.session` and the sidecar timeline) is a lake query, not a metric.

## 2. Sidecar files (per recording part)
| File | Content |
|---|---|
| `<name>.events.jsonl` | every Job event for this part, same schema as stdout |
| `<name>.chapters.json` | `{stream_id, recorded_at, chapters:[{index, start_ms, end_ms, title, category_id, stream_title, tags}]}` (ACC-76 layout plus `category_id`, `stream_title`) |
| `<name>.mkv` | chapters embedded (FFMETADATA1, one per category run) |

## 3. `TwitchChannel` (recorder.civon.github.io/v1alpha1)
```yaml
apiVersion: recorder.civon.github.io/v1alpha1
kind: TwitchChannel
metadata: {name: kana, namespace: twitch-stream-recorder}
spec:
  login: kana          # required, ^[a-zA-Z0-9_]{1,25}$
  quality: best        # default best
  priority: 0          # default 0; higher first under the cap
  paused: false
  container: mkv       # mkv | mp4
  bandwidth: 8M        # optional, kubernetes.io/ingress-bandwidth
status:
  phase: Recording     # Offline | Live | Recording | Deferred | Paused | Failed
  live: true
  streamId: "40000000001"
  categoryId: "516575"
  categoryName: VALORANT
  title: Ranked
  tags: [FPS, English]
  activeJob: rec-kana-40000000001
  lastMetadataChangeAt: "2026-10-15T08:47:30Z"
```

## 4. Recording Job
- Name `rec-<dns(login)>-<stream id>` (63-char cap with hash suffix).
- Labels `recorder.civon.github.io/channel`, `recorder.civon.github.io/stream-id`,
  `app.kubernetes.io/managed-by=twitch-recorder-controller`,
  `app.kubernetes.io/component=recording`.
- Command `python -m recorder.cli record --login <l> --quality <q> --stream-id <id> --container <c>`.
- Exit codes: 0 broadcast recorded (or nothing to record); 1 a finalize failed
  (raw kept); 2 configuration error; 75 stopped while still live (retry).
