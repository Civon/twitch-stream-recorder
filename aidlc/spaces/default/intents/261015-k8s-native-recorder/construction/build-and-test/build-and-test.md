# Build and test

Run on 2026-10-15 in a sandbox. Commands use the repository root.

## Unit tests
```
python -m pytest -q          # 40 passed (Python 3.13)
uv run --python 3.10 ...     # 40 passed (image runtime)
uv run --python 3.8 ...      # 40 passed (README minimum)
```

## ffmpeg remux with chapters (real ffmpeg 7.1)
A 12 s TS (H.264 + AAC) finalized with a two-point timeline produced
`processed/kana/kana - x.mkv` whose embedded chapters read back as
`Music` 0-5 s and `VALORANT` 5-12 s, plus `chapters.json`; the raw `.ts` and
temporary `.ffmeta` were removed. With ffmpeg failing, the raw file stayed and
`outcome` was `finalize_failed`.

## `record` command smoke test (real subprocesses)
Fake `streamlink` on PATH (writes a sample TS after 4 s), real ffmpeg, fake
Helix answering Music, then VALORANT, then offline, metadata interval 1 s:
- part 0: `recording.started` (Music), `stream.metadata_changed` at
  `recording.offset_ms=2001` (Music -> VALORANT), `recording.session`
  `completed`, `session.categories=["Music","VALORANT"]`; MKV chapters at 0
  and 2.001 s.
- part 1 (`... - Ranked - part1.mkv`) started because the same broadcast was
  still live; then offline; exit 0.
- SIGTERM 3 s in: streamlink terminated (exit 130), part finalized,
  `recording.stopped=true`, process exit 75 (broadcast still live).

## Helm
- Legacy values (`values.override.yaml.template`): rendered output is
  byte-identical to the chart before this change. `helm lint` passes.
- Operator values (`operator.enabled=true`, cap 2): 12 objects rendered
  (with `--include-crds`). kubeconform `-strict`: all built-in kinds valid,
  both TwitchChannels valid against the CRD's own schema, and a Job stamped by
  `build_job()` from the rendered template valid as `batch/v1 Job`.
  kubeconform has no schema for the CRD object itself; the k3s apply below
  covers it.

## Kubernetes (k3s v1.31.4 in Docker)
Applied the operator render with server-side apply (CRD established, defaults
filled on the TwitchChannels), then ran `Reconciler.tick()` four times
against the API server with Twitch faked and `maxConcurrentRecordings=1`:

```
tick 1  kana        Recording  Music     rec-kana-111   (Job created, ownerRef kana, podFailurePolicy Ignore)
        valorant-tw Deferred   VALORANT
tick 2  kana        Recording  VALORANT  rec-kana-111   (no new Job)
tick 4  kana        Offline ; valorant-tw Offline

Events:
kana         StreamOnline        Music
kana         RecordingScheduled  Job rec-kana-111 for Music
valorant-tw  RecordingDeferred   1 recordings already running   (Warning)
valorant-tw  StreamOnline        VALORANT
kana         MetadataChanged     category Music -> VALORANT; title 'Ranked'; tags +['FPS'] -['English', 'Singing']
kana         StreamOffline       stream ended
valorant-tw  StreamOffline       stream ended
```

## Not verified here
- The Docker image build (Alpine package mirror unreachable from the sandbox).
  The Dockerfile change is one `COPY` and one `ENV`.
- A live Twitch broadcast end to end, the controller running as a pod with
  its own service account, the CNI bandwidth plugin, and CephFS RWX storage.
  These need the real cluster (rollout steps in delivery-plan.md).
