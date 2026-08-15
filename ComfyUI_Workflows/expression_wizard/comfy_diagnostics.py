from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".engine"}
MODEL_INPUT_HINTS = ("ckpt", "checkpoint", "model", "lora", "vae", "control", "clip", "unet", "diffusion")
MAX_INVENTORY_FILES = 20_000
MAX_SAFETENSORS_HEADER_BYTES = 100 * 1024 * 1024
SAFETENSORS_DTYPE_BYTES = {
    "BOOL": 1,
    "U8": 1,
    "I8": 1,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "U16": 2,
    "I16": 2,
    "F16": 2,
    "BF16": 2,
    "U32": 4,
    "I32": 4,
    "F32": 4,
    "U64": 8,
    "I64": 8,
    "F64": 8,
}


def discover_comfy_root(input_root: Path, output_root: Path, explicit: Path | None = None) -> Path:
    candidates = [explicit] if explicit else []
    candidates.extend([input_root.parent, output_root.parent])
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = candidate.expanduser().resolve()
        if (resolved / "models").is_dir() and (resolved / "custom_nodes").is_dir():
            return resolved
    raise ValueError("Could not discover ComfyUI root; pass --comfy-root explicitly")


def safe_relative_file(root: Path, relative: str) -> Path:
    if "\\" in relative:
        raise ValueError("Backslashes are not allowed in relative paths")
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Invalid relative path")
    root = root.resolve()
    target = root.joinpath(*parts).resolve()
    if root not in target.parents:
        raise ValueError("Path escapes the configured root")
    if not target.is_file():
        raise FileNotFoundError(relative)
    return target


def _utc_timestamp(path: Path) -> str:
    return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def list_models(models_root: Path) -> list[dict[str, Any]]:
    models_root = models_root.resolve()
    result = []
    for index, path in enumerate(sorted(models_root.rglob("*"))):
        if index >= MAX_INVENTORY_FILES:
            raise RuntimeError(f"Model inventory exceeds {MAX_INVENTORY_FILES} filesystem entries")
        if not path.is_file() or path.suffix.lower() not in MODEL_EXTENSIONS:
            continue
        relative = path.relative_to(models_root).as_posix()
        result.append(
            {
                "path": relative,
                "name": path.name,
                "category": relative.split("/", 1)[0] if "/" in relative else "root",
                "extension": path.suffix.lower(),
                "size_bytes": path.stat().st_size,
                "modified_at": _utc_timestamp(path),
            }
        )
    return result


def _git_revision(directory: Path) -> str | None:
    git_dir = directory / ".git"
    head = git_dir / "HEAD"
    if not head.is_file():
        return None
    value = head.read_text(encoding="utf-8", errors="replace").strip()
    if not value.startswith("ref: "):
        return value or None
    reference = value[5:]
    loose = git_dir / Path(reference)
    if loose.is_file():
        return loose.read_text(encoding="utf-8", errors="replace").strip() or None
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
            if line and not line.startswith(("#", "^")):
                revision, name = line.split(" ", 1)
                if name == reference:
                    return revision
    return None


