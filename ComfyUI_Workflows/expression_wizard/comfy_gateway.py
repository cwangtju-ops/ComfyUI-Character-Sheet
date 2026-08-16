from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from typing import Any


HERE = Path(__file__).resolve().parent
CALIBRATION_DIR = HERE.parent / "calibration"
if str(CALIBRATION_DIR) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_DIR))

from lys_calibration import API_DEFAULT, ComfyClient, expression_payload  # noqa: E402
from comfy_diagnostics import (  # noqa: E402
    diagnose_workflow,
    discover_comfy_root,
    inspect_model,
    list_custom_nodes,
    list_models,
)
from comfy_smoke import build_smoke_prompt  # noqa: E402


MINIMUM_TOKEN_LENGTH = 24
MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_PROMPT_BYTES = 10 * 1024 * 1024
MAX_EXECUTION_SECONDS = 600.0
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
SAFE_NODE = re.compile(r"^[A-Za-z0-9_.-]+$")
ALLOWED_NODE_CLASSES = {"LoadImage", "ExpressionEditor", "SaveImage", "SaveExpData"}


def validate_token(token: str) -> str:
    token = token.strip()
    if len(token) < MINIMUM_TOKEN_LENGTH:
        raise ValueError(f"Gateway token must contain at least {MINIMUM_TOKEN_LENGTH} characters")
    return token


def load_or_create_token(path: Path, explicit: str | None = None) -> tuple[str, bool]:
    if explicit:
        return validate_token(explicit), False
    if path.is_file():
        return validate_token(path.read_text(encoding="utf-8")), False
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    return token, True


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def client_is_allowed(address: str, allowed_clients: set[str]) -> bool:
    try:
        if ip_address(address).is_loopback:
            return True
    except ValueError:
        return False
    return address in allowed_clients


def safe_file(root: Path, relative: str) -> Path:
    if "\\" in relative:
        raise ValueError("Backslashes are not allowed in gateway paths")
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Invalid gateway path")
    root = root.resolve()
    target = root.joinpath(*parts).resolve()
    if root not in target.parents:
        raise ValueError("Gateway path escapes its storage root")
    return target


def validate_prompt(prompt: dict[str, Any]) -> set[str]:
    expression_names: set[str] = set()
    for node in prompt.values():
        if not isinstance(node, dict) or node.get("class_type") not in ALLOWED_NODE_CLASSES or not isinstance(node.get("inputs"), dict):
            raise ValueError("Gateway accepts only Expression Wizard workflow nodes")
        class_type = node["class_type"]
        inputs = node["inputs"]
        if class_type == "LoadImage" and not str(inputs.get("image", "")).startswith("Expression_Wizard/"):
            raise ValueError("Gateway workflows may load only staged Expression_Wizard inputs")
        if class_type == "SaveImage" and not str(inputs.get("filename_prefix", "")).startswith("Expression_Wizard/"):
            raise ValueError("Gateway workflows may save only under Expression_Wizard")
        if class_type == "SaveExpData":
            name = str(inputs.get("file_name", ""))
            if not SAFE_NAME.fullmatch(name):
                raise ValueError("Invalid expression output name")
            expression_names.add(name)
    if not expression_names:
        raise ValueError("Gateway workflow must contain SaveExpData")
    return expression_names


