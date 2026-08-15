from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import posixpath
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CALIBRATION_DIR = HERE.parent / "calibration"
if str(CALIBRATION_DIR) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_DIR))

from review_server import ReviewHandler, discover_batches  # noqa: E402
from lys_calibration import ProjectPaths  # noqa: E402
from wizard_core import MAX_UPLOAD_BYTES, WizardService  # noqa: E402
from comfy_transport import RemoteComfyTransport  # noqa: E402
from comfy_management import ComfyManagementClient  # noqa: E402


STATIC_ROOT = HERE / "static"
SESSION_COOKIE = "expression_wizard_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
MINIMUM_TOKEN_LENGTH = 24


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def lan_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = item[4][0]
            parsed = ip_address(address)
            if parsed.is_private and not parsed.is_loopback and not parsed.is_link_local:
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


def validate_access_token(token: str) -> str:
    token = token.strip()
    if len(token) < MINIMUM_TOKEN_LENGTH:
        raise ValueError(f"Access tokens must contain at least {MINIMUM_TOKEN_LENGTH} characters")
    return token


def load_or_create_token(path: Path, explicit: str | None = None) -> tuple[str, bool]:
    if explicit:
        return validate_access_token(explicit), False
    if path.is_file():
        return validate_access_token(path.read_text(encoding="utf-8")), False
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token, True


def project_paths_with_data_root(paths: ProjectPaths, data_root: Path) -> ProjectPaths:
    data_root = data_root.expanduser().resolve()
    if not data_root.is_dir():
        raise ValueError(f"Expression Wizard data root does not exist: {data_root}")
    return ProjectPaths(
        calibration_dir=paths.calibration_dir,
        workflow_dir=paths.workflow_dir,
        lys_root=data_root,
        generated_root=data_root / "ComfyUI_Generated" / "Calibration",
        specs_dir=paths.specs_dir,
        recipe_library=paths.recipe_library,
    )

