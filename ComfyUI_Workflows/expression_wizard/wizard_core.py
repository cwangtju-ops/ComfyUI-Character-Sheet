from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image


HERE = Path(__file__).resolve().parent
CALIBRATION_DIR = HERE.parent / "calibration"
import sys

if str(CALIBRATION_DIR) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_DIR))

from lys_calibration import (  # noqa: E402
    API_DEFAULT,
    CONTROL_RANGES,
    EDITOR_DEFAULTS,
    MANUAL_CONTROLS,
    ComfyClient,
    build_expression_prompt,
    copy_verified,
    image_pixel_hash,
    read_json,
    sha256_file,
    slug,
    utc_now,
    write_json,
)

from comfy_transport import ComfyTransport, LocalComfyTransport  # noqa: E402
from model_profiles import load_profiles, public_profile  # noqa: E402


SCHEMA_VERSION = 1
JOB_STATES = {"queued", "running", "completed", "failed", "cancelled", "interrupted"}
ACTIVE_STATES = {"queued", "running"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_UPLOAD_FORMATS = {"PNG", "JPEG", "WEBP"}
EXPRESSION_LABELS = {
    "rotate_pitch": "Pitch",
    "rotate_yaw": "Yaw",
    "rotate_roll": "Roll",
    "blink": "Blink",
    "eyebrow": "Eyebrow",
    "wink": "Wink",
    "pupil_x": "Pupil X",
    "pupil_y": "Pupil Y",
    "aaa": "AAA",
    "eee": "EEE",
    "woo": "WOO",
    "smile": "Smile",
}


def now_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def linspace(start: float, end: float, count: int) -> list[float]:
    if count < 2:
        raise ValueError("A sweep requires at least two values")
    start, end = float(start), float(end)
    step = (end - start) / (count - 1)
    values = [round(start + step * index, 8) for index in range(count)]
    values[0], values[-1] = start, end
    return values


def default_parameter_schema() -> dict[str, dict[str, Any]]:
    defaults = {name: EDITOR_DEFAULTS[name] for name in MANUAL_CONTROLS}
    return {
        name: {
            "name": name,
            "label": EXPRESSION_LABELS[name],
            "type": "FLOAT",
            "default": float(defaults[name]),
            "min": float(CONTROL_RANGES[name][0]),
            "max": float(CONTROL_RANGES[name][1]),
            "step": 0.01 if name == "smile" else 0.5,
        }
        for name in MANUAL_CONTROLS
    }


def live_expression_schema(api_url: str = API_DEFAULT, transport: ComfyTransport | None = None) -> dict[str, Any]:
    client = transport or ComfyClient(api_url)
    info = client.get("/object_info/ExpressionEditor").get("ExpressionEditor")
    if not info:
        raise RuntimeError("The running ComfyUI does not expose ExpressionEditor")
    required = info["input"]["required"]
    controls: dict[str, Any] = {}
    for name in MANUAL_CONTROLS:
        kind, options = required[name]
        controls[name] = {
            "name": name,
            "label": EXPRESSION_LABELS[name],
            "type": kind,
            "default": float(options["default"]),
            "min": float(options["min"]),
            "max": float(options["max"]),
            "step": float(options.get("step", 0.01)),
        }
    advanced = {}
    for name in ("src_ratio", "crop_factor"):
        kind, options = required[name]
        advanced[name] = {
            "name": name,
            "label": "Source ratio" if name == "src_ratio" else "Crop factor",
            "type": kind,
            "default": float(options["default"]),
            "min": float(options["min"]),
            "max": float(options["max"]),
            "step": float(options.get("step", 0.01)),
        }
    return {
        "controls": controls,
        "advanced": advanced,
        "forced": {"sample_ratio": 0.0, "sample_parts": "OnlyExpression"},
    }


def validate_number(name: str, value: Any, schema: dict[str, Any]) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not float(schema["min"]) <= value <= float(schema["max"]):
        raise ValueError(f"{name}={value} is outside [{schema['min']}, {schema['max']}]")
    return value


def complete_fixed(raw: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    fixed = {}
    for name in MANUAL_CONTROLS:
        definition = schema["controls"][name]
        fixed[name] = validate_number(name, raw.get(name, definition["default"]), definition)
    for name in ("src_ratio", "crop_factor"):
        definition = schema["advanced"][name]
        fixed[name] = validate_number(name, raw.get(name, definition["default"]), definition)
    fixed["sample_ratio"] = 0.0
    fixed["sample_parts"] = "OnlyExpression"
    return fixed


def _validate_controls(raw: dict[str, Any], schema: dict[str, Any]) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError("Candidate controls must be an object")
    controls = {}
    for name, value in raw.items():
        if name not in MANUAL_CONTROLS:
            raise ValueError(f"Unsupported expression control: {name}")
        controls[name] = validate_number(name, value, schema["controls"][name])
    return controls


def build_candidates(request: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, Any]]:
    mode = request.get("mode")
    if mode == "sweep":
        sweep = request.get("sweep") or {}
        parameter = sweep.get("parameter")
        if parameter not in MANUAL_CONTROLS:
            raise ValueError("Sweep parameter is invalid")
        definition = schema["controls"][parameter]
        start = validate_number(parameter, sweep.get("start"), definition)
        end = validate_number(parameter, sweep.get("end"), definition)
        if int(sweep.get("count", 12)) != 12:
            raise ValueError("Expression Wizard sweeps contain exactly 12 candidates")
        return [
            {
                "id": f"candidate_{index + 1:02d}",
                "label": f"{EXPRESSION_LABELS[parameter]} {value:g}",
                "controls": {parameter: value},
            }
            for index, value in enumerate(linspace(start, end, 12))
        ]
    if mode == "grid":
        grid = request.get("grid") or {}
        x, y = grid.get("x") or {}, grid.get("y") or {}
        x_name, y_name = x.get("parameter"), y.get("parameter")
        if x_name not in MANUAL_CONTROLS or y_name not in MANUAL_CONTROLS:
            raise ValueError("Grid parameters are invalid")
        if x_name == y_name:
            raise ValueError("Grid axes must use different controls")
        if int(x.get("count", 4)) != 4 or int(y.get("count", 3)) != 3:
            raise ValueError("Expression Wizard grids are exactly 4 x 3")
        x_values = linspace(
            validate_number(x_name, x.get("start"), schema["controls"][x_name]),
            validate_number(x_name, x.get("end"), schema["controls"][x_name]),
            4,
        )
        y_values = linspace(
            validate_number(y_name, y.get("start"), schema["controls"][y_name]),
            validate_number(y_name, y.get("end"), schema["controls"][y_name]),
            3,
        )
        result = []
        for y_index, y_value in enumerate(y_values):
            for x_index, x_value in enumerate(x_values):
                result.append(
                    {
                        "id": f"candidate_{len(result) + 1:02d}",
                        "label": f"{EXPRESSION_LABELS[x_name]} {x_value:g} / {EXPRESSION_LABELS[y_name]} {y_value:g}",
                        "controls": {x_name: x_value, y_name: y_value},
                        "grid_position": {"x": x_index, "y": y_index},
                    }
                )
        return result
    if mode == "manual":
        rows = request.get("manual", {}).get("candidates")
        if not isinstance(rows, list) or len(rows) != 12:
            raise ValueError("Manual mode requires exactly 12 candidate rows")
        result = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"Manual candidate {index + 1} must be an object")
            result.append(
                {
                    "id": f"candidate_{index + 1:02d}",
                    "label": str(row.get("label") or f"Candidate {index + 1:02d}"),
                    "controls": _validate_controls(row.get("controls", {}), schema),
                }
            )
        return result
    raise ValueError("Mode must be sweep, grid, or manual")


