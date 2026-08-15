from __future__ import annotations

import argparse
import json
import mimetypes
import posixpath
import sys
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CALIBRATION_DIR = HERE.parent / "calibration"
if str(CALIBRATION_DIR) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_DIR))

from review_server import ReviewHandler, discover_batches  # noqa: E402
from lys_calibration import ProjectPaths, read_json  # noqa: E402
from wizard_core import MAX_UPLOAD_BYTES, WizardService  # noqa: E402


STATIC_ROOT = HERE / "static"


class ExpressionWizardHandler(ReviewHandler):
    server_version = "ExpressionWizard/1.0"

    @property
    def wizard(self) -> WizardService:
        return self.server.wizard  # type: ignore[attr-defined]

    def send_error_json(self, status: int, error: Exception | str) -> None:
        self.send_json({"ok": False, "error": str(error)}, status)

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

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                self.serve_static("index.html")
                return
            if path.startswith("/static/"):
                self.serve_static(path[len("/static/") :])
                return
            if path == "/api/explore/health":
                self.send_json({"ok": True, "service": "Expression Wizard", "version": 1, "comfyui": self.wizard.comfy_status()})
                return
            if path == "/api/explore/config":
                self.send_json(self.wizard.config())
                return
            if path == "/api/explore/jobs":
                self.send_json({"jobs": self.wizard.list_jobs()})
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

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path
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
    parser = argparse.ArgumentParser(description="Expression Wizard local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--api", default="http://127.0.0.1:8188")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    paths = ProjectPaths.discover(CALIBRATION_DIR / "lys_calibration.py")
    wizard = WizardService(paths.lys_root, args.api)
    server = ThreadingHTTPServer((args.host, args.port), ExpressionWizardHandler)
    server.wizard = wizard  # type: ignore[attr-defined]
    server.batches = discover_batches(paths)  # type: ignore[attr-defined]
    url = f"http://{args.host}:{args.port}/"
    print("Expression Wizard")
    print(f"Explore: {url}")
    print(f"ComfyUI: {args.api}")
    print("Press Ctrl+C to stop")
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