class ComfyGatewayHandler(BaseHTTPRequestHandler):
    server_version = "ExpressionWizardComfyGateway/1.0"

    @property
    def client(self) -> Any:
        return self.server.comfy_client  # type: ignore[attr-defined]

    @property
    def input_root(self) -> Path:
        return self.server.input_root  # type: ignore[attr-defined]

    @property
    def output_root(self) -> Path:
        return self.server.output_root  # type: ignore[attr-defined]

    @property
    def models_root(self) -> Path:
        return self.server.models_root  # type: ignore[attr-defined]

    @property
    def custom_nodes_root(self) -> Path:
        return self.server.custom_nodes_root  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {format % args}", flush=True)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_failure(self, status: int, error: Exception | str) -> None:
        self.send_json({"ok": False, "error": str(error)}, status)

    def send_file(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(path.name)
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                self.wfile.write(chunk)

    def authorized(self, admin: bool = False) -> bool:
        if not client_is_allowed(self.client_address[0], self.server.allowed_clients):  # type: ignore[attr-defined]
            self.send_failure(HTTPStatus.FORBIDDEN, "Client IP is not allowed")
            return False
        authorization = self.headers.get("Authorization", "")
        token = self.server.admin_token if admin else self.server.access_token  # type: ignore[attr-defined]
        if not authorization.startswith("Bearer ") or not hmac.compare_digest(authorization[7:], token):
            self.send_failure(HTTPStatus.UNAUTHORIZED, "Administrator authentication required" if admin else "Authentication required")
            return False
        return True

    def read_json(self, maximum: int) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Request body is empty")
        if length > maximum:
            raise ValueError("Request body is too large")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def register_history_outputs(self, history: dict[str, Any], expression_names: set[str] | None = None) -> None:
        image_names = set()
        for output in history.get("outputs", {}).values():
            for image in output.get("images", []) if isinstance(output, dict) else []:
                filename = image.get("filename")
                subfolder = str(image.get("subfolder", "")).replace("\\", "/")
                if isinstance(filename, str) and PurePosixPath(filename).name == filename:
                    image_names.add(f"{subfolder}/{filename}" if subfolder else filename)
        with self.server.artifact_lock:  # type: ignore[attr-defined]
            self.server.allowed_images.update(image_names)  # type: ignore[attr-defined]
            self.server.allowed_expressions.update(expression_names or set())  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/health":
                self.send_json({"ok": True, "service": "Expression Wizard ComfyUI Gateway", "auth_required": True})
                return
            admin = parsed.path.startswith("/api/admin/")
            if not self.authorized(admin=admin):
                return
            if parsed.path == "/api/admin/summary":
                models = list_models(self.models_root)
                nodes = list_custom_nodes(self.custom_nodes_root)
                self.send_json(
                    {
                        "ok": True,
                        "mode": "read_only",
                        "comfy_root": str(self.server.comfy_root),  # type: ignore[attr-defined]
                        "model_count": len(models),
                        "custom_node_count": len(nodes),
                    }
                )
                return
            if parsed.path == "/api/admin/models":
                models = list_models(self.models_root)
                self.send_json({"models": models, "count": len(models)})
                return
            if parsed.path == "/api/admin/nodes":
                nodes = list_custom_nodes(self.custom_nodes_root)
                self.send_json({"nodes": nodes, "count": len(nodes)})
                return
            if parsed.path == "/api/admin/model":
                query = urllib.parse.parse_qs(parsed.query)
                relative = query.get("path", [""])[0]
                include_sha256 = query.get("sha256", ["true"])[0].lower() not in {"0", "false", "no"}
                self.send_json(inspect_model(self.models_root, relative, include_sha256=include_sha256))
                return
            if parsed.path == "/api/system-stats":
                self.send_json(self.client.get("/system_stats"))
                return
            if parsed.path == "/api/object-info":
                query = urllib.parse.parse_qs(parsed.query)
                node = query.get("node", [None])[0]
                if node is not None and not SAFE_NODE.fullmatch(node):
                    raise ValueError("Invalid ComfyUI node name")
                self.send_json(self.client.get(f"/object_info/{node}" if node else "/object_info"))
                return
            if parsed.path == "/api/output/image":
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                filename = query.get("filename", [""])[0]
                subfolder = query.get("subfolder", [""])[0].replace("\\", "/")
                if not filename or PurePosixPath(filename).name != filename:
                    raise ValueError("Invalid output filename")
                relative = f"{subfolder}/{filename}" if subfolder else filename
                with self.server.artifact_lock:  # type: ignore[attr-defined]
                    if relative not in self.server.allowed_images:  # type: ignore[attr-defined]
                        raise PermissionError("Image was not produced by this gateway session")
                self.send_file(safe_file(self.output_root, relative))
                return
            prefix = "/api/output/expression/"
            json_prefix = "/api/output/expression-json/"
            if parsed.path.startswith(json_prefix):
                expression_name = urllib.parse.unquote(parsed.path[len(json_prefix) :])
                if not SAFE_NAME.fullmatch(expression_name):
                    raise ValueError("Invalid expression name")
                with self.server.artifact_lock:  # type: ignore[attr-defined]
                    if expression_name not in self.server.allowed_expressions:  # type: ignore[attr-defined]
                        raise PermissionError("Expression was not produced by this gateway session")
                source = safe_file(self.output_root, f"exp_data/{expression_name}.exp")
                self.send_json(expression_payload(source))
                return
            if parsed.path.startswith(prefix):
                expression_name = urllib.parse.unquote(parsed.path[len(prefix) :])
                if not SAFE_NAME.fullmatch(expression_name):
                    raise ValueError("Invalid expression name")
                with self.server.artifact_lock:  # type: ignore[attr-defined]
                    if expression_name not in self.server.allowed_expressions:  # type: ignore[attr-defined]
                        raise PermissionError("Expression was not produced by this gateway session")
                self.send_file(safe_file(self.output_root, f"exp_data/{expression_name}.exp"))
                return
            self.send_failure(HTTPStatus.NOT_FOUND, "Unknown gateway endpoint")
        except FileNotFoundError as exc:
            self.send_failure(HTTPStatus.NOT_FOUND, exc)
        except PermissionError as exc:
            self.send_failure(HTTPStatus.FORBIDDEN, exc)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_failure(HTTPStatus.BAD_REQUEST, exc)
        except Exception as exc:
            self.send_failure(HTTPStatus.BAD_GATEWAY, exc)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            admin = parsed.path.startswith("/api/admin/")
            if not self.authorized(admin=admin):
                return
            if parsed.path == "/api/admin/diagnose-workflow":
                body = self.read_json(MAX_PROMPT_BYTES)
                workflow = body.get("workflow")
                if not isinstance(workflow, dict) or not workflow:
                    raise ValueError("Workflow must be a non-empty object")
                self.send_json(diagnose_workflow(workflow, self.client.get("/object_info")))
                return
            if parsed.path == "/api/input":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("Input upload is empty")
                if length > MAX_INPUT_BYTES:
                    raise ValueError("Input upload exceeds the 25 MB limit")
                input_name = urllib.parse.unquote(self.headers.get("X-Input-Name", ""))
                if Path(input_name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError("Input filename must use PNG, JPEG, or WebP")
                if not input_name.startswith("Expression_Wizard/"):
                    raise ValueError("Inputs must be staged under Expression_Wizard")
                destination = safe_file(self.input_root, input_name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(destination.suffix + ".uploading")
                try:
                    with temporary.open("wb") as output:
                        remaining = length
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ConnectionError("Input upload ended unexpectedly")
                            output.write(chunk)
                            remaining -= len(chunk)
                    temporary.replace(destination)
                finally:
                    if temporary.exists():
                        temporary.unlink()
                self.send_json({"ok": True, "input_name": input_name}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/execute":
                body = self.read_json(MAX_PROMPT_BYTES)
                prompt = body.get("prompt")
                if not isinstance(prompt, dict) or not prompt:
                    raise ValueError("Prompt must be a non-empty object")
                expression_names = validate_prompt(prompt)
                timeout = min(max(float(body.get("timeout", 300.0)), 1.0), MAX_EXECUTION_SECONDS)
                prompt_id = self.client.queue(prompt)
                history = self.client.wait(prompt_id, timeout=timeout)
                self.register_history_outputs(history, expression_names)
                self.send_json({"ok": True, "prompt_id": prompt_id, "history": history}, HTTPStatus.OK)
                return
            if parsed.path == "/api/smoke-test":
                body = self.read_json(MAX_PROMPT_BYTES)
                prompt, config = build_smoke_prompt(body, self.client.get("/object_info"))
                prompt_id = self.client.queue(prompt)
                history = self.client.wait(prompt_id, timeout=MAX_EXECUTION_SECONDS)
                self.register_history_outputs(history)
                self.send_json({"ok": True, "prompt_id": prompt_id, "history": history, "config": config}, HTTPStatus.OK)
                return
            self.send_failure(HTTPStatus.NOT_FOUND, "Unknown gateway endpoint")
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_failure(HTTPStatus.BAD_REQUEST, exc)
        except Exception as exc:
            self.send_failure(HTTPStatus.BAD_GATEWAY, exc)


def build_server(
    host: str,
    port: int,
    api_url: str,
    access_token: str,
    allowed_clients: set[str],
    client: Any | None = None,
    comfy_root: Path | None = None,
    admin_token: str | None = None,
) -> ThreadingHTTPServer:
    token = validate_token(access_token)
    admin_token = validate_token(admin_token or access_token)
    for address in allowed_clients:
        ip_address(address)
    if not is_loopback(host) and not allowed_clients:
        raise ValueError("At least one --allow-client address is required for LAN binding")
    comfy_client = client or ComfyClient(api_url)
    input_root, output_root = comfy_client.system_paths()
    discovered_root = discover_comfy_root(Path(input_root), Path(output_root), comfy_root)
    server = ThreadingHTTPServer((host, port), ComfyGatewayHandler)
    server.comfy_client = comfy_client  # type: ignore[attr-defined]
    server.input_root = Path(input_root).resolve()  # type: ignore[attr-defined]
    server.output_root = Path(output_root).resolve()  # type: ignore[attr-defined]
    server.comfy_root = discovered_root  # type: ignore[attr-defined]
    server.models_root = discovered_root / "models"  # type: ignore[attr-defined]
    server.custom_nodes_root = discovered_root / "custom_nodes"  # type: ignore[attr-defined]
    server.access_token = token  # type: ignore[attr-defined]
    server.admin_token = admin_token  # type: ignore[attr-defined]
    server.allowed_clients = allowed_clients  # type: ignore[attr-defined]
    server.allowed_images = set()  # type: ignore[attr-defined]
    server.allowed_expressions = set()  # type: ignore[attr-defined]
    server.artifact_lock = threading.RLock()  # type: ignore[attr-defined]
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Authenticated LAN gateway from Expression Wizard to local ComfyUI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8189)
    parser.add_argument("--api", default=API_DEFAULT, help="Local ComfyUI API URL")
    parser.add_argument("--comfy-root", help="ComfyUI installation root; auto-discovered from input/output folders when omitted")
    parser.add_argument("--allow-client", action="append", default=[], help="Laptop IPv4 allowed to use the gateway")
    parser.add_argument("--token-file", default=str(Path.home() / ".expression_wizard" / "comfy_gateway_token.txt"))
    parser.add_argument("--access-token", help="Explicit token; prefer token file or EXPRESSION_WIZARD_COMFY_TOKEN")
    parser.add_argument("--admin-token-file", default=str(Path.home() / ".expression_wizard" / "comfy_gateway_admin_token.txt"))
    parser.add_argument("--admin-token", help="Explicit read-only management token; prefer its token file")
    args = parser.parse_args()

    env_clients = [item.strip() for item in os.environ.get("EXPRESSION_WIZARD_ALLOWED_CLIENTS", "").split(",") if item.strip()]
    allowed_clients = set(args.allow_client + env_clients)
    explicit = args.access_token or os.environ.get("EXPRESSION_WIZARD_COMFY_TOKEN")
    token_file = Path(args.token_file).expanduser().resolve()
    token, created = load_or_create_token(token_file, explicit)
    admin_explicit = args.admin_token or os.environ.get("EXPRESSION_WIZARD_COMFY_ADMIN_TOKEN")
    admin_token_file = Path(args.admin_token_file).expanduser().resolve()
    admin_token, admin_created = load_or_create_token(admin_token_file, admin_explicit)
    try:
        root = Path(args.comfy_root).expanduser().resolve() if args.comfy_root else None
        server = build_server(args.host, args.port, args.api, token, allowed_clients, comfy_root=root, admin_token=admin_token)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    print("Expression Wizard ComfyUI Gateway", flush=True)
    print(f"ComfyUI: {args.api}", flush=True)
    print(f"Listen: {args.host}:{args.port}", flush=True)
    print(f"Allowed laptop IPs: {', '.join(sorted(allowed_clients))}", flush=True)
    print(f"ComfyUI root: {server.comfy_root}", flush=True)  # type: ignore[attr-defined]
    print(f"Access token: {token}", flush=True)
    print(f"Token file: {token_file}", flush=True)
    if created:
        print("A new persistent gateway token was created.", flush=True)
    print(f"Read-only admin token: {admin_token}", flush=True)
    print(f"Admin token file: {admin_token_file}", flush=True)
    if admin_created:
        print("A new persistent read-only admin token was created.", flush=True)
    print("Press Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