def validate_job_request(request: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("Job request must be an object")
    source = request.get("source")
    if not isinstance(source, dict) or source.get("type") not in {"lys", "upload"} or not source.get("id"):
        raise ValueError("A Lys anchor or uploaded source is required")
    fixed = complete_fixed(request.get("fixed") or {}, schema)
    candidates = build_candidates(request, schema)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": request["mode"],
        "source": {"type": source["type"], "id": str(source["id"])},
        "fixed": fixed,
        "candidates": candidates,
        "request": copy.deepcopy(request),
    }


def safe_image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return image.size


def canonicalize_upload(data: bytes, destination: Path) -> dict[str, Any]:
    if not data:
        raise ValueError("Upload is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Upload exceeds the 25 MB limit")
    import io

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ALLOWED_UPLOAD_FORMATS:
                raise ValueError("Only PNG, JPEG, and WebP portraits are supported")
            image.load()
            if image.width < 64 or image.height < 64:
                raise ValueError("Portrait must be at least 64 x 64 pixels")
            normalized = image.convert("RGB")
            destination.parent.mkdir(parents=True, exist_ok=True)
            normalized.save(destination, "PNG", optimize=True)
            return {
                "width": normalized.width,
                "height": normalized.height,
                "format": image.format,
                "sha256": sha256_file(destination),
            }
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Uploaded file is not a readable portrait image: {exc}") from exc