def list_custom_nodes(custom_nodes_root: Path) -> list[dict[str, Any]]:
    custom_nodes_root = custom_nodes_root.resolve()
    result = []
    for path in sorted(custom_nodes_root.iterdir(), key=lambda item: item.name.lower()):
        if path.name.startswith((".", "__")):
            continue
        if path.is_dir():
            result.append(
                {
                    "name": path.name,
                    "kind": "directory",
                    "git_revision": _git_revision(path),
                    "has_requirements": (path / "requirements.txt").is_file(),
                    "modified_at": _utc_timestamp(path),
                }
            )
        elif path.is_file() and path.suffix.lower() == ".py":
            result.append(
                {
                    "name": path.name,
                    "kind": "python_file",
                    "git_revision": None,
                    "has_requirements": False,
                    "modified_at": _utc_timestamp(path),
                }
            )
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_safetensors(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    if size < 10:
        return {"valid": False, "error": "File is too small to contain a safetensors header"}
    try:
        with path.open("rb") as source:
            header_length = int.from_bytes(source.read(8), "little", signed=False)
            if header_length <= 1 or header_length > MAX_SAFETENSORS_HEADER_BYTES:
                raise ValueError(f"Invalid safetensors header length: {header_length}")
            if 8 + header_length > size:
                raise ValueError("Safetensors header is truncated")
            raw_header = source.read(header_length)
            if len(raw_header) != header_length:
                raise ValueError("Safetensors header is incomplete")
        header = json.loads(raw_header.decode("utf-8"))
        if not isinstance(header, dict):
            raise ValueError("Safetensors header must be a JSON object")
        intervals = []
        tensor_count = 0
        for name, definition in header.items():
            if name == "__metadata__":
                continue
            if not isinstance(definition, dict) or not isinstance(definition.get("data_offsets"), list) or len(definition["data_offsets"]) != 2:
                raise ValueError(f"Tensor {name!r} has invalid data_offsets")
            start, end = definition["data_offsets"]
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
                raise ValueError(f"Tensor {name!r} has invalid offsets")
            dtype = definition.get("dtype")
            shape = definition.get("shape")
            if dtype not in SAFETENSORS_DTYPE_BYTES or not isinstance(shape, list) or any(not isinstance(item, int) or item < 0 for item in shape):
                raise ValueError(f"Tensor {name!r} has an unsupported dtype or invalid shape")
            element_count = 1
            for dimension in shape:
                element_count *= dimension
            expected_bytes = element_count * SAFETENSORS_DTYPE_BYTES[dtype]
            if end - start != expected_bytes:
                raise ValueError(
                    f"Tensor {name!r} declares {expected_bytes} bytes from shape/dtype but covers {end - start} bytes"
                )
            intervals.append((start, end, name))
            tensor_count += 1
        data_bytes = size - 8 - header_length
        position = 0
        for start, end, name in sorted(intervals):
            if start != position:
                raise ValueError(f"Tensor data is not fully covered before {name!r}")
            if end > data_bytes:
                raise ValueError(f"Tensor {name!r} extends beyond the file")
            position = end
        if position != data_bytes:
            raise ValueError("Tensor data does not fully cover the file")
        try:
            from safetensors import safe_open

            with safe_open(str(path), framework="pt", device="cpu") as handle:
                native_tensor_count = len(handle.keys())
            if native_tensor_count != tensor_count:
                raise ValueError("Native safetensors tensor count does not match parsed metadata")
            validator = "safetensors.safe_open"
        except ImportError:
            validator = "structural_fallback"
        return {"valid": True, "validator": validator, "tensor_count": tensor_count, "header_bytes": header_length, "data_bytes": data_bytes}
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


def inspect_model(models_root: Path, relative: str, include_sha256: bool = True) -> dict[str, Any]:
    path = safe_relative_file(models_root, relative)
    if path.suffix.lower() not in MODEL_EXTENSIONS:
        raise ValueError("File extension is not an approved model type")
    result: dict[str, Any] = {
        "path": path.relative_to(models_root.resolve()).as_posix(),
        "size_bytes": path.stat().st_size,
        "modified_at": _utc_timestamp(path),
    }
    if include_sha256:
        result["sha256"] = sha256_file(path)
    if path.suffix.lower() == ".safetensors":
        result["safetensors"] = validate_safetensors(path)
    return result


def diagnose_workflow(workflow: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    missing_nodes: list[dict[str, str]] = []
    unavailable_models: list[dict[str, str]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type", ""))
        definition = object_info.get(class_type)
        if not definition:
            missing_nodes.append({"node_id": str(node_id), "class_type": class_type})
            continue
        declared = definition.get("input", {})
        inputs_schema = {**declared.get("required", {}), **declared.get("optional", {})}
        for input_name, value in (node.get("inputs") or {}).items():
            schema = inputs_schema.get(input_name)
            choices = schema[0] if isinstance(schema, list) and schema and isinstance(schema[0], list) else None
            lowered = input_name.lower()
            if choices is not None and isinstance(value, str) and value not in choices and any(hint in lowered for hint in MODEL_INPUT_HINTS):
                unavailable_models.append(
                    {"node_id": str(node_id), "class_type": class_type, "input": input_name, "value": value}
                )
    return {
        "valid_dependencies": not missing_nodes and not unavailable_models,
        "missing_nodes": missing_nodes,
        "unavailable_models": unavailable_models,
        "node_count": len(workflow),
    }
