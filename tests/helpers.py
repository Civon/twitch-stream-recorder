import subprocess

from recorder.metadata import StreamMetadata


def stream(category=("26936", "Music"), title="Singing stream", tags=("Singing", "English"), stream_id="111",
           login="kana", **kw):
    return StreamMetadata(stream_id=stream_id, user_id="42", user_login=login, category_id=category[0],
                          category_name=category[1], title=title, tags=tuple(tags), language="en",
                          is_mature=False, viewer_count=100, started_at="2026-10-15T08:00:00Z", **kw)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeProc:
    """streamlink stand-in: writes the capture file, stays 'running' for N waits."""

    def __init__(self, args, clock, waits, returncode=0, payload=b"video"):
        self.args = args
        self.clock = clock
        self.waits_left = waits
        self._returncode = returncode
        self.returncode = None
        self.terminated = False
        output = args[args.index("-o") + 1]
        with open(output, "wb") as f:
            f.write(payload)

    def wait(self, timeout=None):
        if self.waits_left > 0 and not self.terminated:
            self.waits_left -= 1
            self.clock.t += timeout
            raise subprocess.TimeoutExpired(self.args, timeout)
        self.returncode = self._returncode
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True


class FakeFfmpeg:
    """ffmpeg stand-in: copies the first input to the output path."""

    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.returncode == 0:
            src = cmd[cmd.index("-i") + 1]
            with open(src, "rb") as f, open(cmd[-1], "wb") as out:
                out.write(f.read())
        return subprocess.CompletedProcess(cmd, self.returncode)


class FakeHelix:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get_stream(self, login):
        self.calls += 1
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    def get_streams(self, logins):
        item = self.get_stream(None)
        return item
