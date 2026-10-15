# Requirements questions

These would normally be asked one by one at the requirements gate. The run was
autonomous, so each has the assumption that was used. Change any answer in the
PR review and the design follows.

| # | Question | Assumed answer | Changes if different |
|---|---|---|---|
| Q1 | Does "category change from singing to game" mean the Twitch category (`game_name`), the tags, or the title? | All three are tracked; chapters split on category only. | Chapter rule in `chapters.build_chapters`. |
| Q2 | Wide event, log, or both? | Both: JSON lines on stdout (wide event) by default, `log` sink for logfmt, and an `events.jsonl` sidecar always. | `EVENT_SINKS` default. |
| Q3 | Is a ReadWriteMany storage class (e.g. CephFS `vod-data`) available inside k3s? | Not assumed. Chart defaults to RWX but documents RWO + nodeSelector. | `storage.*` defaults. |
| Q4 | Is near-real-time change detection needed (seconds), or is 30 s plus Helix cache delay enough? | Polling is enough for chapters and analytics. | EventSub `channel.update` (needs a user token for WebSocket or a public callback for webhooks). |
| Q5 | Should deleting a `TwitchChannel` stop its running recording? | Yes (owner reference, garbage collection). | Drop the ownerReference in `jobs.build_job`. |
| Q6 | Keep MP4 as the legacy default? | Yes; operator mode defaults to MKV. | `--container` default for `watch`. |
| Q7 | Should the controller emit `stream.*` wide events for channels that are live but not recorded (paused/deferred)? | No; only Kubernetes Events + status. Recording Jobs own `stream.*` events. | See ADR-003. |
| Q8 | Does the CNI on the k3s nodes include the bandwidth plugin? | k3s' bundled flannel config includes it; verify on the cluster. | `bandwidth` is a no-op without it. |
