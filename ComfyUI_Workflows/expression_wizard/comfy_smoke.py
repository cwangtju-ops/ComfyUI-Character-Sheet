from __future__ import annotations

import time
import uuid
from typing import Any

from model_profiles import load_profiles, profile_for_checkpoint, public_profile


def _require_node(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    definition = object_info.get(class_type)
    if not isinstance(definition, dict):
        raise RuntimeError(f"ComfyUI does not expose required core node {class_type}")
    return definition


def _choices(object_info: dict[str, Any], class_type: str, input_name: str) -> list[Any]:
    definition = _require_node(object_info, class_type)
    inputs = definition.get("input", {})
    schema = {**inputs.get("required", {}), **inputs.get("optional", {})}.get(input_name)
    if not isinstance(schema, (list, tuple)) or not schema or not isinstance(schema[0], (list, tuple)):
        raise RuntimeError(f"ComfyUI does not expose choices for {class_type}.{input_name}")
    return list(schema[0])


def _bounded_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _bounded_float(name: str, value: Any, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not minimum <= float(value) <= maximum:
        raise ValueError(f"{name} must be a number in [{minimum}, {maximum}]")
    return float(value)


def build_smoke_prompt(
    request: dict[str, Any],
    object_info: dict[str, Any],
    profiles: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    checkpoint = str(request.get("checkpoint", "")).strip()
    checkpoints = _choices(object_info, "CheckpointLoaderSimple", "ckpt_name")
    if checkpoint not in checkpoints:
        raise ValueError(f"Checkpoint is not installed or selectable: {checkpoint}")

    profile = profile_for_checkpoint(checkpoint, profiles if profiles is not None else load_profiles())
    if profile is None:
        raise ValueError(f"No Model Profile exists for checkpoint: {checkpoint}")
    requested_profile = str(request.get("profile_id", "")).strip()
    if requested_profile and requested_profile != profile["profile_id"]:
        raise ValueError(f"Model Profile {requested_profile} does not match checkpoint {checkpoint}")
    defaults = profile["generation"]
    prompt_defaults = profile["prompts"]

    subject = str(request.get("positive") or prompt_defaults["default_subject"]).strip()
    prefix = str(prompt_defaults["positive_prefix"]).strip()
    positive = subject if subject.lower().startswith(prefix.lower()) else f"{prefix}, {subject}"
    negative = str(request["negative"] if "negative" in request else prompt_defaults["negative"]).strip()
    if not positive or len(positive) > 4000 or len(negative) > 4000:
        raise ValueError("Prompts must contain 1-4000 characters (negative may be empty)")
    width = _bounded_int("width", request.get("width", defaults["width"]), 512, 1536)
    height = _bounded_int("height", request.get("height", defaults["height"]), 512, 1536)
    if width % 64 or height % 64:
        raise ValueError("Smoke-test width and height must be divisible by 64")
    steps = _bounded_int("steps", request.get("steps", defaults["steps"]), 1, 80)
    cfg = _bounded_float("cfg", request.get("cfg", defaults["cfg"]), 1.0, 20.0)
    seed = _bounded_int("seed", request.get("seed", 20260816), 0, 2**63 - 1)
    sampler_name = str(request.get("sampler_name", defaults["sampler_name"]))
    scheduler = str(request.get("scheduler", defaults["scheduler"]))
    clip_skip = _bounded_int("clip_skip", request.get("clip_skip", defaults["clip_skip"]), 1, 12)
    if sampler_name not in _choices(object_info, "KSampler", "sampler_name"):
        raise ValueError(f"Unsupported sampler: {sampler_name}")
    if scheduler not in _choices(object_info, "KSampler", "scheduler"):
        raise ValueError(f"Unsupported scheduler: {scheduler}")
    if clip_skip > 1:
        _require_node(object_info, "CLIPSetLastLayer")

    test_id = f"smoke_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    actual_generation = {
        "width": width,
        "height": height,
        "steps": steps,
        "cfg": cfg,
        "sampler_name": sampler_name,
        "scheduler": scheduler,
        "clip_skip": clip_skip,
        "vae": defaults["vae"],
    }
    overrides = {
        name: {"recommended": defaults[name], "actual": actual_generation[name]}
        for name in ("width", "height", "steps", "cfg", "sampler_name", "scheduler", "clip_skip")
        if name in request and actual_generation[name] != defaults[name]
    }
    if "negative" in request and negative != prompt_defaults["negative"]:
        overrides["negative"] = {"recommended": prompt_defaults["negative"], "actual": negative}

    prompt: dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
    }
    if clip_skip > 1:
        prompt["2"] = {
            "class_type": "CLIPSetLastLayer",
            "inputs": {"clip": ["1", 1], "stop_at_clip_layer": -clip_skip},
        }
        clip_reference: list[Any] = ["2", 0]
        next_node = 3
    else:
        clip_reference = ["1", 1]
        next_node = 2
    positive_node = str(next_node)
    negative_node = str(next_node + 1)
    latent_node = str(next_node + 2)
    sampler_node = str(next_node + 3)
    decode_node = str(next_node + 4)
    output_node = str(next_node + 5)
    prompt[positive_node] = {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": clip_reference}}
    prompt[negative_node] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": clip_reference}}
    prompt[latent_node] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
    prompt[sampler_node] = {
        "class_type": "KSampler",
        "inputs": {
            "model": ["1", 0],
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "positive": [positive_node, 0],
            "negative": [negative_node, 0],
            "latent_image": [latent_node, 0],
            "denoise": 1.0,
        },
    }
    prompt[decode_node] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_node, 0], "vae": ["1", 2]}}
    prompt[output_node] = {
        "class_type": "SaveImage",
        "inputs": {"images": [decode_node, 0], "filename_prefix": f"Expression_Wizard/Smoke_Test/{test_id}"},
    }
    normalized = {
        "test_id": test_id,
        "checkpoint": checkpoint,
        "checkpoint_sha256": profile["checkpoint"]["sha256"],
        "profile_id": profile["profile_id"],
        "profile_status": profile["status"],
        "profile": public_profile(profile),
        "positive": positive,
        "positive_subject": subject,
        "subject_customized": "positive" in request and subject != prompt_defaults["default_subject"],
        "negative": negative,
        **actual_generation,
        "seed": seed,
        "recommended_defaults": defaults,
        "overrides": overrides,
        "recommendation_match": not overrides,
        "refinement": {**profile["refinement"], "enabled": False},
        "output_node_id": output_node,
    }
    return prompt, normalized
