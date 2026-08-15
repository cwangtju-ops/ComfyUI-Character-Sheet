from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
STATIC_ROOT = HERE / "static"
DEFAULT_BACKEND = "http://127.0.0.1:8765"
MAX_PROXY_BODY = 30 * 1024 * 1024
REQUEST_HEADERS = {"accept", "content-type", "x-filename"}
RESPONSE_HEADERS = {"cache-control", "content-disposition", "content-type", "etag", "last-modified"}


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class ExpressionWizardDevHandler(BaseHTTPRequestHandler):
    server_version = "ExpressionWizardDevProxy/1.0"

    @property
    def backend_url(self) -> str:
        return self.server.backend_url  # type: ignore[attr-defined]

    @property
    def access_token(self) -> str:
        return self.server.access_token  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}", flush=True)

    def send_bytes(
        self,
        data: bytes,
        content_type: str,
        status: int = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            if name.lower() != "content-type":
                self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        self.send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def serve_static(self, request_path: str) -> None:
        relative = {
            "": "index.html",
            "/": "index.html",
            "/login": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/styles.css": "styles.css",
            "/static/app.js": "app.js",
            "/static/styles.css": "styles.css",
        }.get(request_path)
        if relative is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = STATIC_ROOT / relative
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_bytes(path.read_bytes(), content_type, headers={"Cache-Control": "no-store"})

    def read_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None
        length = int(raw_length)
        if length < 0 or length > MAX_PROXY_BODY:
            raise ValueError(f"Request body exceeds the {MAX_PROXY_BODY // (1024 * 1024)} MB development proxy limit")
        return self.rfile.read(length)

    def proxy(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            data = self.read_body()
            headers = {
                name: value
                for name, value in self.headers.items()
                if name.lower() in REQUEST_HEADERS
            }
            headers["Authorization"] = f"Bearer {self.access_token}"
            headers["User-Agent"] = self.server_version
            request = urllib.request.Request(
                self.backend_url + self.path,
                data=data,
                headers=headers,
                method=self.command,
            )
            try:
                response = urllib.request.urlopen(request, timeout=620)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                payload = response.read()
                forwarded = {
                    name: value
                    for name, value in response.headers.items()
                    if name.lower() in RESPONSE_HEADERS
                }
                self.send_bytes(
                    payload,
                    response.headers.get_content_type(),
                    response.status,
                    forwarded,
                )
        except urllib.error.URLError as exc:
            self.send_json(
                {"ok": False, "error": f"Expression Wizard backend is unavailable at {self.backend_url}: {exc.reason}"},
                HTTPStatus.BAD_GATEWAY,
            )
        except (ValueError, OSError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/dev/health":
            self.send_json({"ok": True, "service": "Expression Wizard UI development proxy", "backend": self.backend_url})
            return
        if parsed.path.startswith("/api/"):
            self.proxy()
            return
        self.serve_static(parsed.path)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        self.proxy()

    def do_PUT(self) -> None:
        self.proxy()

    def do_PATCH(self) -> None:
        self.proxy()

    def do_DELETE(self) -> None:
        self.proxy()


def build_server(host: str, port: int, backend_url: str, access_token: str) -> ThreadingHTTPServer:
    if not is_loopback_host(host):
        raise ValueError("The UI development proxy must bind to localhost or another loopback address")
    backend = backend_url.rstrip("/")
    parsed = urllib.parse.urlparse(backend)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("The backend URL must be an absolute http:// or https:// URL")
    if not access_token.strip():
        raise ValueError("EXPRESSION_WIZARD_TOKEN is required for the remote development backend")
    server = ThreadingHTTPServer((host, port), ExpressionWizardDevHandler)
    server.backend_url = backend  # type: ignore[attr-defined]
    server.access_token = access_token.strip()  # type: ignore[attr-defined]
    return server


def open_when_ready(url: str) -> None:
    def worker() -> None:
        for _ in range(50):
            try:
                with urllib.request.urlopen(url + "dev/health", timeout=0.25):
                    webbrowser.open(url)
                    return
            except Exception:
                time.sleep(0.1)

    threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the laptop's Expression Wizard UI and proxy API calls to a remote backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--backend", default=os.environ.get("EXPRESSION_WIZARD_URL") or DEFAULT_BACKEND)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("EXPRESSION_WIZARD_TOKEN", "")
    try:
        server = build_server(args.host, args.port, args.backend, token)
    except ValueError as exc:
        parser.error(str(exc))
    url = f"http://{args.host}:{server.server_port}/"
    print("Expression Wizard UI development proxy", flush=True)
    print(f"Laptop UI: {url}", flush=True)
    print(f"Desktop backend: {server.backend_url}", flush=True)  # type: ignore[attr-defined]
    print(f"Static files: {STATIC_ROOT}", flush=True)
    print("The access token stays in the proxy process and is not sent to the browser.", flush=True)
    print("Press Ctrl+C to stop", flush=True)
    if args.open_browser:
        open_when_ready(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
