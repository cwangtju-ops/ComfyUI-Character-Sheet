from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HERE = Path(__file__).resolve().parent
WIZARD_DIR = HERE.parent
if str(WIZARD_DIR) not in sys.path:
    sys.path.insert(0, str(WIZARD_DIR))

from dev_proxy import build_server  # noqa: E402


class MockBackendHandler(BaseHTTPRequestHandler):
    last_headers: dict[str, str] = {}

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        type(self).last_headers = {name.lower(): value for name, value in self.headers.items()}
        payload = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class DevProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = ThreadingHTTPServer(("127.0.0.1", 0), MockBackendHandler)
        self.backend_thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.backend_thread.start()
        backend_url = f"http://127.0.0.1:{self.backend.server_port}"
        self.proxy = build_server("127.0.0.1", 0, backend_url, "test-token-value")
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.proxy_thread.start()
        self.proxy_url = f"http://127.0.0.1:{self.proxy.server_port}"

    def tearDown(self) -> None:
        self.proxy.shutdown()
        self.proxy.server_close()
        self.backend.shutdown()
        self.backend.server_close()

    def test_proxy_injects_bearer_and_strips_browser_origin(self) -> None:
        request = urllib.request.Request(
            self.proxy_url + "/api/explore/config",
            headers={"Origin": self.proxy_url},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            result = json.loads(response.read())
        self.assertTrue(result["ok"])
        self.assertEqual(MockBackendHandler.last_headers["authorization"], "Bearer test-token-value")
        self.assertNotIn("origin", MockBackendHandler.last_headers)

    def test_proxy_serves_html_static_paths_without_cache(self) -> None:
        with urllib.request.urlopen(self.proxy_url + "/static/app.js", timeout=2) as response:
            payload = response.read().decode("utf-8")
            cache_control = response.headers["Cache-Control"]
        self.assertIn("initialize()", payload)
        self.assertEqual(cache_control, "no-store")
        with urllib.request.urlopen(self.proxy_url + "/static/styles.css", timeout=2) as response:
            css = response.read().decode("utf-8")
        self.assertIn(":root", css)

    def test_proxy_refuses_non_loopback_binding(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            build_server("0.0.0.0", 0, "http://127.0.0.1:8765", "test-token-value")


if __name__ == "__main__":
    unittest.main()
