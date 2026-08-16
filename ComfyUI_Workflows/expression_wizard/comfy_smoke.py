from __future__ import annotations

import time
import uuid
from typing import Any


DEFAULT_POSITIVE = (
    "score_9, score_8_up, score_7_up, semi-realistic cinematic portrait of an adult woman, "
    "natural skin texture, detailed eyes, soft studio lighting, head and shoulders, neutral background"
)
DEFAULT_NEGATIVE = (
    "score_4, score_3, score_2, score_1, low quality, worst quality, blurry, deformed, "
    "bad anatomy, extra fingers, text, watermark"
)


def _choices(object_info: dict[str, Any], class_type: str, input_name: str) -> list[Any]:
    definition = object_info.get(class_type)
    if not isinstance(definition, dict):
        raise RuntimeError(f"ComfyUI does not expose required core node {class_type}")
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


def build_smoke_prompt(request: dict[str, Any], object_info: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    checkpoint = str(request.get("checkpoint", "")).strip()
    checkpoints = _choices(object_info, "CheckpointLoaderSimple", "ckpt_name")
    if checkpoint not in checkpoints:
        raise ValueError(f"Checkpoint is not installed or selectable: {checkpoint}")

    positive = str(request.get("positive") or DEFAULT_POSITIVE).strip()
    negative = str(request.get("negative") or DEFAULT_NEGATIVE).strip()
    if not positive or len(positive) > 4000 or len(negative) > 4000:
        raise ValueError("Prompts must contain 1-4000 characters (negative may be empty)")
    width = _bounded_int("width", request.get("width", 768), 512, 1024)
    height = _bounded_int("height", request.get("height", 768), 512, 1024)
    if width % 64 or height % 64:
        raise ValueError("Smoke-test width and height must be divisible by 64")
    steps = _bounded_int("steps", request.get("steps", 20), 1, 40)
    cfg = _bounded_float("cfg", request.get("cfg", 6.0), 1.0, 15.0)
    seed = _bounded_int("seed", request.get("seed", 20260816), 0, 2**63 - 1)
    sampler_name = str(request.get("sampler_name", "dpmpp_2m_sde"))
    scheduler = str(request.get("scheduler", "karras"))
    if sampler_name not in _choices(object_info, "KSampler", "sampler_name"):
        raise ValueError(f"Unsupported sampler: {sampler_name}")
    if scheduler not in _choices(object_info, "KSampler", "scheduler"):
        raise ValueError(f"Unsupported scheduler: {scheduler}")

    test_id = f"smoke_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    normalized = {
        "test_id": test_id,
        "checkpoint": checkpoint,
        "positive": positive,
        "negative": negative,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg": cfg,
        "seed": seed,
        "sampler_name": sampler_name,
        "scheduler": scheduler,
    }
    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "denoise": 1.0,
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": f"Expression_Wizard/Smoke_Test/{test_id}"}},
    }
    return prompt, normalized
