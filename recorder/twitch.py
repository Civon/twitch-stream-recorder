"""Minimal Twitch Helix client (app access token)."""

from __future__ import annotations

import logging
from typing import Iterable

import requests

from recorder.metadata import StreamMetadata

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX_URL = "https://api.twitch.tv/helix"
MAX_LOGINS_PER_REQUEST = 100

log = logging.getLogger(__name__)


class HelixClient:
    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None,
                 timeout: float = 15):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self.timeout = timeout
        self._access_token: str | None = None

    def refresh_token(self) -> str:
        # Credentials go in the form body, not the URL, so they stay out of access logs.
        r = self.session.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }, timeout=self.timeout)
        r.raise_for_status()
        self._access_token = r.json()["access_token"]
        return self._access_token

    def _get(self, path: str, params) -> dict:
        if self._access_token is None:
            self.refresh_token()
        for attempt in (1, 2):
            headers = {"Client-ID": self.client_id, "Authorization": f"Bearer {self._access_token}"}
            r = self.session.get(f"{HELIX_URL}{path}", params=params, headers=headers, timeout=self.timeout)
            if r.status_code == 401 and attempt == 1:
                log.warning("Helix returned 401, refreshing app access token")
                self.refresh_token()
                continue
            r.raise_for_status()
            return r.json()
        raise AssertionError("unreachable")

    def get_streams(self, logins: Iterable[str]) -> dict[str, StreamMetadata]:
        """Live streams for the given logins, keyed by lower-case login.

        Offline or unknown logins are simply absent. One request covers up
        to 100 logins, so a controller watching many channels costs one call
        per poll instead of one per channel.
        """
        wanted = sorted({login.lower() for login in logins})
        live: dict[str, StreamMetadata] = {}
        for i in range(0, len(wanted), MAX_LOGINS_PER_REQUEST):
            batch = wanted[i:i + MAX_LOGINS_PER_REQUEST]
            body = self._get("/streams", [("user_login", login) for login in batch]
                             + [("first", str(MAX_LOGINS_PER_REQUEST))])
            for item in body.get("data") or []:
                meta = StreamMetadata.from_helix(item)
                live[meta.user_login] = meta
        return live

    def get_stream(self, login: str) -> StreamMetadata | None:
        return self.get_streams([login]).get(login.lower())
