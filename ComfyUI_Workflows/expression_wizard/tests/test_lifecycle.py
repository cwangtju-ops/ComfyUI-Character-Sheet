from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace


WIZARD_DIR = Path(__file__).resolve().parents[1]
if str(WIZARD_DIR) not in sys.path:
    sys.path.insert(0, str(WIZARD_DIR))

from server import ExpressionWizardHandler, SessionStore  # noqa: E402


CONTROL_TOKEN = "lifecycle-test-token-with-24-characters"


class FakeWizard:
    def __init__(self, active: bool = False):
        self.active = active
        self.cancelled = []
        self.paths = SimpleNamespace(root=Path.cwd(), assets=Path.cwd(), lys_root=Path.cwd())

    def active_jobs(self) -> list[dict]:
        return [{"job_id": "active-job", "status": "running"}] if self.active else []

    def cancel_active_jobs(self) -> list[str]:
        if not self.active:
            return []
        self.active = False
        self.cancelled.append("active-job")
        return ["active-job"]

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        return not self.active

    def comfy_status(self) -> dict:
        return {"online": True}


def request(url: str, path: str, method: str = "GET", body: dict | None = None, token: str = CONTROL_TOKEN):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    return urllib.request.urlopen(urllib.request.Request(url + path, data=data, headers=headers, method=method), timeout=2)


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ExpressionWizardHandler)
        self.server.wizard = FakeWizard()
        self.server.batches = []
        self.server.access_token = None
        self.server.sessions = SessionStore()
        self.server.lan_mode = False
        self.server.management = None
        self.server.control_token = CONTROL_TOKEN
        self.server.shutdown_requested = False
        self.server.idle_timeout_seconds = 0
        self.server.last_browser_activity = time.monotonic()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()

    def test_status_requires_control_authentication(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as denied:
            request(self.url, "/api/lifecycle/status", token="wrong-token-with-at-least-24-characters")
        self.assertEqual(denied.exception.code, 401)
        with request(self.url, "/api/lifecycle/status") as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["service"], "Expression Wizard")
        self.assertEqual(payload["active_jobs"], [])

    def test_shutdown_refuses_active_job_without_explicit_cancel(self) -> None:
        self.server.wizard.active = True
        with self.assertRaises(urllib.error.HTTPError) as busy:
            request(self.url, "/api/lifecycle/shutdown", method="POST", body={"cancel_active": False})
        self.assertEqual(busy.exception.code, 409)
        self.assertFalse(self.server.shutdown_requested)
        self.assertEqual(self.server.wizard.cancelled, [])

    def test_explicit_cancel_requests_graceful_shutdown(self) -> None:
        self.server.wizard.active = True
        with request(self.url, "/api/lifecycle/shutdown", method="POST", body={"cancel_active": True}) as response:
            payload = json.loads(response.read())
        self.assertTrue(payload["shutting_down"])
        self.assertEqual(payload["cancelled_jobs"], ["active-job"])
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())


if __name__ == "__main__":
    unittest.main()
