# Reverse engineering: twitch-stream-recorder (before this intent)

## Business overview
Records Twitch channels to disk while they are live, for later watching
(Emby/Jellyfin) and downstream automation. Single operator, homelab.

## Architecture
- `twitch-recorder.py` (~200 lines): one process per channel. Every 15 s it
  calls Helix `GET /streams?user_login=<one login>`; when live it runs
  `streamlink ... -o recorded/<login>/<name>.mp4`, then
  `ffmpeg -c copy` into `processed/<login>/`, then deletes the raw file.
- `config.py`: credentials and username from env (`username`, `client_id`,
  `client_secret`, `twitch_oauth_token`), root `/app/rec`.
- Docker image `ghcr.io/civon/twitch-stream-recorder` (python:3.10-alpine +
  streamlink + ffmpeg), built on release.
- Helm chart (`helm/`, scaffolded recently): for each entry in `channels`, one
  Deployment plus one `ReadWriteOnce` `local-path` PVC; one Secret rendered
  from values.

## Findings that shape the design
| # | Finding | Effect |
|---|---|---|
| F1 | One always-on pod per channel, idle most of the day, each polling Twitch separately | N pods and N API calls per 15 s |
| F2 | Per-channel RWO local-path PVC pins each pod to one node | no rescheduling on node loss |
| F3 | ffmpeg exit code ignored; raw file deleted even if the remux failed | data loss (ACC-15) |
| F4 | Empty `twitch_oauth_token` (Docker default) is still sent as `Authorization=OAuth ` | needless failed auth header |
| F5 | Client secret placed in the token URL query string | secret in proxy/access logs |
| F6 | No record of what a stream was about over time; filename keeps only the first title | category changes (singing -> game) are lost |
| F7 | No status surface besides logs; no concurrency limit | ACC-18, ACC-19, ACC-20 |
| F8 | Recorder is PID 1 with no SIGTERM handling | pod stop kills streamlink abruptly, no finalize |

## Technology stack
Python 3 (stdlib + requests), streamlink, ffmpeg, Helm 3, k3s on Proxmox VMs
(cluster-terraform `proxmox-k3s`), Loki planned (ACC-165), Parquet/Iceberg
cold path planned (ACC-34).
