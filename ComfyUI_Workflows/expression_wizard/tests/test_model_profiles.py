from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


WIZARD_DIR = Path(__file__).resolve().parents[1]
if str(WIZARD_DIR) not in sys.path:
    sys.path.insert(0, str(WIZARD_DIR))

from model_profiles import load_profiles, profile_for_checkpoint, validate_profile  # noqa: E402


class ModelProfileTests(unittest.TestCase):
    def test_builtin_semireal_v6_profile_is_verified_and_exact(self) -> None:
        profiles = load_profiles()
        profile = profile_for_checkpoint("cyberrealisticPony_semiRealV6.safetensors", profiles)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["status"], "verified")
        self.assertEqual(profile["checkpoint"]["sha256"], "1b25570a2849bb699dd78414758b8d95a4943130d02e6ae8b26222d68dac4af7")
        self.assertEqual((profile["generation"]["width"], profile["generation"]["height"]), (832, 1216))
        self.assertEqual(profile["generation"]["clip_skip"], 2)
        self.assertFalse(profile["refinement"]["enabled_by_default"])

    def test_profile_loader_rejects_default_upscale(self) -> None:
        profile = load_profiles()[0]
        value = json.loads(json.dumps({key: item for key, item in profile.items() if not key.startswith("_")}))
        value["refinement"]["enabled_by_default"] = True
        with self.assertRaisesRegex(ValueError, "disabled by default"):
            validate_profile(value)

    def test_profile_loader_rejects_duplicate_checkpoint(self) -> None:
        profile = load_profiles()[0]
        value = {key: item for key, item in profile.items() if not key.startswith("_")}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one.json").write_text(json.dumps(value), encoding="utf-8")
            duplicate = json.loads(json.dumps(value))
            duplicate["profile_id"] = "duplicate-profile"
            (root / "two.json").write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                load_profiles(root)


if __name__ == "__main__":
    unittest.main()
