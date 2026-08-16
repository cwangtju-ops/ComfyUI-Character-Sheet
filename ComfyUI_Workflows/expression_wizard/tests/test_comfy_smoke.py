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
        "CLIPSetLastLayer": {"input": {"required": {}}},
        "KSampler": {"input": {"required": {
            "sampler_name": [["dpmpp_2m", "dpmpp_2m_sde"], {}],
            "scheduler": [["karras"], {}],
        }}},
    }


def profile() -> dict:
    return {
        "profile_id": "test-pony-profile",
        "status": "verified",
        "display_name": "Test Pony",
        "checkpoint": {"filename": "pony.safetensors", "sha256": "a" * 64, "size_bytes": 1, "base_model": "Pony"},
        "sources": [],
        "generation": {"width": 832, "height": 1216, "steps": 30, "cfg": 5.0, "sampler_name": "dpmpp_2m", "scheduler": "karras", "clip_skip": 2, "vae": "baked"},
        "prompts": {"positive_prefix": "score_9, score_8_up, score_7_up", "default_subject": "portrait", "negative": "score_6, low quality"},
        "refinement": {"enabled_by_default": False, "stage": "final_refinement"},
    }


class ComfySmokeTests(unittest.TestCase):
    def test_builds_profile_native_prompt_with_clip_skip(self) -> None:
        prompt, config = build_smoke_prompt({"checkpoint": "pony.safetensors", "seed": 123}, object_info(), [profile()])
        self.assertEqual(list(prompt), ["1", "2", "3", "4", "5", "6", "7", "8"])
        self.assertEqual(prompt["2"]["class_type"], "CLIPSetLastLayer")
        self.assertEqual(prompt["2"]["inputs"]["stop_at_clip_layer"], -2)
        self.assertEqual(prompt["6"]["inputs"]["seed"], 123)
        self.assertEqual(prompt["8"]["class_type"], "SaveImage")
        self.assertTrue(prompt["8"]["inputs"]["filename_prefix"].startswith("Expression_Wizard/Smoke_Test/smoke_"))
        self.assertEqual(config["checkpoint"], "pony.safetensors")
        self.assertEqual((config["width"], config["height"]), (832, 1216))
        self.assertEqual(config["output_node_id"], "8")
        self.assertTrue(config["recommendation_match"])
        self.assertFalse(config["refinement"]["enabled"])

    def test_records_explicit_deviation_from_profile(self) -> None:
        _, config = build_smoke_prompt(
            {"checkpoint": "pony.safetensors", "width": 512, "steps": 12, "sampler_name": "dpmpp_2m_sde"},
            object_info(),
            [profile()],
        )
        self.assertFalse(config["recommendation_match"])
        self.assertEqual(config["overrides"]["width"], {"recommended": 832, "actual": 512})
        self.assertEqual(config["overrides"]["steps"], {"recommended": 30, "actual": 12})

    def test_rejects_checkpoint_and_resource_abuse(self) -> None:
        with self.assertRaisesRegex(ValueError, "not installed"):
            build_smoke_prompt({"checkpoint": "missing.safetensors"}, object_info(), [profile()])
        with self.assertRaisesRegex(ValueError, "No Model Profile"):
            build_smoke_prompt({"checkpoint": "pony.safetensors"}, object_info(), [])
        with self.assertRaisesRegex(ValueError, "divisible by 64"):
            build_smoke_prompt({"checkpoint": "pony.safetensors", "width": 513}, object_info(), [profile()])
        with self.assertRaisesRegex(ValueError, "steps"):
            build_smoke_prompt({"checkpoint": "pony.safetensors", "steps": 100}, object_info(), [profile()])
        with self.assertRaisesRegex(ValueError, "Unsupported sampler"):
            build_smoke_prompt({"checkpoint": "pony.safetensors", "sampler_name": "unsafe"}, object_info(), [profile()])


if __name__ == "__main__":
    unittest.main()
