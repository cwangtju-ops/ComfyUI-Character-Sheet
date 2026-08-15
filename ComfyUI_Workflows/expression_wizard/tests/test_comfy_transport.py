from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


WIZARD_DIR = Path(__file__).resolve().parents[1]
if str(WIZARD_DIR) not in sys.path:
    sys.path.insert(0, str(WIZARD_DIR))

from comfy_transport import LocalComfyTransport  # noqa: E402


class FakeComfyClient:
    def __init__(self, input_root: Path, output_root: Path):
        self.input_root = input_root
        self.output_root = output_root
        self.queued_prompt = None
        self.wait_call = None

    def system_paths(self) -> tuple[Path, Path]:
        return self.input_root, self.output_root

    def queue(self, prompt: dict) -> str:
        self.queued_prompt = prompt
        return "prompt-123"

    def wait(self, prompt_id: str, timeout: float) -> dict:
        self.wait_call = (prompt_id, timeout)
        return {"outputs": {"3": {"images": [{"filename": "result.png", "subfolder": "job"}]}}}


class LocalComfyTransportTests(unittest.TestCase):
    def test_stages_executes_and_materializes_comfy_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_root = root / "comfy-input"
            output_root = root / "comfy-output"
            source = root / "source.png"
            source.write_bytes(b"source-image")
            (output_root / "job").mkdir(parents=True)
            (output_root / "job" / "result.png").write_bytes(b"generated-image")
            (output_root / "exp_data").mkdir()
            (output_root / "exp_data" / "smile.exp").write_bytes(b"expression-data")

            client = FakeComfyClient(input_root, output_root)
            transport = LocalComfyTransport(client=client)

            input_name = transport.stage_input(source, "Expression_Wizard/source.png")
            self.assertEqual(input_name, "Expression_Wizard/source.png")
            self.assertEqual((input_root / input_name).read_bytes(), b"source-image")

            prompt_id, history = transport.execute({"node": "value"}, timeout=42.0)
            self.assertEqual(prompt_id, "prompt-123")
            self.assertEqual(client.queued_prompt, {"node": "value"})
            self.assertEqual(client.wait_call, ("prompt-123", 42.0))

            image_destination = root / "wizard" / "images" / "candidate.png"
            output = transport.materialize_image(history, image_destination)
            self.assertEqual(output["filename"], "result.png")
            self.assertEqual(image_destination.read_bytes(), b"generated-image")

            expression_destination = root / "wizard" / "exp_data" / "candidate.exp"
            transport.materialize_expression("smile", expression_destination)
            self.assertEqual(expression_destination.read_bytes(), b"expression-data")


if __name__ == "__main__":
    unittest.main()