class SessionStore:
    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, float] = {}
        self._lock = threading.RLock()

    def issue(self) -> str:
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[session_id] = time.time() + self.ttl_seconds
        return session_id

    def valid(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        now = time.time()
        with self._lock:
            expires = self._sessions.get(session_id)
            if expires is None:
                return False
            if expires <= now:
                self._sessions.pop(session_id, None)
                return False
            self._sessions[session_id] = now + self.ttl_seconds
            return True

    def revoke(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)


class ExpressionWizardHandler(ReviewHandler):
    server_version = "ExpressionWizard/1.1"

    @property
    def wizard(self) -> WizardService:
        return self.server.wizard  # type: ignore[attr-defined]

    @property
    def access_token(self) -> str | None:
        return self.server.access_token  # type: ignore[attr-defined]

    @property
    def sessions(self) -> SessionStore:
        return self.server.sessions  # type: ignore[attr-defined]

    @property
    def management(self) -> ComfyManagementClient:
        client = self.server.management  # type: ignore[attr-defined]
        if client is None:
            raise ConnectionError("Read-only ComfyUI management is not configured")
        return client

    def send_error_json(self, status: int, error: Exception | str) -> None:
        self.send_json({"ok": False, "error": str(error)}, status)

    def send_json_headers(self, payload: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def read_json_body(self, maximum: int = 2 * 1024 * 1024) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("Request body is empty")
            if length > maximum:
                raise ValueError("Request body is too large")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc

    def serve_static(self, relative: str) -> None:
        clean = posixpath.normpath("/" + relative).lstrip("/")
        target = (STATIC_ROOT / Path(clean)).resolve()
        if target != STATIC_ROOT.resolve() and STATIC_ROOT.resolve() not in target.parents:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_bytes(target.read_bytes(), mime)

    def serve_file_under(self, root: Path, relative: str) -> None:
        root = root.resolve()
        clean = posixpath.normpath("/" + relative).lstrip("/")
        target = (root / Path(clean)).resolve()
        if target != root and root not in target.parents:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_bytes(target.read_bytes(), mime)

    @staticmethod
    def explore_parts(path: str) -> list[str]:
        prefix = "/api/explore/"
        if not path.startswith(prefix):
            return []
        return [urllib.parse.unquote(part) for part in path[len(prefix) :].split("/") if part]

    def session_id(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            morsel = cookie.get(SESSION_COOKIE)
            return morsel.value if morsel else None
        except Exception:
            return None

    def authorized(self) -> bool:
        if self.access_token is None:
            return True
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer ") and hmac.compare_digest(authorization[7:], self.access_token):
            return True
        return self.sessions.valid(self.session_id())

    def require_authorized(self, path: str) -> bool:
        if self.authorized():
            return False
        if path.startswith("/api/"):
            self.send_json_headers({"ok": False, "error": "Authentication required"}, HTTPStatus.UNAUTHORIZED)
        else:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        return True

    def valid_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urllib.parse.urlparse(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc == self.headers.get("Host")

    def require_valid_origin(self) -> bool:
        if self.valid_origin():
            return False
        self.send_json_headers({"ok": False, "error": "Cross-origin request rejected"}, HTTPStatus.FORBIDDEN)
        return True

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/explore/health":
                payload: dict[str, Any] = {
                    "ok": True,
                    "service": "Expression Wizard",
                    "version": 2,
                    "auth_required": self.access_token is not None,
                    "authenticated": self.authorized(),
                    "lan_mode": bool(self.server.lan_mode),  # type: ignore[attr-defined]
                }
                if self.authorized():
                    payload["comfyui"] = self.wizard.comfy_status()
                self.send_json_headers(payload)
                return
            if path == "/api/auth/status":
                self.send_json_headers({"auth_required": self.access_token is not None, "authenticated": self.authorized()})
                return
            if path == "/login":
                if self.access_token is None or self.authorized():
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", "/")
                    self.end_headers()
                else:
                    self.serve_static("login.html")
                return
            if path.startswith("/static/"):
                self.serve_static(path[len("/static/") :])
                return
            if self.require_authorized(path):
                return
            if path == "/":
                self.serve_static("index.html")
                return
            if path == "/api/explore/config":
                self.send_json(self.wizard.config())
                return
            if path == "/api/explore/jobs":
                self.send_json({"jobs": self.wizard.list_jobs()})
                return
            if path == "/api/manage/summary":
                self.send_json(self.management.summary())
                return
            if path == "/api/manage/models":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("query", [None])[0]
                self.send_json(self.management.models(query))
                return
            if path == "/api/manage/nodes":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("query", [None])[0]
                self.send_json(self.management.nodes(query))
                return
            if path == "/api/manage/model":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                relative = query.get("path", [""])[0]
                include_sha256 = query.get("sha256", ["true"])[0].lower() not in {"0", "false", "no"}
                self.send_json(self.management.inspect_model(relative, include_sha256))
                return
            parts = self.explore_parts(path)
            if len(parts) == 2 and parts[0] == "sources":
                self.serve_file_under(self.wizard.paths.lys_root, {"anchor_1": "anchor 1.png", "anchor_2": "anchor 2.png", "anchor_3": "anchor 3.png"}.get(parts[1], "__missing__"))
                return
            if len(parts) == 2 and parts[0] == "assets":
                self.serve_file_under(self.wizard.paths.assets, f"{parts[1]}.png")
                return
            if len(parts) >= 2 and parts[0] == "jobs":
                job_id = parts[1]
                if len(parts) == 2:
                    self.send_json(self.wizard.get_job(job_id))
                    return
                if len(parts) == 3 and parts[2] == "analysis":
                    self.send_json(self.wizard.analysis(job_id))
                    return
                if len(parts) >= 4 and parts[2] == "files":
                    self.serve_file_under(self.wizard.paths.job_dir(job_id), "/".join(parts[3:]))
                    return
                if len(parts) == 5 and parts[2] == "candidates" and parts[4] == "workflow":
                    self.send_json(self.wizard.workflow(job_id, parts[3]))
                    return
            super().do_GET()
        except FileNotFoundError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, exc)
        except (ValueError, KeyError) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, exc)
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, exc)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/auth/login":
                if self.access_token is None:
                    self.send_json_headers({"ok": True, "authenticated": True})
                    return
                if self.require_valid_origin():
                    return
                supplied = str(self.read_json_body(maximum=4096).get("token", ""))
                if not hmac.compare_digest(supplied, self.access_token):
                    time.sleep(0.25)
                    self.send_json_headers({"ok": False, "error": "Invalid access token"}, HTTPStatus.UNAUTHORIZED)
                    return
                session_id = self.sessions.issue()
                cookie = f"{SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL_SECONDS}"
                self.send_json_headers({"ok": True, "authenticated": True}, headers={"Set-Cookie": cookie})
                return
            if self.require_authorized(path) or self.require_valid_origin():
                return
            if path == "/api/auth/logout":
                self.sessions.revoke(self.session_id())
                cookie = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                self.send_json_headers({"ok": True}, headers={"Set-Cookie": cookie})
                return
            if path == "/api/explore/assets":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("Upload is empty")
                if length > MAX_UPLOAD_BYTES:
                    raise ValueError("Upload exceeds the 25 MB limit")
                filename = urllib.parse.unquote(self.headers.get("X-Filename", "portrait"))
                self.send_json(self.wizard.add_upload(self.rfile.read(length), filename), HTTPStatus.CREATED)
                return
            if path == "/api/explore/jobs":
                try:
                    result = self.wizard.create_job(self.read_json_body())
                except RuntimeError as exc:
                    self.send_error_json(HTTPStatus.CONFLICT, exc)
                    return
                except ConnectionError as exc:
                    self.send_error_json(HTTPStatus.SERVICE_UNAVAILABLE, exc)
                    return
                self.send_json(result, HTTPStatus.ACCEPTED)
                return
            if path == "/api/explore/validate-workflow":
                body = self.read_json_body(maximum=10 * 1024 * 1024)
                self.send_json(self.wizard.validate_workflow(body.get("workflow")))
                return
            if path == "/api/manage/diagnose-workflow":
                body = self.read_json_body(maximum=10 * 1024 * 1024)
                workflow = body.get("workflow")
                if not isinstance(workflow, dict) or not workflow:
                    raise ValueError("Workflow must be a non-empty object")
                self.send_json(self.management.diagnose_workflow(workflow))
                return
            parts = self.explore_parts(path)
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "retry":
                try:
                    result = self.wizard.retry_job(parts[1])
                except RuntimeError as exc:
                    self.send_error_json(HTTPStatus.CONFLICT, exc)
                    return
                self.send_json(result, HTTPStatus.ACCEPTED)
                return
            super().do_POST()
        except FileNotFoundError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, exc)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, exc)
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, exc)

    def do_PUT(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if self.require_authorized(path) or self.require_valid_origin():
            return
        super().do_PUT()

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if self.require_authorized(path) or self.require_valid_origin():
            return
        parts = self.explore_parts(path)
        try:
            if len(parts) == 2 and parts[0] == "jobs":
                self.send_json(self.wizard.cancel_job(parts[1]), HTTPStatus.ACCEPTED)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except FileNotFoundError as exc:
            self.send_error_json(HTTPStatus.NOT_FOUND, exc)
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, exc)
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, exc)


