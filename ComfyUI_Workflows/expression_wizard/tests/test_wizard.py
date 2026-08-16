from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_server
from wizard_core import (
    WizardPaths,
    analyze_job_dir,
    build_candidates,
    canonicalize_upload,
    default_parameter_schema,
    linspace,
    validate_job_request,
)


def schema() -> dict:
    return {
        "controls": default_parameter_schema(),
        "advanced": {
            "src_ratio": {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01},
            "crop_factor": {"default": 1.7, "min": 1.5, "max": 2.5, "step": 0.1},
        },
        "forced": {"sample_ratio": 0.0, "sample_parts": "OnlyExpression"},
    }


class WizardTests(unittest.TestCase):
    def test_sweep_is_inclusive_even_and_reversible(self) -> None:
        values = linspace(-20, 5, 12)
        self.assertEqual(values[0], -20)
        self.assertEqual(values[-1], 5)
        gaps = [round(values[i + 1] - values[i], 6) for i in range(11)]
        self.assertLess(max(gaps) - min(gaps), 0.000002)
        self.assertEqual(linspace(5, -20, 12), list(reversed(values)))

    def test_sweep_builds_exactly_twelve_declared_changes(self) -> None:
        candidates = build_candidates({"mode": "sweep", "sweep": {"parameter": "blink", "start": -20, "end": 5, "count": 12}}, schema())
        self.assertEqual(len(candidates), 12)
        self.assertTrue(all(set(item["controls"]) == {"blink"} for item in candidates))

    def test_grid_is_four_by_three_row_major(self) -> None:
        candidates = build_candidates({"mode": "grid", "grid": {"x": {"parameter": "smile", "start": -0.3, "end": 1.3, "count": 4}, "y": {"parameter": "blink", "start": -20, "end": 5, "count": 3}}}, schema())
        self.assertEqual(len(candidates), 12)
        self.assertEqual(candidates[0]["grid_position"], {"x": 0, "y": 0})
        self.assertEqual(candidates[3]["grid_position"], {"x": 3, "y": 0})
        self.assertEqual(candidates[4]["grid_position"], {"x": 0, "y": 1})

    def test_grid_rejects_same_axis(self) -> None:
        request = {"mode": "grid", "grid": {"x": {"parameter": "blink", "start": -20, "end": 5, "count": 4}, "y": {"parameter": "blink", "start": -20, "end": 5, "count": 3}}}
        with self.assertRaises(ValueError):
            build_candidates(request, schema())

    def test_manual_requires_twelve_rows_and_valid_ranges(self) -> None:
        rows = [{"label": f"row {index}", "controls": {"smile": 0.1}} for index in range(12)]
        candidates = build_candidates({"mode": "manual", "manual": {"candidates": rows}}, schema())
        self.assertEqual(len(candidates), 12)
        rows[0]["controls"]["smile"] = 99
        with self.assertRaises(ValueError):
            build_candidates({"mode": "manual", "manual": {"candidates": rows}}, schema())

    def test_job_request_forces_manual_sample_settings(self) -> None:
        request = {"source": {"type": "lys", "id": "anchor_1"}, "mode": "sweep", "fixed": {"blink": 2}, "sweep": {"parameter": "smile", "start": 0, "end": 1, "count": 12}}
        normalized = validate_job_request(request, schema())
        self.assertEqual(normalized["fixed"]["sample_ratio"], 0)
        self.assertEqual(normalized["fixed"]["sample_parts"], "OnlyExpression")
        self.assertEqual(normalized["fixed"]["blink"], 2)

    def test_upload_is_canonical_png_and_preserves_dimensions(self) -> None:
        buffer = io.BytesIO()
        Image.new("RGB", (1024, 1024), "white").save(buffer, "WEBP")
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "asset.png"
            result = canonicalize_upload(buffer.getvalue(), destination)
            self.assertEqual((result["width"], result["height"]), (1024, 1024))
            with Image.open(destination) as image:
                self.assertEqual(image.size, (1024, 1024))
                self.assertEqual(image.format, "PNG")

    def test_paths_reject_traversal_job_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = WizardPaths(Path(temp))
            with self.assertRaises(ValueError):
                paths.job_dir("../escape")

    def test_analysis_reports_strength_codes_and_violations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            (job / "exp_data").mkdir()
            spec = {"job_id": "test", "mode": "sweep", "request": {"sweep": {"parameter": "blink"}}, "fixed": {"blink": 0.0, "smile": 0.0}}
            candidates = []
            for index, value in enumerate((-1.0, 0.0, 1.0)):
                candidate_id = f"candidate_{index + 1:02d}"
                tensor = [[[0.0, value, 0.0]] + [[0.0, 0.0, 0.0] for _ in range(20)]]
                codes = [{"code": 1, "value": value}]
                (job / "exp_data" / f"{candidate_id}.json").write_text(json.dumps({"e": tensor, "r": [0, 0, 0], "s": 0, "t": 0, "codes": codes}), encoding="utf-8")
                candidates.append({"candidate_id": candidate_id, "controls": {"blink": value}, "effective_parameters": {"blink": value, "smile": 0.0}, "exp_json": f"exp_data/{candidate_id}.json"})
            (job / "batch_spec.json").write_text(json.dumps(spec), encoding="utf-8")
            (job / "manifest.json").write_text(json.dumps({"candidates": candidates}), encoding="utf-8")
            result = analyze_job_dir(job)
            self.assertEqual(result["candidate_count"], 3)
            self.assertEqual(result["candidates"][0]["affected_codes"], [1])
            self.assertFalse(result["candidates"][0]["fixed_control_violations"])

    def test_mcp_initialization_and_tool_discovery(self) -> None:
        initialized = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "expression-wizard")
        tools = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(tools["result"]["tools"]), 16)

    def test_mcp_tool_returns_structured_content(self) -> None:
        with patch.object(mcp_server, "call_tool", return_value={"ok": True}):
            result = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_expression_schema", "arguments": {}}})
        self.assertEqual(result["result"]["structuredContent"], {"ok": True})
        self.assertFalse(result["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
