from __future__ import annotations

import argparse
import hashlib
import random

from lys_calibration import (
    API_DEFAULT,
    ProjectPaths,
    copy_verified,
    image_pixel_hash,
    polish_recipe,
    read_json,
    sha256_file,
    slug,
    write_json,
    write_reviewer,
)


def build_batch(recipe_names: list[str], api_url: str) -> str:
    if len(recipe_names) != 4 or len(set(recipe_names)) != 4:
        raise ValueError("Provide exactly four distinct recipes (4 x 3 wash strengths = 12 images)")
    paths = ProjectPaths.discover()
    recipe_dirs = [polish_recipe(name, api_url=api_url, paths=paths) for name in recipe_names]
    batch_id = "polish_" + "_".join(slug(name) for name in recipe_names)
    batch_dir = paths.generated_root / "Polish_Batches" / batch_id
    images_dir = batch_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    anchor_source = paths.lys_root / "anchor 1.png"
    copy_verified(anchor_source, batch_dir / "anchor.png")
    candidates = []
    for recipe_name, recipe_dir in zip(recipe_names, recipe_dirs):
        source_manifest = read_json(recipe_dir / "manifest.json")
        for item in source_manifest["candidates"]:
            candidate_id = slug(f"{recipe_name}_{item['candidate_id']}")
            destination = images_dir / f"{candidate_id}.png"
            copy_verified(recipe_dir / item["image"], destination)
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "label": f"{recipe_name}: {item['label']}",
                    "controls": item["controls"],
                    "effective_parameters": {"recipe": recipe_name, **item["effective_parameters"]},
                    "image": f"images/{candidate_id}.png",
                    "pixel_hash": image_pixel_hash(destination),
                }
            )
    order = [item["candidate_id"] for item in candidates]
    random.Random(int(hashlib.sha256(batch_id.encode()).hexdigest()[:16], 16)).shuffle(order)
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "purpose": "Blinded twelve-image review of four approved recipes at three wash strengths each.",
        "target": "finalist_polish_comparison",
        "anchor": {"name": "anchor 1.png", "sha256": sha256_file(anchor_source)},
        "candidates": candidates,
        "review_order": order,
    }
    write_json(batch_dir / "manifest.json", manifest)
    write_reviewer(batch_dir, manifest)
    return str(batch_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a 12-image polish review from four recipes")
    parser.add_argument("recipe_names", nargs=4)
    parser.add_argument("--api", default=API_DEFAULT)
    args = parser.parse_args()
    print(build_batch(args.recipe_names, args.api))
