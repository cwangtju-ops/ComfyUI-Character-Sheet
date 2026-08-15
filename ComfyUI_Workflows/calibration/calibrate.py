from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lys_calibration import (
    API_DEFAULT,
    ProjectPaths,
    analyze_batch,
    build_control_atlas,
    generate_batch,
    ingest_review,
    interaction_spec,
    load_recipe_library,
    polish_recipe,
    promote_recipe,
    read_json,
    refinement_spec,
    validate_recipe_views,
    write_json,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Lys AdvancedLivePortrait calibration toolkit")
    root.add_argument("--api", default=API_DEFAULT, help="ComfyUI API base URL")
    sub = root.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="Generate one deterministic 12-candidate batch")
    generate.add_argument("spec", type=Path)
    generate.add_argument("--force", action="store_true")

    generate_all = sub.add_parser("generate-all", help="Generate every JSON specification in a directory")
    generate_all.add_argument("spec_dir", type=Path)
    generate_all.add_argument("--force", action="store_true")

    analyze = sub.add_parser("analyze", help="Analyze hidden tensors against the baseline batch")
    analyze.add_argument("batch_id")
    analyze.add_argument("--baseline", default="baseline_v1")

    ingest = sub.add_parser("ingest-review", help="Validate and import a reviewer JSON export")
    ingest.add_argument("batch_id")
    ingest.add_argument("review", type=Path)

    atlas = sub.add_parser("build-atlas", help="Combine reviewed single-control batches")
    atlas.add_argument("batch_ids", nargs="+")

    interaction = sub.add_parser("make-interaction", help="Create a 3x4 recipe interaction specification")
    interaction.add_argument("recipe", choices=(
        "soft_smile", "warm_intimate_smile", "playful_teasing", "suspicious_mocking_teasing",
        "restrained_anger", "eyes_closed_trusting", "quiet_longing_parted_lips",
        "open_mouth_smile", "surprise", "speaking_aaa_eee", "speaking_aaa_woo",
    ))
    interaction.add_argument("--atlas", type=Path)
    interaction.add_argument("--output", type=Path)

    refine = sub.add_parser("make-refinement", help="Create the next 12-candidate refinement specification")
    refine.add_argument("batch_id")
    refine.add_argument("--output", type=Path)

    promote = sub.add_parser("promote", help="Promote the highest accepted candidate into the recipe library")
    promote.add_argument("batch_id")
    promote.add_argument("recipe_name")

    validate = sub.add_parser("validate-views", help="Replay an approved expression on all face anchors")
    validate.add_argument("recipe_name")

    polish = sub.add_parser("polish", help="Run 1.5x finalist-only image-wash variants")
    polish.add_argument("recipe_name")

    status = sub.add_parser("status", help="Print calibration batches and review status")
    status.add_argument("--json", action="store_true")
    return root


def batch_path(paths: ProjectPaths, batch_id: str) -> Path:
    result = paths.generated_root / batch_id
    if not result.is_dir():
        raise FileNotFoundError(f"Unknown calibration batch: {batch_id}")
    return result


def status_payload(paths: ProjectPaths) -> dict:
    batches = []
    if paths.generated_root.is_dir():
        for manifest_path in sorted(paths.generated_root.glob("*/manifest.json")):
            manifest = read_json(manifest_path)
            batch_dir = manifest_path.parent
            review_path = batch_dir / "review.json"
            review = read_json(review_path) if review_path.is_file() else None
            batches.append(
                {
                    "batch_id": manifest["batch_id"],
                    "candidate_count": len(manifest.get("candidates", [])),
                    "reviewed": review is not None,
                    "accepted": sum(1 for item in (review or {}).get("reviews", []) if item.get("passes")),
                    "reviewer": str(batch_dir / "review.html"),
                }
            )
    library = load_recipe_library(paths.recipe_library)
    return {"batches": batches, "recipes": sorted(library["recipes"])}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    paths = ProjectPaths.discover()
    if args.command == "generate":
        print(generate_batch(args.spec.resolve(), api_url=args.api, force=args.force, paths=paths))
    elif args.command == "generate-all":
        for spec in sorted(args.spec_dir.resolve().glob("*.json")):
            print(generate_batch(spec, api_url=args.api, force=args.force, paths=paths))
    elif args.command == "analyze":
        result = analyze_batch(batch_path(paths, args.batch_id), batch_path(paths, args.baseline))
        print(json.dumps({"batch_id": result["batch_id"], "controls": sorted(result["controls"])}, indent=2))
    elif args.command == "ingest-review":
        review = ingest_review(batch_path(paths, args.batch_id), args.review.resolve())
        print(json.dumps({"batch_id": review["batch_id"], "accepted": sum(item["passes"] for item in review["reviews"])}, indent=2))
    elif args.command == "build-atlas":
        atlas = build_control_atlas([batch_path(paths, item) for item in args.batch_ids], paths.generated_root / "Atlas")
        print(json.dumps({name: data["approved_values"] for name, data in atlas["controls"].items()}, indent=2))
    elif args.command == "make-interaction":
        atlas_path = args.atlas or (paths.generated_root / "Atlas" / "control_atlas.json")
        result = interaction_spec(args.recipe, read_json(atlas_path))
        output = args.output or (paths.specs_dir / "generated" / f"{result['batch_id']}.json")
        write_json(output, result)
        print(output)
    elif args.command == "make-refinement":
        result = refinement_spec(batch_path(paths, args.batch_id))
        output = args.output or (paths.specs_dir / "generated" / f"{result['batch_id']}.json")
        write_json(output, result)
        print(output)
    elif args.command == "promote":
        result = promote_recipe(batch_path(paths, args.batch_id), args.recipe_name, paths.recipe_library)
        print(json.dumps(result, indent=2))
    elif args.command == "validate-views":
        print(validate_recipe_views(args.recipe_name, api_url=args.api, paths=paths))
    elif args.command == "polish":
        print(polish_recipe(args.recipe_name, api_url=args.api, paths=paths))
    elif args.command == "status":
        payload = status_payload(paths)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for item in payload["batches"]:
                state = "reviewed" if item["reviewed"] else "awaiting human review"
                print(f"{item['batch_id']}: {item['candidate_count']} candidates, {state}, {item['accepted']} accepted")
                print(f"  {item['reviewer']}")
            print("Recipes:", ", ".join(payload["recipes"]) or "none")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
