from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


WIZARD_DIR = Path(__file__).resolve().parents[1]
if str(WIZARD_DIR) not in sys.path:
    sys.path.insert(0, str(WIZARD_DIR))

from wizard_core import WizardService  # noqa: E402


class FakeSmokeTransport:
    def smoke_test(self, request: dict) -> tuple[str, dict, dict]:
        return "prompt-1", {"outputs": {"7": {"images": [{"filename": "result.png"}]}}}, {
            "test_id": "smoke_test_1",
            "checkpoint": request["checkpoint"],
        }

    def materialize_image(self, history: dict, destination: Path, node_id: str = "3") -> dict:
        self.node_id = node_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (512, 640), "blue").save(destination)
        return history["outputs"][node_id]["images"][0]


class WizardSmokeTests(unittest.TestCase):
    def test_smoke_result_is_kept_in_laptop_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            transport = FakeSmokeTransport()
            service = WizardService(Path(temp), transport=transport)
            result = service.run_smoke_test({"checkpoint": "pony.safetensors"})
            self.assertTrue(result["ok"])
            self.assertEqual(transport.node_id, "7")
            self.assertEqual((result["image"]["width"], result["image"]["height"]), (512, 640))
            self.assertTrue(Path(result["image"]["path"]).is_file())
            self.assertEqual(result["image"]["url"], "/api/manage/smoke-tests/smoke_test_1.png")


if __name__ == "__main__":
    unittest.main()