class WizardPaths:
    def __init__(self, lys_root: Path):
        self.lys_root = lys_root.resolve()
        self.root = self.lys_root / "ComfyUI_Generated" / "Expression_Wizard"
        self.assets = self.root / "_assets"
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        cleaned = slug(job_id)
        if cleaned != job_id:
            raise ValueError("Invalid job id")
        job_id = cleaned
        result = (self.root / job_id).resolve()
        if result.parent != self.root.resolve():
            raise ValueError("Invalid job id")
        return result

    def source_path(self, source: dict[str, str]) -> Path:
        if source["type"] == "lys":
            mapping = {"anchor_1": "anchor 1.png", "anchor_2": "anchor 2.png", "anchor_3": "anchor 3.png"}
            if source["id"] not in mapping:
                raise ValueError("Unknown Lys anchor")
            path = self.lys_root / mapping[source["id"]]
        else:
            path = self.assets / f"{slug(source['id'])}.png"
        if not path.is_file():
            raise FileNotFoundError(f"Source image is unavailable: {source['id']}")
        return path


class WizardService:
    def __init__(self, lys_root: Path, api_url: str = API_DEFAULT, transport: ComfyTransport | None = None):
        self.paths = WizardPaths(lys_root)
        self.api_url = api_url.rstrip("/")
        self.transport = transport or LocalComfyTransport(self.api_url)
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._cancel: dict[str, threading.Event] = {}
        self._recover_interrupted()

    def comfy_status(self) -> dict[str, Any]:
        try:
            stats = self.transport.get("/system_stats")
            info = self.transport.get("/object_info/ExpressionEditor")
            return {"online": bool(info.get("ExpressionEditor")), "api_url": self.api_url, "system": stats.get("system", {})}
        except Exception as exc:
            return {"online": False, "api_url": self.api_url, "error": str(exc)}

    def schema(self) -> dict[str, Any]:
        return live_expression_schema(self.api_url, self.transport)

    def run_smoke_test(self, request: dict[str, Any]) -> dict[str, Any]:
        """Run a constrained core ComfyUI text-to-image check and keep the result locally."""
        prompt_id, history, config = self.transport.smoke_test(request)
        test_id = str(config["test_id"])
        smoke_root = self.paths.root / "_smoke_tests"
        destination = smoke_root / f"{test_id}.png"
        output = self.transport.materialize_image(history, destination, node_id=str(config["output_node_id"]))
        width, height = safe_image_dimensions(destination)
        return {
            "ok": True,
            "test_id": test_id,
            "prompt_id": prompt_id,
            "config": config,
            "image": {
                "path": str(destination),
                "url": f"/api/manage/smoke-tests/{destination.name}",
                "width": width,
                "height": height,
                "sha256": sha256_file(destination),
                "pixel_sha256": image_pixel_hash(destination),
                "comfy_output": output,
            },
        }

    def model_profiles(self) -> dict[str, Any]:
        object_info = self.transport.get("/object_info")
        checkpoint_schema = object_info.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {}).get("ckpt_name", [])
        installed = set(checkpoint_schema[0]) if checkpoint_schema and isinstance(checkpoint_schema[0], list) else set()
        profiles = []
        for profile in load_profiles():
            item = public_profile(profile)
            item["installed"] = item["checkpoint"]["filename"] in installed
            profiles.append(item)
        return {"profiles": profiles, "count": len(profiles)}

    def lys_sources(self) -> list[dict[str, Any]]:
        result = []
        for source_id, filename in (("anchor_1", "anchor 1.png"), ("anchor_2", "anchor 2.png"), ("anchor_3", "anchor 3.png")):
            path = self.paths.lys_root / filename
            if path.is_file():
                width, height = safe_image_dimensions(path)
                result.append(
                    {
                        "type": "lys",
                        "id": source_id,
                        "name": filename,
                        "width": width,
                        "height": height,
                        "sha256": sha256_file(path),
                        "url": f"/api/explore/sources/{source_id}",
                    }
                )
        return result

    def config(self) -> dict[str, Any]:
        status = self.comfy_status()
        schema = self.schema() if status["online"] else {
            "controls": default_parameter_schema(),
            "advanced": {
                "src_ratio": {"name": "src_ratio", "label": "Source ratio", "type": "FLOAT", "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01},
                "crop_factor": {"name": "crop_factor", "label": "Crop factor", "type": "FLOAT", "default": 1.7, "min": 1.5, "max": 2.5, "step": 0.1},
            },
            "forced": {"sample_ratio": 0.0, "sample_parts": "OnlyExpression"},
        }
        return {"schema_version": 1, "comfyui": status, "parameters": schema, "sources": self.lys_sources(), "max_upload_bytes": MAX_UPLOAD_BYTES}

    def add_upload(self, data: bytes, filename: str) -> dict[str, Any]:
        asset_id = f"upload_{sha256_bytes(data)[:16]}"
        destination = self.paths.assets / f"{asset_id}.png"
        metadata = canonicalize_upload(data, destination)
        record = {
            "type": "upload",
            "id": asset_id,
            "name": Path(filename or "portrait").name,
            "width": metadata["width"],
            "height": metadata["height"],
            "sha256": metadata["sha256"],
            "url": f"/api/explore/assets/{asset_id}",
            "created_at": utc_now(),
        }
        write_json(self.paths.assets / f"{asset_id}.json", record)
        return record

    def source_asset(self, source_id: str) -> Path:
        if source_id in {"anchor_1", "anchor_2", "anchor_3"}:
            return self.paths.source_path({"type": "lys", "id": source_id})
        return self.paths.source_path({"type": "upload", "id": source_id})

    def _unique_job_id(self, mode: str, request: dict[str, Any]) -> str:
        detail = mode
        if mode == "sweep":
            detail = f"sweep_{request.get('sweep', {}).get('parameter', 'control')}"
        elif mode == "grid":
            grid = request.get("grid", {})
            detail = f"grid_{grid.get('x', {}).get('parameter', 'x')}_{grid.get('y', {}).get('parameter', 'y')}"
        base = slug(f"{now_id()}_{detail}")
        candidate, suffix = base, 2
        while (self.paths.root / candidate).exists():
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _active_job(self) -> str | None:
        for summary in self.list_jobs():
            if summary["status"] in ACTIVE_STATES:
                return summary["job_id"]
        return None

    def create_job(self, request: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            active = self._active_job()
            if active:
                raise RuntimeError(f"Expression Wizard is already generating {active}")
            if not self.comfy_status()["online"]:
                raise ConnectionError("ComfyUI is offline or ExpressionEditor is unavailable")
            normalized = validate_job_request(request, self.schema())
            source_path = self.paths.source_path(normalized["source"])
            job_id = self._unique_job_id(normalized["mode"], request)
            job_dir = self.paths.job_dir(job_id)
            for name in ("input", "images", "exp_data", "prompts"):
                (job_dir / name).mkdir(parents=True, exist_ok=True)
            source_copy = job_dir / "input" / "source.png"
            copy_verified(source_path, source_copy)
            width, height = safe_image_dimensions(source_copy)
            spec = {
                **normalized,
                "job_id": job_id,
                "created_at": utc_now(),
                "source": {
                    **normalized["source"],
                    "name": source_path.name,
                    "width": width,
                    "height": height,
                    "sha256": sha256_file(source_copy),
                    "path": "input/source.png",
                },
            }
            write_json(job_dir / "batch_spec.json", spec)
            manifest = self._initial_manifest(spec)
            write_json(job_dir / "manifest.json", manifest)
            job = {
                "schema_version": 1,
                "job_id": job_id,
                "mode": normalized["mode"],
                "status": "queued",
                "created_at": spec["created_at"],
                "started_at": None,
                "completed_at": None,
                "updated_at": utc_now(),
                "completed_candidates": 0,
                "total_candidates": 12,
                "current_candidate": None,
                "error": None,
                "cancel_requested": False,
                "source": spec["source"],
            }
            write_json(job_dir / "job.json", job)
            self._start_worker(job_id)
            return self.get_job(job_id)

    def _initial_manifest(self, spec: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "job_id": spec["job_id"],
            "mode": spec["mode"],
            "created_at": spec["created_at"],
            "updated_at": utc_now(),
            "source": spec["source"],
            "fixed": spec["fixed"],
            "candidates": [],
        }

    def _start_worker(self, job_id: str) -> None:
        event = threading.Event()
        self._cancel[job_id] = event
        self._worker = threading.Thread(target=self._run_job, args=(job_id, event), daemon=True, name=f"expression-wizard-{job_id}")
        self._worker.start()

    def _update_job(self, job_id: str, **changes: Any) -> dict[str, Any]:
        path = self.paths.job_dir(job_id) / "job.json"
        job = read_json(path)
        job.update(changes)
        job["updated_at"] = utc_now()
        write_json(path, job)
        return job

    def _run_job(self, job_id: str, cancel: threading.Event) -> None:
        job_dir = self.paths.job_dir(job_id)
        try:
            spec = read_json(job_dir / "batch_spec.json")
            manifest_path = job_dir / "manifest.json"
            manifest = read_json(manifest_path)
            completed = {item["candidate_id"] for item in manifest["candidates"]}
            self._update_job(job_id, status="running", started_at=read_json(job_dir / "job.json").get("started_at") or utc_now(), error=None)
            source_path = job_dir / spec["source"]["path"]
            input_name = f"Expression_Wizard/{spec['source']['sha256'][:16]}_{job_id}.png"
            input_name = self.transport.stage_input(source_path, input_name)
            for candidate in spec["candidates"]:
                if candidate["id"] in completed:
                    continue
                if cancel.is_set():
                    self._update_job(job_id, status="cancelled", completed_at=utc_now(), current_candidate=None, cancel_requested=True)
                    return
                candidate_id = candidate["id"]
                self._update_job(job_id, current_candidate=candidate_id)
                parameters = dict(spec["fixed"])
                parameters.update(candidate["controls"])
                exp_name = slug(f"expression_wizard_{job_id}_{candidate_id}")
                prompt = build_expression_prompt(
                    anchor_input_name=input_name,
                    sample_input_name=None,
                    parameters=parameters,
                    save_prefix=f"Expression_Wizard/{job_id}/{candidate_id}",
                    exp_file_name=exp_name,
                )
                prompt_path = job_dir / "prompts" / f"{candidate_id}.json"
                write_json(prompt_path, prompt)
                prompt_id, history = self.transport.execute(prompt, timeout=300.0)
                image_path = job_dir / "images" / f"{candidate_id}.png"
                self.transport.materialize_image(history, image_path)
                dimensions = safe_image_dimensions(image_path)
                expected = (int(spec["source"]["width"]), int(spec["source"]["height"]))
                if dimensions != expected:
                    raise RuntimeError(f"{candidate_id} changed canvas size from {expected} to {dimensions}")
                exp_binary = job_dir / "exp_data" / f"{candidate_id}.exp"
                exp_json = job_dir / "exp_data" / f"{candidate_id}.json"
                exp_csv = job_dir / "exp_data" / f"{candidate_id}.csv"
                expression = self.transport.export_expression(exp_name, exp_binary, exp_json, exp_csv)
                record = {
                    "candidate_id": candidate_id,
                    "label": candidate["label"],
                    "controls": candidate["controls"],
                    "effective_parameters": parameters,
                    "grid_position": candidate.get("grid_position"),
                    "image": f"images/{candidate_id}.png",
                    "image_url": f"/api/explore/jobs/{job_id}/files/images/{candidate_id}.png",
                    "width": dimensions[0],
                    "height": dimensions[1],
                    "exp_binary": f"exp_data/{candidate_id}.exp",
                    "exp_json": f"exp_data/{candidate_id}.json",
                    "exp_csv": f"exp_data/{candidate_id}.csv",
                    "workflow": f"prompts/{candidate_id}.json",
                    "prompt_id": prompt_id,
                    "pixel_hash": image_pixel_hash(image_path),
                    "expression_hash": expression["expression_hash"],
                }
                manifest = read_json(manifest_path)
                manifest["candidates"] = [item for item in manifest["candidates"] if item["candidate_id"] != candidate_id] + [record]
                order = {item["id"]: index for index, item in enumerate(spec["candidates"])}
                manifest["candidates"].sort(key=lambda item: order[item["candidate_id"]])
                manifest["updated_at"] = utc_now()
                write_json(manifest_path, manifest)
                self._update_job(job_id, completed_candidates=len(manifest["candidates"]), current_candidate=None)
            write_json(job_dir / "analysis.json", analyze_job_dir(job_dir))
            self._update_job(job_id, status="completed", completed_at=utc_now(), completed_candidates=12, current_candidate=None)
        except Exception as exc:
            write_json(job_dir / "error.json", {"error": str(exc), "traceback": traceback.format_exc(), "at": utc_now()})
            self._update_job(job_id, status="failed", completed_at=utc_now(), current_candidate=None, error=str(exc))
        finally:
            self._cancel.pop(job_id, None)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.get_job(job_id)
            if job["status"] not in ACTIVE_STATES:
                raise ValueError(f"Job {job_id} is not active")
            event = self._cancel.get(job_id)
            if event:
                event.set()
            self._update_job(job_id, cancel_requested=True)
            return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            active = self._active_job()
            if active:
                raise RuntimeError(f"Expression Wizard is already generating {active}")
            job = self.get_job(job_id)
            if job["status"] not in {"failed", "cancelled", "interrupted"}:
                raise ValueError("Only failed, cancelled, or interrupted jobs can be resumed")
            self._update_job(job_id, status="queued", completed_at=None, error=None, cancel_requested=False)
            self._start_worker(job_id)
            return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job_dir = self.paths.job_dir(job_id)
        if not (job_dir / "job.json").is_file():
            raise FileNotFoundError(f"Unknown Expression Wizard job: {job_id}")
        job = read_json(job_dir / "job.json")
        manifest = read_json(job_dir / "manifest.json") if (job_dir / "manifest.json").is_file() else {"candidates": []}
        job["candidates"] = manifest.get("candidates", [])
        job["manifest_url"] = f"/api/explore/jobs/{job_id}/files/manifest.json"
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = []
        if not self.paths.root.is_dir():
            return jobs
        for path in self.paths.root.iterdir():
            if not path.is_dir() or path.name.startswith("_") or not (path / "job.json").is_file():
                continue
            try:
                job = read_json(path / "job.json")
                jobs.append({key: job.get(key) for key in ("job_id", "mode", "status", "created_at", "updated_at", "completed_candidates", "total_candidates", "source", "error")})
            except Exception:
                continue
        jobs.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return jobs

    def _recover_interrupted(self) -> None:
        for job in self.list_jobs():
            if job["status"] in ACTIVE_STATES:
                self._update_job(job["job_id"], status="interrupted", completed_at=utc_now(), current_candidate=None, error="Backend stopped before the batch completed")

    def analysis(self, job_id: str) -> dict[str, Any]:
        job_dir = self.paths.job_dir(job_id)
        analysis = analyze_job_dir(job_dir)
        write_json(job_dir / "analysis.json", analysis)
        return analysis

    def workflow(self, job_id: str, candidate_id: str) -> dict[str, Any]:
        path = self.paths.job_dir(job_id) / "prompts" / f"{slug(candidate_id)}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Workflow is unavailable for {candidate_id}")
        return read_json(path)

    def validate_workflow(self, workflow: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(workflow, dict) or not workflow:
            raise ValueError("Workflow must be a non-empty Comfy API prompt object")
        object_info = self.transport.get("/object_info")
        errors, warnings = [], []
        for node_id, node in workflow.items():
            if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
                errors.append({"node": node_id, "error": "Node must contain class_type and inputs"})
                continue
            class_type = node["class_type"]
            if class_type not in object_info:
                errors.append({"node": node_id, "class_type": class_type, "error": "Unknown node class"})
                continue
            definition = object_info[class_type]["input"]
            allowed = set(definition.get("required", {})) | set(definition.get("optional", {})) | set(definition.get("hidden", {}))
            for input_name in node["inputs"]:
                if input_name not in allowed:
                    errors.append({"node": node_id, "class_type": class_type, "input": input_name, "error": "Unknown input"})
            for required in definition.get("required", {}):
                if required not in node["inputs"]:
                    errors.append({"node": node_id, "class_type": class_type, "input": required, "error": "Missing required input"})
            for input_name, value in node["inputs"].items():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] not in workflow:
                    errors.append({"node": node_id, "input": input_name, "error": f"References missing node {value[0]}"})
        if not any(node.get("class_type") == "SaveImage" for node in workflow.values() if isinstance(node, dict)):
            warnings.append("Workflow has no SaveImage node")
        return {"valid": not errors, "errors": errors, "warnings": warnings, "node_count": len(workflow)}


def _flatten_e(payload: dict[str, Any]) -> list[float]:
    return [float(value) for landmark in payload["e"][0] for value in landmark]


def _vector(value: Any) -> list[float]:
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_vector(item))
        return result
    if isinstance(value, (int, float)):
        return [float(value)]
    return []


