from recorder.twitch import HelixClient


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, get_statuses=()):
        self.posts = []
        self.gets = []
        self.get_statuses = list(get_statuses)

    def post(self, url, data=None, timeout=None):
        self.posts.append(data)
        return Resp(200, {"access_token": f"tok{len(self.posts)}"})

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append((params, headers))
        status = self.get_statuses.pop(0) if self.get_statuses else 200
        logins = [v for k, v in params if k == "user_login"]
        data = [{"id": "1", "user_login": logins[0], "game_id": "26936", "game_name": "Music"}]
        return Resp(status, {"data": data})


def test_get_streams_batches_100_logins_per_call():
    session = FakeSession()
    live = HelixClient("id", "secret", session=session).get_streams([f"user{i}" for i in range(150)] + ["USER0"])
    assert len(session.gets) == 2
    assert sum(1 for k, _ in session.gets[0][0] if k == "user_login") == 100
    assert "user0" in live and live["user0"].category_name == "Music"


def test_401_refreshes_token_once_and_retries():
    session = FakeSession(get_statuses=[401, 200])
    assert HelixClient("id", "secret", session=session).get_stream("kana") is not None
    assert len(session.posts) == 2
    assert session.gets[-1][1]["Authorization"] == "Bearer tok2"


def test_credentials_are_sent_in_body_not_url():
    session = FakeSession()
    HelixClient("id", "secret", session=session).refresh_token()
    assert session.posts[0] == {"client_id": "id", "client_secret": "secret", "grant_type": "client_credentials"}
