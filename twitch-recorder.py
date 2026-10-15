"""Legacy entry point: ``twitch-recorder.py -u <username> -q <quality>``.

Kept so existing Docker Compose setups and the per-channel Helm Deployment
keep working. It runs ``recorder watch`` with values from ``config.py``.
"""

import sys

import config
from recorder import cli

if __name__ == "__main__":
    sys.exit(cli.main(["watch", *sys.argv[1:]], cli.Settings.from_config_module(config)))
