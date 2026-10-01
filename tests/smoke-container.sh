#!/usr/bin/env sh
set -eu

IMAGE_NAME="twitch-stream-recorder:smoke"
CONFIG_FILE="$(mktemp)"
trap 'rm -f "$CONFIG_FILE"' EXIT

cat >"$CONFIG_FILE" <<'EOF'
root_path = "/recordings"
username = "smoke-test"
client_id = "unused"
client_secret = "unused"
EOF

docker build --tag "$IMAGE_NAME" .

output="$(
    docker run --rm \
        --volume "$CONFIG_FILE:/app/config.py:ro" \
        "$IMAGE_NAME" \
        --help
)"

echo "$output"
echo "$output" | grep -F "twitch-recorder.py -u <username> -q <quality>" >/dev/null
