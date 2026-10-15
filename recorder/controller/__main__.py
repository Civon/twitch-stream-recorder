"""``python -m recorder.controller``: run the reconcile loop until SIGTERM.

Environment:
  POD_NAMESPACE               namespace to watch (downward API)
  JOB_TEMPLATE_PATH           Job template JSON rendered by the Helm chart
  MAX_CONCURRENT_RECORDINGS   cap on running recordings, 0 = no cap
  POLL_INTERVAL               seconds between ticks (default 30, min 15)
  HEARTBEAT_PATH              file touched after each good tick (liveness probe)
  EVENT_SINKS                 stdout, log, none (default stdout)
  client_id / client_secret   Twitch app credentials
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading

from recorder.controller import COMPONENT
from recorder.controller.kube import KubeClient
from recorder.controller.reconciler import Reconciler, run_forever
from recorder.events import EventEmitter, runtime_fields, sinks_from_spec
from recorder.twitch import HelixClient

log = logging.getLogger("recorder.controller")


def main() -> int:
    env = os.environ
    logging.basicConfig(level=getattr(logging, env.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    namespace = env.get("POD_NAMESPACE")
    if not namespace or not env.get("client_id") or not env.get("client_secret"):
        log.error("POD_NAMESPACE, client_id and client_secret are required")
        return 2
    with open(env.get("JOB_TEMPLATE_PATH", "/etc/twitch-recorder/job-template.json"), encoding="utf-8") as f:
        template = json.load(f)

    interval = max(15.0, float(env.get("POLL_INTERVAL", "30")))
    reconciler = Reconciler(
        kube=KubeClient.from_env(),
        helix=HelixClient(env["client_id"], env["client_secret"]),
        namespace=namespace,
        job_template=template,
        emitter=EventEmitter(sinks_from_spec(env.get("EVENT_SINKS", "stdout")), runtime_fields(COMPONENT)),
        max_concurrent=int(env.get("MAX_CONCURRENT_RECORDINGS", "0")),
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    log.info("Watching TwitchChannels in %s every %ss", namespace, interval)
    run_forever(reconciler, interval, stop, env.get("HEARTBEAT_PATH", "/tmp/heartbeat"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
