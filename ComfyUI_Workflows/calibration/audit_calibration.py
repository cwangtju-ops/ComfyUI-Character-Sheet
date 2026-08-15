from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from lys_calibration import (
    API_DEFAULT,
    ComfyClient,
    ProjectPaths,
    build_replay_prompt,
    copy_verified,
    image_pixel_hash,
    output_image_from_history,
    read_json,
    sha256_file,
)


BATCHES = ("baseline_v1", "eyes_v1", "brow_gaze_v1", "mouth_phonemes_v1", "smile_v1")


def audit(paths: ProjectPaths) -> dict:
    summary = []
    for batch_id in BATCHES:
        batch_dir = paths.generated_root / batch_id
        manifest = read_json(batch_dir / "manifest.json")
        spec = read_json(batch_dir / "batch_spec.json")
        if manifest["batch_id"] != batch_id or spec["batch_id"] != batch_id:
            raise AssertionError(f"Batch id mismatch in {batch_id}")
        if len(manifest["candidates"]) != 12 or len(spec["candidates"]) != 12:
            raise AssertionError(f"{batch_id} does not contain exactly 12 candidates")
        spec_by_id = {item["id"]: item for item in spec["candidates"]}
        image_hashes, expression_hashes = [], []
        for candidate in manifest["candidates"]:
            candidate_id = candidate["candidate_id"]
            if candidate["controls"] != spec_by_id[candidate_id]["controls"]:
                raise AssertionError(f"Control mismatch in {batch_id}/{candidate_id}")
            for relative in (
                candidate["image"],
                candidate["exp_binary"],
                candidate["exp_json"],
                candidate["exp_csv"],
                candidate["prompt"],
            ):
                if not (batch_dir / relative).is_file():
                    raise AssertionError(f"Missing {batch_id}/{relative}")
            with Image.open(batch_dir / candidate["image"]) as image:
                if image.size != (1254, 1254):
                    raise AssertionError(f"Unexpected size {image.size} in {batch_id}/{candidate_id}")
            expression = read_json(batch_dir / candidate["exp_json"])
            tensor = expression["e"][0]
            if expression["e_shape"] != [1, 21, 3] or len(tensor) != 21 or any(len(row) != 3 for row in tensor):
                raise AssertionError(f"Hidden tensor is not 21x3 in {batch_id}/{candidate_id}")
            if len(expression["codes"]) != 63:
                raise AssertionError(f"Expected 63 landmark-axis codes in {batch_id}/{candidate_id}")
            image_hashes.append(candidate["pixel_hash"])
            expression_hashes.append(candidate["expression_hash"])
        summary.append(
            {
                "batch_id": batch_id,
                "candidates": 12,
                "dimensions": [1254, 1254],
                "unique_images": len(set(image_hashes)),
                "unique_expressions": len(set(expression_hashes)),
            }
        )
    baseline = summary[0]
    if baseline["unique_images"] != 1 or baseline["unique_expressions"] != 1:
        raise AssertionError("Baseline is not deterministic")
    return {"ok": True, "batches": summary}


def replay_baseline(paths: ProjectPaths, api_url: str) -> dict:
    batch_dir = paths.generated_root / "baseline_v1"
    candidate = read_json(batch_dir / "manifest.json")["candidates"][0]
    client = ComfyClient(api_url)
    input_root, output_root = client.system_paths()
    anchor = paths.lys_root / "anchor 1.png"
    anchor_input = f"Lys_Calibration/{sha256_file(anchor)[:12]}_anchor_1.png"
    exp_name = "lys_installation_replay"
    copy_verified(anchor, input_root / Path(anchor_input))
    copy_verified(batch_dir / candidate["exp_binary"], output_root / "exp_data" / f"{exp_name}.exp")
    prompt = build_replay_prompt(anchor_input, exp_name, "Lys_Comfy/Calibration/Verification/baseline_replay")
    prompt_id = client.queue(prompt)
    history = client.wait(prompt_id)
    output = output_image_from_history(history, "3")
    replay = output_root / output.get("subfolder", "") / output["filename"]
    expected = batch_dir / candidate["image"]
    expected_hash, replay_hash = image_pixel_hash(expected), image_pixel_hash(replay)
    return {
        "ok": replay_hash == expected_hash,
        "prompt_id": prompt_id,
        "expected_pixel_hash": expected_hash,
        "replay_pixel_hash": replay_hash,
        "output": str(replay),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the Lys calibration installation")
    parser.add_argument("--api", default=API_DEFAULT)
    parser.add_argument("--live-replay", action="store_true")
    args = parser.parse_args()
    paths = ProjectPaths.discover()
    result = audit(paths)
    if args.live_replay:
        result["replay"] = replay_baseline(paths, args.api)
        if not result["replay"]["ok"]:
            raise AssertionError("LoadExpData replay did not reproduce the baseline candidate")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
