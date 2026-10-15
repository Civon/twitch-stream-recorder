# U1 recorder: functional and NFR design, code summary

## Modules
| Module | Responsibility |
|---|---|
| `recorder/twitch.py` | `HelixClient`: app token via form body, one 401 refresh-and-retry, `get_streams()` in batches of 100 |
| `recorder/metadata.py` | `StreamMetadata` snapshot from Helix; `diff()` -> `MetadataChange` (category by id, falling back to name; tags as a set) |
| `recorder/events.py` | `EventEmitter` with bound context; sinks `StdoutSink`, `LogSink` (logfmt), `JsonlFileSink`; failures logged, never raised |
| `recorder/chapters.py` | timeline -> chapters (split on category), FFMETADATA1 with escaping, timeline from an events sidecar |
| `recorder/session.py` | `RecordingSession.run()`: start streamlink, poll metadata every interval, finalize, summary event; `finalize()` deletes raw only on success; `finalize_leftovers()` |
| `recorder/cli.py` | `record` (Job: one broadcast, many parts, SIGTERM-aware exit codes) and `watch` (legacy loop) |
| `twitch-recorder.py`, `config.py` | legacy entry point and config, now thin |

## Session algorithm
```
emit recording.started (offset 0)
start streamlink -> recorded/<login>/<name>.ts|.mp4
loop:
  wait(metadataInterval) for streamlink
  if still running and not stopping:
    latest = Helix.get_stream(login)        # errors counted, ignored
    if latest.stream_id changed: emit stream.restarted
    elif diff(current, latest): emit stream.metadata_changed(offset), append to timeline
finalize: chapters from timeline -> ffmeta -> ffmpeg -map 0:v? -map 0:a? -map_chapters 1 -c copy
          delete raw only if exit 0 and output non-empty; write chapters.json
emit recording.session; move events.jsonl next to the output
```
`-map 0:v? -map 0:a?` keeps Twitch's ID3 data stream out of the MKV, which
Matroska would reject. MP4 mode keeps the original command (default stream
selection, no chapter embedding), plus `-nostdin -y` so an existing partial
output from a crash cannot block on a prompt.

## Record command (Job)
- Skip (exit 0) when offline or when `--stream-id` is no longer the live broadcast.
- After each part: re-check; same broadcast still live -> next part
  immediately; empty captures back off 15/30/45/60 s and give up after 5.
- SIGTERM: stop streamlink, finalize, exit 75 if the broadcast is still live.

## NFR design
- Best-effort telemetry: a broken sink or a failed metadata poll never stops capture.
- Time: offsets use a monotonic clock from streamlink start; precision is one
  poll interval plus HLS latency and Helix cache delay.
- Compatibility: no syntax newer than Python 3.8; only `requests` beyond the stdlib.
