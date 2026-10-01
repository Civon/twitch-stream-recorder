import contextlib
import importlib.util
import io
import pathlib
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).parents[1] / "twitch-recorder.py"


def load_recorder_module():
    config = types.ModuleType("config")
    config.root_path = "/tmp"
    config.username = "smoke-test"
    config.client_id = "unused"
    config.client_secret = "unused"
    requests = types.ModuleType("requests")
    requests.exceptions = types.SimpleNamespace(RequestException=Exception)

    spec = importlib.util.spec_from_file_location("twitch_recorder", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"config": config, "requests": requests}):
        spec.loader.exec_module(module)
    return module


class CommandLineSmokeTest(unittest.TestCase):
    def test_help_does_not_initialize_the_recorder(self):
        module = load_recorder_module()
        stdout = io.StringIO()

        with (
            mock.patch.object(
                module,
                "TwitchRecorder",
                side_effect=AssertionError("help must not initialize the recorder"),
            ),
            contextlib.redirect_stdout(stdout),
            self.assertRaises(SystemExit) as exit_context,
        ):
            module.main(["--help"])

        self.assertEqual(exit_context.exception.code, 0)
        self.assertIn(
            "twitch-recorder.py -u <username> -q <quality>",
            stdout.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