def open_when_ready(url: str) -> None:
    def worker() -> None:
        for _ in range(50):
            try:
                with urllib.request.urlopen(url + "api/explore/health", timeout=0.25):
                    webbrowser.open(url)
                    return
            except Exception:
                time.sleep(0.1)

    import urllib.request

    threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Expression Wizard local/LAN server")
    parser.add_argument("--host", default=None, help="Bind address; defaults to 127.0.0.1 or 0.0.0.0 with --lan")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--api", default="http://127.0.0.1:8188")
    parser.add_argument("--comfy-gateway", help="Remote desktop gateway URL; may also use EXPRESSION_WIZARD_COMFY_URL")
    parser.add_argument("--comfy-token", help="Remote gateway token; prefer EXPRESSION_WIZARD_COMFY_TOKEN")
    parser.add_argument("--data-root", help="Folder containing anchors and ComfyUI_Generated; may also use EXPRESSION_WIZARD_DATA_ROOT")
    parser.add_argument("--lan", action="store_true", help="Listen on the LAN and require token authentication")
    parser.add_argument("--token-file", help="Persistent access-token file; generated when missing")
    parser.add_argument("--access-token", help="Explicit access token (prefer the token file or environment variable)")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()

    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    paths = ProjectPaths.discover(CALIBRATION_DIR / "lys_calibration.py")
    data_root_text = args.data_root or os.environ.get("EXPRESSION_WIZARD_DATA_ROOT")
    if data_root_text:
        try:
            paths = project_paths_with_data_root(paths, Path(data_root_text))
        except ValueError as exc:
            parser.error(str(exc))
    gateway_url = args.comfy_gateway or os.environ.get("EXPRESSION_WIZARD_COMFY_URL")
    if gateway_url:
        gateway_token = args.comfy_token or os.environ.get("EXPRESSION_WIZARD_COMFY_TOKEN", "")
        try:
            transport = RemoteComfyTransport(gateway_url, gateway_token)
        except ValueError as exc:
            parser.error(str(exc))
        wizard = WizardService(paths.lys_root, gateway_url, transport)
    else:
        wizard = WizardService(paths.lys_root, args.api)
    lan_mode = not is_loopback_host(host)
    token: str | None = None
    token_file = Path(args.token_file).expanduser().resolve() if args.token_file else wizard.paths.root / "_server" / "access_token.txt"
    explicit_token = args.access_token or os.environ.get("EXPRESSION_WIZARD_ACCESS_TOKEN")
    created = False
    if args.lan or lan_mode or explicit_token or args.token_file:
        token, created = load_or_create_token(token_file, explicit_token)

    server = ThreadingHTTPServer((host, args.port), ExpressionWizardHandler)
    server.wizard = wizard  # type: ignore[attr-defined]
    server.batches = discover_batches(paths)  # type: ignore[attr-defined]
    server.access_token = token  # type: ignore[attr-defined]
    server.sessions = SessionStore()  # type: ignore[attr-defined]
    server.lan_mode = lan_mode  # type: ignore[attr-defined]
    admin_token = os.environ.get("EXPRESSION_WIZARD_COMFY_ADMIN_TOKEN", "")
    server.management = ComfyManagementClient(gateway_url, admin_token) if gateway_url and admin_token else None  # type: ignore[attr-defined]

    local_url = f"http://127.0.0.1:{args.port}/"
    print("Expression Wizard", flush=True)
    print(f"Local: {local_url}", flush=True)
    if lan_mode:
        addresses = lan_ipv4_addresses()
        if addresses:
            for address in addresses:
                print(f"Laptop: http://{address}:{args.port}/", flush=True)
        else:
            print("Laptop: use this desktop's private IPv4 address", flush=True)
    print(f"Data root: {paths.lys_root}", flush=True)
    print(f"ComfyUI: {gateway_url or args.api}", flush=True)
    print(f"Comfy transport: {'remote gateway' if gateway_url else 'local filesystem'}", flush=True)
    if token:
        print(f"Access token: {token}", flush=True)
        if explicit_token:
            print("Token source: command/environment", flush=True)
        else:
            print(f"Token file: {token_file}", flush=True)
            if created:
                print("A new persistent LAN token was created.", flush=True)
    print("Press Ctrl+C to stop", flush=True)
    if args.open_browser:
        open_when_ready(local_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
