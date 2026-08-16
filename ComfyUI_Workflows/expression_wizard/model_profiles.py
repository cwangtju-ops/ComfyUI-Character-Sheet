from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PROFILE_DIR = Path(__file__).resolve().parent / "model_profiles"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
REQUIRED_GENERATION_FIELDS = {
    "width",
    "height",
    "steps",
    "cfg",
    "sampler_name",
    "scheduler",
    "clip_skip",
    "vae",
}


def _required_text(value: Any, name: str, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"Model profile {name} must contain 1-{maximum} characters")
    return value.strip()


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if profile.get("schema_version") != 1:
        raise ValueError("Model profile schema_version must be 1")
    profile_id = _required_text(profile.get("profile_id"), "profile_id", 81)
    if not PROFILE_ID.fullmatch(profile_id):
        raise ValueError("Model profile profile_id is invalid")
    if profile.get("status") not in {"verified", "partial", "unverified"}:
        raise ValueError("Model profile status is invalid")
    _required_text(profile.get("display_name"), "display_name", 200)

    checkpoint = profile.get("checkpoint")
    if not isinstance(checkpoint, dict):
        raise ValueError("Model profile checkpoint must be an object")
    filename = _required_text(checkpoint.get("filename"), "checkpoint.filename", 255)
    if Path(filename).name != filename or "\\" in filename or "/" in filename:
        raise ValueError("Model profile checkpoint.filename must be a plain filename")
    sha256 = str(checkpoint.get("sha256", "")).lower()
    if not SHA256.fullmatch(sha256):
        raise ValueError("Model profile checkpoint.sha256 must contain 64 lowercase hex characters")
    size_bytes = checkpoint.get("size_bytes")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
        raise ValueError("Model profile checkpoint.size_bytes must be positive")
    _required_text(checkpoint.get("base_model"), "checkpoint.base_model", 200)

    sources = profile.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Model profile must contain at least one source")
    for source in sources:
        if not isinstance(source, dict) or not str(source.get("url", "")).startswith("https://"):
            raise ValueError("Every model profile source must use an https URL")
        _required_text(source.get("id"), "source.id", 80)
        _required_text(source.get("retrieved_at"), "source.retrieved_at", 40)

    generation = profile.get("generation")
    if not isinstance(generation, dict) or not REQUIRED_GENERATION_FIELDS.issubset(generation):
        raise ValueError("Model profile generation defaults are incomplete")
    for field in ("width", "height"):
        value = generation[field]
        if not isinstance(value, int) or isinstance(value, bool) or not 512 <= value <= 1536 or value % 64:
            raise ValueError(f"Model profile generation.{field} must be 512-1536 and divisible by 64")
    if not isinstance(generation["steps"], int) or isinstance(generation["steps"], bool) or not 1 <= generation["steps"] <= 80:
        raise ValueError("Model profile generation.steps must be 1-80")
    if not isinstance(generation["cfg"], (int, float)) or isinstance(generation["cfg"], bool) or not 1 <= float(generation["cfg"]) <= 20:
        raise ValueError("Model profile generation.cfg must be 1-20")
    if not isinstance(generation["clip_skip"], int) or isinstance(generation["clip_skip"], bool) or not 1 <= generation["clip_skip"] <= 12:
        raise ValueError("Model profile generation.clip_skip must be 1-12")
    for field in ("sampler_name", "scheduler", "vae"):
        _required_text(generation[field], f"generation.{field}", 100)

    prompts = profile.get("prompts")
    if not isinstance(prompts, dict):
        raise ValueError("Model profile prompts must be an object")
    _required_text(prompts.get("positive_prefix"), "prompts.positive_prefix")
    _required_text(prompts.get("default_subject"), "prompts.default_subject")
    _required_text(prompts.get("negative"), "prompts.negative")

    refinement = profile.get("refinement")
    if not isinstance(refinement, dict) or refinement.get("enabled_by_default") is not False:
        raise ValueError("Model profile refinement must exist and remain disabled by default")
    return profile


def load_profiles(directory: Path = PROFILE_DIR) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    if not directory.is_dir():
        return profiles
    for path in sorted(directory.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Model profile must be a JSON object: {path.name}")
        profile = validate_profile(value)
        profile["_filename"] = path.name
        profiles.append(profile)
    profile_ids = [item["profile_id"] for item in profiles]
    checkpoint_names = [item["checkpoint"]["filename"] for item in profiles]
    if len(profile_ids) != len(set(profile_ids)) or len(checkpoint_names) != len(set(checkpoint_names)):
        raise ValueError("Model profile ids and checkpoint filenames must be unique")
    return profiles


def profile_for_checkpoint(checkpoint: str, profiles: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    for profile in profiles if profiles is not None else load_profiles():
        if profile["checkpoint"]["filename"] == checkpoint:
            return profile
    return None


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in profile.items() if not key.startswith("_")}
