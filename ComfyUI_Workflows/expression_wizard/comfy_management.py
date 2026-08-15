from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ComfyManagementClient:
    """Read-only client for the desktop ComfyUI management endpoints."""

    def __init__(self, gateway_url: str, admin_token: str, timeout: float = 610.0):
        self.gateway_url = gateway_url.rstrip("/")
        self.admin_token = admin_token.strip()
        self.timeout = timeout
        if not self.gateway_url.startswith(("http://", "https://")):
            raise ValueError("ComfyUI gateway URL must use http:// or https://")
        if not self.admin_token:
            raise ValueError("A ComfyUI read-only admin token is required")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.gateway_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise RuntimeError("Management gateway returned a non-object response")
                return result
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(raw).get("error", raw)
            except Exception:
                message = raw
            raise RuntimeError(f"ComfyUI management HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"ComfyUI management gateway is unavailable at {self.gateway_url}") from exc

    def summary(self) -> dict[str, Any]:
        return self._request("GET", "/api/admin/summary")

    def models(self, query: str | None = None) -> dict[str, Any]:
        result = self._request("GET", "/api/admin/models")
        if query:
            query = query.lower()
            result["models"] = [item for item in result.get("models", []) if query in item.get("path", "").lower()]
            result["count"] = len(result["models"])
            result["query"] = query
        return result

    def nodes(self, query: str | None = None) -> dict[str, Any]:
        result = self._request("GET", "/api/admin/nodes")
        if query:
            query = query.lower()
            result["nodes"] = [item for item in result.get("nodes", []) if query in item.get("name", "").lower()]
            result["count"] = len(result["nodes"])
            result["query"] = query
        return result

    def inspect_model(self, path: str, include_sha256: bool = True) -> dict[str, Any]:
        query = urllib.parse.urlencode({"path": path, "sha256": str(include_sha256).lower()})
        return self._request("GET", f"/api/admin/model?{query}")

    def diagnose_workflow(self, workflow: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/admin/diagnose-workflow", {"workflow": workflow})
