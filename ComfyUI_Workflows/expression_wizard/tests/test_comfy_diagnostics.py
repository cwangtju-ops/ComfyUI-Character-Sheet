from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


WIZARD_DIR = Path(__file__).resolve().parents[1]
if str(WIZARD_DIR) not in sys.path:
    sys.path.insert(0, str(WIZARD_DIR))

from comfy_diagnostics import (  # noqa: E402
    diagnose_workflow,
    discover_comfy_root,
    inspect_model,
    list_custom_nodes,
    list_models,
    validate_safetensors,
)


def write_safetensors(path: Path, data: bytes, declared_end: int | None = None) -> None:
    header = json.dumps(
        {"tensor": {"dtype": "U8", "shape": [len(data)], "data_offsets": [0, len(data) if declared_end is None else declared_end]}}
    ).encode("utf-8")
    path.write_bytes(len(header).to_bytes(8, "little") + header + data)


class ComfyDiagnosticsTests(unittest.TestCase):
    def test_discovers_inventory_and_git_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "ComfyUI"
            input_root = root / "input"
            output_root = root / "output"
            models = root / "models" / "liveportrait"
            node = root / "custom_nodes" / "ExampleNode"
            models.mkdir(parents=True)
            input_root.mkdir()
            output_root.mkdir()
            (node / ".git" / "refs" / "heads").mkdir(parents=True)
            (node / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (node / ".git" / "refs" / "heads" / "main").write_text("abc123\n", encoding="utf-8")
            (node / "requirements.txt").write_text("Pillow\n", encoding="utf-8")
            write_safetensors(models / "valid.safetensors", b"1234")

            self.assertEqual(discover_comfy_root(input_root, output_root), root.resolve())
            self.assertEqual(list_models(root / "models")[0]["path"], "liveportrait/valid.safetensors")
            nodes = list_custom_nodes(root / "custom_nodes")
            self.assertEqual(nodes[0]["git_revision"], "abc123")
            self.assertTrue(nodes[0]["has_requirements"])

    def test_safetensors_validation_detects_incomplete_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid = root / "valid.safetensors"
            invalid = root / "invalid.safetensors"
            write_safetensors(valid, b"1234")
            write_safetensors(invalid, b"12", declared_end=4)
            self.assertTrue(validate_safetensors(valid)["valid"])
            self.assertFalse(validate_safetensors(invalid)["valid"])
            inspected = inspect_model(root, "valid.safetensors")
            self.assertEqual(len(inspected["sha256"]), 64)

    def test_workflow_dependency_diagnosis(self) -> None:
        object_info = {
            "CheckpointLoaderSimple": {
                "input": {"required": {"ckpt_name": [["installed.safetensors"], {}]}}
            }
        }
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "missing.safetensors"}},
            "2": {"class_type": "UnknownCustomNode", "inputs": {}},
        }
        result = diagnose_workflow(workflow, object_info)
        self.assertFalse(result["valid_dependencies"])
        self.assertEqual(result["unavailable_models"][0]["value"], "missing.safetensors")
        self.assertEqual(result["missing_nodes"][0]["class_type"], "UnknownCustomNode")


if __name__ == "__main__":
    unittest.main()
