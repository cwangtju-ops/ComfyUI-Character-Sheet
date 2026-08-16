from __future__ import annotations

import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image


WIZARD_DIR = Path(__file__).resolve().parents[1]
if str(WIZARD_DIR) not in sys.path:
    sys.path.insert(0, str(WIZARD_DIR))

from comfy_gateway import build_server  # noqa: E402
from comfy_management import ComfyManagementClient  # noqa: E402
from comfy_transport import RemoteComfyTransport  # noqa: E402


TOKEN = "gateway-test-token-with-24-chars"
ADMIN_TOKEN = "admin-test-token-with-at-least-24-chars"


class FakeComfyClient:
    def __init__(self, input_root: Path, output_root: Path):
        self.input_root = input_root
        self.output_root = output_root
        self.queued = None

    def system_paths(self) -> tuple[Path, Path]:
        return self.input_root, self.output_root

    def get(self, path: str) -> dict:
        if path == "/system_stats":
            return {"system": {"os": "test"}}
        if path == "/object_info/ExpressionEditor":
            return {"ExpressionEditor": {"input": {"required": {}}}}
        if path == "/object_info":
            return {
                "ExpressionEditor": {"input": {"required": {}}},
                "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["cyberrealisticPony_semiRealV6.safetensors"], {}]}}},
                "CLIPSetLastLayer": {"input": {"required": {}}},
                "CLIPTextEncode": {"input": {"required": {}}},
                "EmptyLatentImage": {"input": {"required": {}}},
                "KSampler": {"input": {"required": {"sampler_name": [["dpmpp_2m", "dpmpp_2m_sde"], {}], "scheduler": [["karras"], {}]}}},
                "VAEDecode": {"input": {"required": {}}},
                "SaveImage": {"input": {"required": {}}},
            }
        raise AssertionError(path)

    def queue(self, prompt: dict) -> str:
        self.queued = prompt
        return "remote-prompt-1"

    def wait(self, prompt_id: str, timeout: float) -> dict:
        assert prompt_id == "remote-prompt-1"
        assert timeout in {42.0, 600.0}
        node_id = next((key for key, node in self.queued.items() if node.get("class_type") == "SaveImage"), "3")
        subfolder = "remote-job" if node_id == "3" else "remote-job\\windows-output"
        return {"outputs": {node_id: {"images": [{"filename": "result.png", "subfolder": subfolder}]}}}


class ComfyGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.input_root = root / "input"
        self.output_root = root / "output"
        (root / "models" / "liveportrait").mkdir(parents=True)
        (root / "custom_nodes" / "ComfyUI-AdvancedLivePortrait").mkdir(parents=True)
        (root / "models" / "liveportrait" / "broken.safetensors").write_bytes(b"bad")
        (self.output_root / "remote-job").mkdir(parents=True)
        Image.new("RGB", (512, 512), "purple").save(self.output_root / "remote-job" / "result.png")
        (self.output_root / "remote-job" / "windows-output").mkdir()
        Image.new("RGB", (512, 512), "purple").save(self.output_root / "remote-job" / "windows-output" / "result.png")
        (self.output_root / "exp_data").mkdir()
        (self.output_root / "exp_data" / "smile.exp").write_bytes(b"remote-expression")
        self.client = FakeComfyClient(self.input_root, self.output_root)
        self.server = build_server("127.0.0.1", 0, "http://127.0.0.1:8188", TOKEN, set(), self.client, admin_token=ADMIN_TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_remote_transport_full_transfer_cycle(self) -> None:
        transport = RemoteComfyTransport(self.url, TOKEN)
        self.assertEqual(transport.get("/system_stats")["system"]["os"], "test")
        self.assertIn("ExpressionEditor", transport.get("/object_info/ExpressionEditor"))

        source = Path(self.temp.name) / "source.png"
        source.write_bytes(b"source-image")
        input_name = transport.stage_input(source, "Expression_Wizard/source.png")
        self.assertEqual(input_name, "Expression_Wizard/source.png")
        self.assertEqual((self.input_root / "Expression_Wizard" / "source.png").read_bytes(), b"source-image")

        prompt = {
            "1": {"class_type": "LoadImage", "inputs": {"image": "Expression_Wizard/source.png"}},
            "2": {"class_type": "ExpressionEditor", "inputs": {"src_image": ["1", 0]}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "Expression_Wizard/job/candidate"}},
            "4": {"class_type": "SaveExpData", "inputs": {"save_exp": ["2", 2], "file_name": "smile"}},
        }
        prompt_id, history = transport.execute(prompt, timeout=42.0)
        self.assertEqual(prompt_id, "remote-prompt-1")
        self.assertEqual(self.client.queued, prompt)

        image = Path(self.temp.name) / "wizard" / "candidate.png"
        transport.materialize_image(history, image)
        self.assertEqual(image.read_bytes(), (self.output_root / "remote-job" / "result.png").read_bytes())
        expression = Path(self.temp.name) / "wizard" / "candidate.exp"
        transport.materialize_expression("smile", expression)
        self.assertEqual(expression.read_bytes(), b"remote-expression")

    def test_constrained_smoke_test_uses_fixed_core_workflow(self) -> None:
        transport = RemoteComfyTransport(self.url, TOKEN)
        prompt_id, history, config = transport.smoke_test(
            {"checkpoint": "cyberrealisticPony_semiRealV6.safetensors", "width": 512, "height": 512, "steps": 4}
        )
        self.assertEqual(prompt_id, "remote-prompt-1")
        self.assertEqual(config["checkpoint"], "cyberrealisticPony_semiRealV6.safetensors")
        self.assertEqual({node["class_type"] for node in self.client.queued.values()}, {
            "CheckpointLoaderSimple", "CLIPSetLastLayer", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage"
        })
        self.assertEqual(config["profile_id"], "cyberrealistic-pony-semireal-v6")
        self.assertFalse(config["recommendation_match"])
        image = Path(self.temp.name) / "smoke.png"
        transport.materialize_image(history, image, node_id=config["output_node_id"])
        with Image.open(image) as generated:
            self.assertEqual(generated.size, (512, 512))

    def test_smoke_test_rejects_uninstalled_checkpoint(self) -> None:
        transport = RemoteComfyTransport(self.url, TOKEN)
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            transport.smoke_test({"checkpoint": "missing.safetensors"})

    def test_gateway_rejects_missing_token_and_path_traversal(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(self.url + "/api/system-stats", timeout=2)
        self.assertEqual(unauthorized.exception.code, 401)

        request = urllib.request.Request(
            self.url + "/api/input",
            data=b"escape",
            headers={"Authorization": f"Bearer {TOKEN}", "X-Input-Name": "../escape.png"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as traversal:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(traversal.exception.code, 400)
        self.assertFalse((Path(self.temp.name) / "escape.png").exists())

    def test_lan_binding_requires_an_allowed_client(self) -> None:
        with self.assertRaisesRegex(ValueError, "allow-client"):
            build_server("0.0.0.0", 0, "http://127.0.0.1:8188", TOKEN, set(), self.client)

    def test_read_only_management_uses_separate_token(self) -> None:
        request = urllib.request.Request(
            self.url + "/api/admin/summary",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        with self.assertRaises(urllib.error.HTTPError) as wrong_scope:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(wrong_scope.exception.code, 401)

        management = ComfyManagementClient(self.url, ADMIN_TOKEN)
        summary = management.summary()
        self.assertEqual(summary["mode"], "read_only")
        self.assertEqual(summary["model_count"], 1)
        self.assertEqual(management.models("broken")["count"], 1)
        inspected = management.inspect_model("liveportrait/broken.safetensors", include_sha256=False)
        self.assertFalse(inspected["safetensors"]["valid"])
        self.assertNotIn("sha256", inspected)
        self.assertEqual(management.nodes("AdvancedLivePortrait")["count"], 1)

        diagnosis = management.diagnose_workflow(
            {"1": {"class_type": "MissingNode", "inputs": {}}}
        )
        self.assertFalse(diagnosis["valid_dependencies"])
        self.assertEqual(diagnosis["missing_nodes"][0]["class_type"], "MissingNode")


if __name__ == "__main__":
    unittest.main()
