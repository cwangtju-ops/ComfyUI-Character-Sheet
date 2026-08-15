from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
WIZARD_DIR = HERE.parent
if str(WIZARD_DIR) not in sys.path:
    sys.path.insert(0, str(WIZARD_DIR))

import server  # noqa: E402
import wizard_core  # noqa: E402


class LanSecurityTests(unittest.TestCase):
    def test_loopback_detection(self) -> None:
        self.assertTrue(server.is_loopback_host("127.0.0.1"))
        self.assertTrue(server.is_loopback_host("::1"))
        self.assertTrue(server.is_loopback_host("localhost"))
        self.assertFalse(server.is_loopback_host("0.0.0.0"))
        self.assertFalse(server.is_loopback_host("192.168.1.10"))

    def test_token_file_is_created_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.txt"
            token, created = server.load_or_create_token(path)
            self.assertTrue(created)
            self.assertGreaterEqual(len(token), server.MINIMUM_TOKEN_LENGTH)
            repeated, created_again = server.load_or_create_token(path)
            self.assertFalse(created_again)
            self.assertEqual(token, repeated)

    def test_short_explicit_token_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                server.load_or_create_token(Path(directory) / "token.txt", "short")

    def test_sessions_expire_and_can_be_revoked(self) -> None:
        sessions = server.SessionStore(ttl_seconds=1)
        session_id = sessions.issue()
        self.assertTrue(sessions.valid(session_id))
        sessions.revoke(session_id)
        self.assertFalse(sessions.valid(session_id))
        expiring = sessions.issue()
        sessions._sessions[expiring] = time.time() - 1
        self.assertFalse(sessions.valid(expiring))

    def test_rest_request_uses_remote_url_and_bearer_token(self) -> None:
        response = mock.MagicMock()
        response.read.return_value = b'{"ok": true}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch.dict(os.environ, {"EXPRESSION_WIZARD_URL": "http://192.168.1.10:8765", "EXPRESSION_WIZARD_TOKEN": "secret-token"}, clear=False):
            with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
                self.assertEqual(wizard_core.rest_request("GET", "/api/explore/config"), {"ok": True})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://192.168.1.10:8765/api/explore/config")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")


if __name__ == "__main__":
    unittest.main()