def _fit_line(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(set(xs)) < 2:
        return None
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    sst = sum((y - mean_y) ** 2 for y in ys)
    return 1.0 if sst == 0 and sse == 0 else (1.0 - sse / sst if sst else 0.0)


def analyze_job_dir(job_dir: Path) -> dict[str, Any]:
    spec = read_json(job_dir / "batch_spec.json")
    manifest = read_json(job_dir / "manifest.json")
    rows = []
    strengths = []
    for candidate in manifest.get("candidates", []):
        exp = read_json(job_dir / candidate["exp_json"])
        flat = _flatten_e(exp)
        strength = math.sqrt(sum(value * value for value in flat))
        strengths.append(strength)
        affected = [entry["code"] for entry in exp["codes"] if abs(float(entry["value"])) > 1e-8]
        declared = set(candidate["controls"])
        violations = []
        for name, fixed_value in spec["fixed"].items():
            if name in declared:
                continue
            if candidate["effective_parameters"].get(name) != fixed_value:
                violations.append({"parameter": name, "expected": fixed_value, "actual": candidate["effective_parameters"].get(name)})
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "controls": candidate["controls"],
                "tensor_l2_delta": strength,
                "affected_codes": affected,
                "rotation": _vector(exp.get("r")),
                "scale": _vector(exp.get("s")),
                "translation": _vector(exp.get("t")),
                "fixed_control_violations": violations,
            }
        )
    maximum = max(strengths, default=0.0)
    for row in rows:
        row["normalized_strength"] = row["tensor_l2_delta"] / maximum if maximum else 0.0
    linearity = None
    if spec["mode"] == "sweep" and len(rows) >= 3:
        parameter = spec["request"]["sweep"]["parameter"]
        xs = [float(row["controls"][parameter]) for row in rows]
        ys = [float(row["tensor_l2_delta"]) for row in rows]
        linearity = {"parameter": parameter, "r_squared_strength": _fit_line(xs, ys)}
    return {
        "schema_version": 1,
        "job_id": spec["job_id"],
        "created_at": utc_now(),
        "baseline": "zero ExpressionEditor delta",
        "candidate_count": len(rows),
        "linearity": linearity,
        "candidates": rows,
    }


def rest_request(method: str, path: str, payload: Any | None = None, base_url: str | None = None) -> Any:
    base_url = (base_url or os.environ.get("EXPRESSION_WIZARD_URL") or "http://127.0.0.1:8765").rstrip("/")
    data = None
    headers = {}
    token = os.environ.get("EXPRESSION_WIZARD_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=610) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(raw).get("error", raw)
        except Exception:
            message = raw
        raise RuntimeError(f"Expression Wizard HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Expression Wizard is not running at {base_url}") from exc

