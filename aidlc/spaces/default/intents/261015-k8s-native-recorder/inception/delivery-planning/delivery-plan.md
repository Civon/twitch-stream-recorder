# Delivery plan

## Bolt 1: k8s-native draft (this PR)
U1 + U2 + U3 behind `operator.enabled: false`. Merging changes nothing for an
existing install except: a new image is needed for operator mode, and the
legacy path now keeps the raw file when ffmpeg fails, stops sending an empty
OAuth header, and writes `.events.jsonl` / `.chapters.json` next to each MP4.

## Rollout on the cluster
1. Release an image from this branch (e.g. `2.5`); set `image.tag`.
2. Decide storage: a ReadWriteMany class (CephFS) for `storage.existingClaim`,
   or `sharedAccessMode: ReadWriteOnce` plus `operator.job.nodeSelector`.
3. Move Twitch credentials to an external Secret; set `secrets.existingSecret`.
4. `helm upgrade` with `operator.enabled: true`, `maxConcurrentRecordings`
   sized to the WAN link (e.g. 1080p60 is about 6-8 Mbit/s per stream).
5. Watch `kubectl get tch -w` through one broadcast; check the MKV chapters.
6. Point Loki/Alloy at the namespace; save the LogQL query from contracts.md.
7. Copy old recordings from the per-channel PVCs to the shared claim if wanted,
   then delete the old PVCs (they are not removed automatically).

## Follow-ups (proposed, not yet in Linear)
| Proposal | Why | Relates to |
|---|---|---|
| EventSub `channel.update` as the metadata source in the controller | near-real-time changes; also covers live-but-not-recorded channels | ADR-002, ACC-114 |
| Prometheus metrics endpoint on the controller (live, recording, deferred, metadata changes) | alerting on stuck or failed recordings | ACC-20, ACC-34 |
| `recover` CronJob for raw files left by killed pods (mtime guard so in-flight files are skipped) | Job mode does not run the startup leftover pass | ACC-15 |
| `Recording` CRD or a small read-only status page | history beyond Job TTL; ACC-19 UI | ADR-005, ACC-19 |
| AV1 archive Job for recordings older than 30 days, GPU node selector | storage cost | ACC-17 |
| Load `events.jsonl` into Parquet/Iceberg | category timelines for analytics | ACC-34, ACC-141 |
| Run recording pods as non-root (needs a one-time chown of existing data) | hardening | NFR-4 |
| CI: run pytest and helm lint on PRs | keep the chart and code honest | - |
