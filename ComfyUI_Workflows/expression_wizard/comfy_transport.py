from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol
import json
import urllib.error
import urllib.parse
import urllib.request


HERE = Path(__file__).resolve().parent
CALIBRATION_DIR = HERE.parent / "calibration"
import sys

if str(CALIBRATION_DIR) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_DIR))

from lys_calibration import (  # noqa: E402
    API_DEFAULT,
    ComfyClient,
    copy_verified,
    output_image_from_history,
)


class ComfyTransport(Protocol):
    """The file and execution boundary between Expression Wizard and ComfyUI."""

    def get(self, path: str) -> dict[str, Any]:
        """Read an approved ComfyUI API resource."""

    def stage_input(self, source: Path, input_name: str) -> str:
        """Make source available to ComfyUI and return its workflow input name."""

    def execute(self, prompt: dict[str, Any], timeout: float) -> tuple[str, dict[str, Any]]:
        """Queue a prompt and wait for its history record."""

    def smoke_test(self, request: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Run the gateway's constrained core-node text-to-image workflow."""

    def materialize_image(self, history: dict[str, Any], destination: Path, node_id: str = "3") -> dict[str, Any]:
        """Copy the generated image into Expression Wizard storage."""

    def materialize_expression(self, expression_name: str, destination: Path) -> None:
        """Copy the generated .exp file into Expression Wizard storage."""


class LocalComfyTransport:
    """Transport for Expression Wizard and ComfyUI sharing one filesystem."""

    def __init__(self, api_url: str = API_DEFAULT, client: Any | None = None):
        self.client = client or ComfyClient(api_url)
        self._system_paths: tuple[Path, Path] | None = None

    def _paths(self) -> tuple[Path, Path]:
        if self._system_paths is None:
            input_root, output_root = self.client.system_paths()
            self._system_paths = (Path(input_root), Path(output_root))
        return self._system_paths

    def get(self, path: str) -> dict[str, Any]:
        return self.client.get(path)

    def stage_input(self, source: Path, input_name: str) -> str:
        input_root, _ = self._paths()
        copy_verified(source, input_root / Path(input_name))
        return input_name

    def execute(self, prompt: dict[str, Any], timeout: float = 300.0) -> tuple[str, dict[str, Any]]:
        prompt_id = self.client.queue(prompt)
        history = self.client.wait(prompt_id, timeout=timeout)
        return prompt_id, history

    def smoke_test(self, request: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        from comfy_smoke import build_smoke_prompt

        prompt, config = build_smoke_prompt(request, self.client.get("/object_info"))
        prompt_id, history = self.execute(prompt, timeout=600.0)
        return prompt_id, history, config

    def materialize_image(self, history: dict[str, Any], destination: Path, node_id: str = "3") -> dict[str, Any]:
        _, output_root = self._paths()
        output = output_image_from_history(history, node_id=node_id)
        generated = output_root / output.get("subfolder", "") / output["filename"]
        copy_verified(generated, destination)
        return output

    def materialize_expression(self, expression_name: str, destination: Path) -> None:
        _, output_root = self._paths()
        source = output_root / "exp_data" / f"{expression_name}.exp"
        copy_verified(source, destination)


class RemoteComfyTransport:
    """Authenticated transport to the small desktop ComfyUI gateway."""

    def __init__(self, gateway_url: str, access_token: str, request_timeout: float = 610.0):
        gateway_url = gateway_url.rstrip("/")
        access_token = access_token.strip()
        if not gateway_url.startswith(("http://", "https://")):
            raise ValueError("ComfyUI gateway URL must use http:// or https://")
        if not access_token:
            raise ValueError("A ComfyUI gateway access token is required")
        self.gateway_url = gateway_url
        self.access_token = access_token
        self.request_timeout = request_timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> Any:
        request_headers = {"Authorization": f"Bearer {self.access_token}", **(headers or {})}
        body = data
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.gateway_url + path, data=body, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if expect_json else raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(raw).get("error", raw)
            except Exception:
                message = raw
            raise RuntimeError(f"ComfyUI gateway HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"ComfyUI gateway is unavailable at {self.gateway_url}") from exc

    def get(self, path: str) -> dict[str, Any]:
        routes = {
            "/system_stats": "/api/system-stats",
            "/object_info": "/api/object-info",
        }
        if path.startswith("/object_info/"):
            node = urllib.parse.quote(path[len("/object_info/") :], safe="")
            gateway_path = f"/api/object-info?node={node}"
        else:
            gateway_path = routes.get(path)
        if not gateway_path:
            raise ValueError(f"Remote Comfy transport does not allow GET {path}")
        result = self._request("GET", gateway_path)
        if not isinstance(result, dict):
            raise RuntimeError("ComfyUI gateway returned an invalid JSON response")
        return result

    def stage_input(self, source: Path, input_name: str) -> str:
        result = self._request(
            "POST",
            "/api/input",
            data=source.read_bytes(),
            headers={"Content-Type": "application/octet-stream", "X-Input-Name": urllib.parse.quote(input_name, safe="/")},
        )
        return str(result["input_name"])

    def execute(self, prompt: dict[str, Any], timeout: float = 300.0) -> tuple[str, dict[str, Any]]:
        result = self._request("POST", "/api/execute", payload={"prompt": prompt, "timeout": timeout})
        return str(result["prompt_id"]), result["history"]

    def smoke_test(self, request: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        result = self._request("POST", "/api/smoke-test", payload=request)
        return str(result["prompt_id"]), result["history"], result["config"]

    def materialize_image(self, history: dict[str, Any], destination: Path, node_id: str = "3") -> dict[str, Any]:
        output = output_image_from_history(history, node_id=node_id)
        query = urllib.parse.urlencode({"filename": output["filename"], "subfolder": output.get("subfolder", "")})
        data = self._request("GET", f"/api/output/image?{query}", expect_json=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return output

    def materialize_expression(self, expression_name: str, destination: Path) -> None:
        name = urllib.parse.quote(expression_name, safe="")
        data = self._request("GET", f"/api/output/expression/{name}", expect_json=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
