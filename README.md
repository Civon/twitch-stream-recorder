# Twitch Stream Recorder

This script allows you to record twitch streams live to .mp4 files.

## Prerequisite

- `client_id` - you can grab this from [here](https://dev.twitch.tv/console/apps) once you register your application
- `client_secret` - you generate this [here](https://dev.twitch.tv/console/apps) as well, for your registered application
- (Optional)`twitch_oauth_token` - personal OAuth token from Twitch. Check [this section](#how-to-get-twitch-oauth-token) for details.

## Bulk run using helm

```
# helm install
helm install twitch-stream-recorder ./helm --namespace twitch-stream-recorder-namespace
```

add list of channels to ./helm/values.override.yaml and upgrade

```bash
helm upgrade --install twitch-stream-recorder ./helm \
  --namespace twitch-stream-recorder \
  --values ./helm/values.yaml \
  --values ./helm/values.override.yaml
```

### Operator mode (one Job per live broadcast)

Set `operator.enabled: true` (needs an image built from this version or later). The
chart then installs the `TwitchChannel` CRD and a small controller instead of one
Deployment per channel. The same `channels:` list becomes `TwitchChannel` resources;
the controller checks all of them with one Twitch API call per 100 channels and
starts a recording Job for each live broadcast.

```bash
kubectl -n twitch-stream-recorder get twitchchannels
# NAME          LOGIN         PHASE       CATEGORY   AGE
# kana          kana          Recording   Music      2d
# valorant-tw   valorant_tw   Deferred    VALORANT   2d

kubectl -n twitch-stream-recorder describe twitchchannel kana
# Events:  MetadataChanged  category Music -> VALORANT; title 'Ranked'
```

Useful values: `operator.maxConcurrentRecordings` (cap on parallel recordings, higher
`priority` channels go first), per-channel `bandwidth` (download cap through the CNI
bandwidth plugin), `storage.existingClaim` (a ReadWriteMany claim shared by all Jobs)
and `secrets.existingSecret`. Helm does not upgrade CRDs; after a chart upgrade run
`kubectl apply -f helm/crds/`.

## Stream metadata events

While recording, the recorder checks the stream's category, title, tags, language and
mature flag every `--metadata-interval` seconds (default 30). A change, such as a
singing stream (`Music`) switching to a game, is written as a wide event: one flat
JSON object with the full context and the position in the file where it happened.

```json
{"ts":"2026-10-15T09:12:03.120Z","event":"stream.metadata_changed","schema":"twitch-recorder.event/v1",
 "channel.login":"kana","stream.id":"40000000001","recording.id":"kana-40000000001-20261015T080005",
 "recording.offset_ms":3723000,"change.fields":["category","title"],
 "category.before.name":"Music","category.name":"VALORANT","stream.title":"Ranked","k8s.pod.name":"rec-kana-40000000001-x7k2p"}
```

| Event | When |
|---|---|
| `recording.started` | capture begins (full metadata snapshot) |
| `stream.metadata_changed` | category, title, tags, language or mature flag changed |
| `recording.session` | capture finalized: outcome, duration, bytes, categories visited, change counts |
| `recording.skipped` / `recording.recovered` | Job started too late / leftover file finalized after a restart |
| `recording.scheduled` / `recording.deferred` / `controller.reconciled` | controller decisions (operator mode) |

Where they go (`EVENT_SINKS` or `--event-sinks`, comma separated):

- `stdout` (default): JSON lines on stdout. In Kubernetes, Loki/Alloy/Vector already
  collect these: `{namespace="twitch-stream-recorder"} | json | event="stream.metadata_changed"`.
- `log`: the same fields as a logfmt line in the application log.
- Always: a `<recording>.events.jsonl` file next to the video. MKV recordings also get
  chapters at each category change, and a `<recording>.chapters.json` sidecar.

## Run on Docker

### Docker Compose

1. Clone the repo.
1. Simply copy .env.example to .env file
1. run `Docker compose up`

#### Single container(recommend)

Create .env file from .env.example and run

```
docker run --name twitch-stream-recorder \
    -v <your-vod-warehouse>:/app/rec:Z \
    --env-file .env \
    ghcr.io/civon/twitch-stream-recorder \
    --username myFavStreamer # You can override env by args \
    -q worst \
    --log warn
```

## Requirements

1. [python3.8](https://www.python.org/downloads/release/python-380/) or higher
2. [streamlink](https://streamlink.github.io/)
3. [ffmpeg](https://ffmpeg.org/)

## Setting up

1. Check if you have latest version of streamlink:

   - `streamlink --version` shows current version
   - `streamlink --version-check` shows available upgrade
   - `sudo pip install --upgrade streamlink` do upgrade

2. Install `requests` module [if you don't have it](https://pypi.org/project/requests/)
   - Windows: `python -m pip install requests`
   - Linux: `python3.8 -m pip install requests`
3. Create `config.py` file in the same directory as `twitch-recorder.py` with:

```properties
root_path = "/home/abathur/Videos/twitch"
username = "forsen"
client_id = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
client_secret = "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
twitch_oauth_token = "" # Optional
```

`root_path` - path to a folder where you want your VODs to be saved to  
`username` - name of the streamer you want to record by default  
`client_id` - you can grab this from [here](https://dev.twitch.tv/console/apps) once you register your application  
`client_secret` - you generate this [here](https://dev.twitch.tv/console/apps) as well, for your registered application
`twitch_oauth_token` - personal OAuth token from Twitch (Optional)

4. (Optional) Modify .streamlinkrc base on https://streamlink.github.io/cli.html

### How to get Twitch OAuth Token

Check [this document](https://streamlink.github.io/cli/plugins/twitch.html) for details.

After login Twitch on web browser, open Developer console. And paste Javascript code below to "Console" tab and press enter.

```
document.cookie.split("; ").find(item=>item.startsWith("auth-token="))?.split("=")[1]
```

## Running script

The script will be logging to a console and to a file `twitch-recorder.log`

### On linux

Run the script

```shell script
python3.8 twitch-recorder.py
```

To record a specific streamer use `-u` or `--username`

```shell script
python3.8 twitch-recorder.py --username forsen
```

To specify quality use `-q` or `--quality`

```shell script
python3.8 twitch-recorder.py --quality 720p
```

To change default logging use `-l`, `--log` or `--logging`

```shell script
python3.8 twitch-recorder.py --log warn
```

To disable ffmpeg processing (fixing errors in recorded file) use `--disable-ffmpeg`

```shell script
python3.8 twitch-recorder.py --disable-ffmpeg
```

If you want to run the script as a job in the background and be able to close the terminal:

```shell script
nohup python3.8 twitch-recorder.py >/dev/null 2>&1 &
```

In order to kill the job, you first list them all:

```shell script
jobs
```

The output should show something like this:

```shell script
[1]+  Running                 nohup python3.8 twitch-recorder > /dev/null 2>&1 &
```

And now you can just kill the job:

```shell script
kill %1
```

### On Windows

You can run the scipt from `cmd` or [terminal](https://www.microsoft.com/en-us/p/windows-terminal/9n0dx20hk701?activetab=pivot:overviewtab), by simply going to the directory where the script is located at and using command:

```shell script
python twitch-recorder.py
```

The optional parameters should work exactly the same as on Linux.

## Credits

- @junian
- @Ancalentari
- @AntonioMIN
- @jim60105
