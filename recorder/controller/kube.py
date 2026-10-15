"""The handful of Kubernetes API calls the controller needs, over plain HTTP.

In a pod it uses the service account token and CA. Outside a cluster it talks
to ``kubectl proxy`` at ``$K8S_API_URL`` (default http://127.0.0.1:8001), so
local runs need no kubeconfig parsing.
"""

from __future__ import annotations

import os
from typing import Any

import requests

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"Kubernetes API returned {status}: {body[:300]}")
        self.status = status
        self.body = body


class KubeClient:
    def __init__(self, base_url: str, token_path: str | None = None, verify: bool | str = True,
                 session: requests.Session | None = None, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.token_path = token_path
        self.verify = verify
        self.session = session or requests.Session()
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "KubeClient":
        host, port = os.environ.get("KUBERNETES_SERVICE_HOST"), os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        if host:
            if ":" in host:
                host = f"[{host}]"
            return cls(f"https://{host}:{port}", token_path=f"{SA_DIR}/token", verify=f"{SA_DIR}/ca.crt")
        return cls(os.environ.get("K8S_API_URL", "http://127.0.0.1:8001"))

    def _headers(self, content_type: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.token_path:
            # Re-read every call: projected service account tokens rotate on disk.
            with open(self.token_path, encoding="utf-8") as f:
                headers["Authorization"] = "Bearer " + f.read().strip()
        return headers

    def request(self, method: str, path: str, body: Any = None, params: dict | None = None,
                content_type: str | None = "application/json") -> dict:
        r = self.session.request(method, self.base_url + path, json=body, params=params,
                                 headers=self._headers(content_type if body is not None else None),
                                 verify=self.verify, timeout=self.timeout)
        if r.status_code >= 400:
            raise ApiError(r.status_code, r.text)
        return r.json() if r.content else {}

    def list_custom(self, group: str, version: str, namespace: str, plural: str) -> list[dict]:
        return self.request("GET", f"/apis/{group}/{version}/namespaces/{namespace}/{plural}").get("items", [])

    def patch_custom_status(self, group: str, version: str, namespace: str, plural: str, name: str,
                            status: dict) -> dict:
        return self.request("PATCH", f"/apis/{group}/{version}/namespaces/{namespace}/{plural}/{name}/status",
                            {"status": status}, content_type="application/merge-patch+json")

    def list_jobs(self, namespace: str, label_selector: str) -> list[dict]:
        return self.request("GET", f"/apis/batch/v1/namespaces/{namespace}/jobs",
                            params={"labelSelector": label_selector}).get("items", [])

    def create_job(self, namespace: str, job: dict) -> dict:
        return self.request("POST", f"/apis/batch/v1/namespaces/{namespace}/jobs", job)

    def create_event(self, namespace: str, event: dict) -> dict:
        return self.request("POST", f"/api/v1/namespaces/{namespace}/events", event)
