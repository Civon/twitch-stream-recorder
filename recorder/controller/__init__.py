"""Kubernetes controller: TwitchChannel resources in, one recording Job per live broadcast out."""

GROUP = "recorder.civon.github.io"
VERSION = "v1alpha1"
KIND = "TwitchChannel"
PLURAL = "twitchchannels"

COMPONENT = "twitch-recorder-controller"
LABEL_MANAGED_BY = "app.kubernetes.io/managed-by"
LABEL_CHANNEL = f"{GROUP}/channel"
LABEL_STREAM_ID = f"{GROUP}/stream-id"
