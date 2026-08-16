from __future__ import annotations

import sys
import unittest
from pathlib import Path


WIZARD_DIR = Path(__file__).resolve().parents[1]
if str(WIZARD_DIR) not in sys.path:
    sys.path.insert(0, str(WIZARD_DIR))

from comfy_smoke import build_smoke_prompt  # noqa: E402


def object_info() -> dict:
    return {
        "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["pony.safetensors"], {}]}}},
        "KSampler": {"input": {"required": {
            "sampler_name": [["dpmpp_2m_sde"], {}],
            "scheduler": [["karras"], {}],
        }}},
    }


class ComfySmokeTests(unittest.TestCase):
    def test_builds_fixed_seven_node_prompt(self) -> None:
        prompt, config = build_smoke_prompt({"checkpoint": "pony.safetensors", "seed": 123}, object_info())
        self.assertEqual(list(prompt), ["1", "2", "3", "4", "5", "6", "7"])
        self.assertEqual(prompt["5"]["inputs"]["seed"], 123)
        self.assertEqual(prompt["7"]["class_type"], "SaveImage")
        self.assertTrue(prompt["7"]["inputs"]["filename_prefix"].startswith("Expression_Wizard/Smoke_Test/smoke_"))
        self.assertEqual(config["checkpoint"], "pony.safetensors")

    def test_rejects_checkpoint_and_resource_abuse(self) -> None:
        with self.assertRaisesRegex(ValueError, "not installed"):
            build_smoke_prompt({"checkpoint": "missing.safetensors"}, object_info())
        with self.assertRaisesRegex(ValueError, "divisible by 64"):
            build_smoke_prompt({"checkpoint": "pony.safetensors", "width": 513}, object_info())
        with self.assertRaisesRegex(ValueError, "steps"):
            build_smoke_prompt({"checkpoint": "pony.safetensors", "steps": 100}, object_info())
        with self.assertRaisesRegex(ValueError, "Unsupported sampler"):
            build_smoke_prompt({"checkpoint": "pony.safetensors", "sampler_name": "unsafe"}, object_info())


if __name__ == "__main__":
    unittest.main()
