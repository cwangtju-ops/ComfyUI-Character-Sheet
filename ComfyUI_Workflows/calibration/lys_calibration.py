from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
API_DEFAULT = "http://127.0.0.1:8188"
MANUAL_CONTROLS = (
    "rotate_pitch",
    "rotate_yaw",
    "rotate_roll",
    "blink",
    "eyebrow",
    "wink",
    "pupil_x",
    "pupil_y",
    "aaa",
    "eee",
    "woo",
    "smile",
)
CONTROL_RANGES = {
    "rotate_pitch": (-20.0, 20.0),
    "rotate_yaw": (-20.0, 20.0),
    "rotate_roll": (-20.0, 20.0),
    "blink": (-20.0, 5.0),
    "eyebrow": (-10.0, 15.0),
    "wink": (0.0, 25.0),
    "pupil_x": (-15.0, 15.0),
    "pupil_y": (-15.0, 15.0),
    "aaa": (-30.0, 120.0),
    "eee": (-20.0, 15.0),
    "woo": (-20.0, 15.0),
    "smile": (-0.3, 1.3),
}
EDITOR_DEFAULTS = {
    **{name: 0.0 for name in MANUAL_CONTROLS},
    "src_ratio": 1.0,
    "sample_ratio": 0.0,
    "sample_parts": "OnlyExpression",
    "crop_factor": 1.7,
}
SAMPLE_PARTS = {"OnlyExpression", "OnlyRotation", "OnlyMouth", "OnlyEyes", "All"}
RECIPE_INTERACTIONS = {
    "soft_smile": {"primary": "smile", "secondary": "blink", "mask": "subtle"},
    "warm_intimate_smile": {"primary": "smile", "secondary": "aaa", "mask": "midface"},
    "playful_teasing": {
        "primary": "smile",
        "secondary": "wink",
        "mask": "midface",
        "compensate_wink": True,
    },
    "suspicious_mocking_teasing": {
        "primary": "eyebrow",
        "secondary": "wink",
        "mask": "midface",
        "compensate_wink": True,
    },
    "restrained_anger": {"primary": "eyebrow", "secondary": "blink", "mask": "midface"},
    "eyes_closed_trusting": {"primary": "blink", "secondary": "smile", "mask": "subtle"},
    "quiet_longing_parted_lips": {"primary": "blink", "secondary": "aaa", "mask": "midface"},
    "open_mouth_smile": {"primary": "smile", "secondary": "aaa", "mask": "openjaw"},
    "surprise": {
        "primary": "blink",
        "secondary": "aaa",
        "fixed_from_atlas": {"eyebrow": "best_positive"},
        "mask": "openjaw",
    },
    "speaking_aaa_eee": {"primary": "aaa", "secondary": "eee", "mask": "openjaw"},
    "speaking_aaa_woo": {"primary": "aaa", "secondary": "woo", "mask": "openjaw"},
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    if not cleaned:
        raise ValueError("Value cannot be converted to a safe identifier")
    return cleaned


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def copy_verified(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and sha256_file(source) == sha256_file(destination):
        return
    shutil.copy2(source, destination)


@dataclass(frozen=True)
class ProjectPaths:
    calibration_dir: Path
    workflow_dir: Path
    lys_root: Path
    generated_root: Path
    specs_dir: Path
    recipe_library: Path

    @classmethod
    def discover(cls, script_path: Path | None = None) -> "ProjectPaths":
        calibration_dir = (script_path or Path(__file__)).resolve().parent
        workflow_dir = calibration_dir.parent
        lys_root = workflow_dir.parent
        generated_root = lys_root / "ComfyUI_Generated" / "Calibration"
        return cls(
            calibration_dir=calibration_dir,
            workflow_dir=workflow_dir,
            lys_root=lys_root,
            generated_root=generated_root,
            specs_dir=calibration_dir / "specs",
            recipe_library=calibration_dir / "recipe_library.json",
        )


class ComfyClient:
    def __init__(self, api_url: str = API_DEFAULT, timeout: float = 15.0):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, route: str, payload: Any | None = None) -> Any:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.api_url + route, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ComfyUI API request failed: {method} {route}: {exc}") from exc

    def get(self, route: str) -> Any:
        return self._request("GET", route)

    def post(self, route: str, payload: Any) -> Any:
        return self._request("POST", route, payload)

    def system_paths(self) -> tuple[Path, Path]:
        stats = self.get("/system_stats")
        argv = stats["system"].get("argv", [])

        def argument_path(flag: str) -> Path:
            if flag not in argv:
                raise RuntimeError(f"Running ComfyUI did not report {flag}")
            index = argv.index(flag)
            if index + 1 >= len(argv):
                raise RuntimeError(f"Running ComfyUI reported an empty {flag}")
            return Path(argv[index + 1]).resolve()

        return argument_path("--input-directory"), argument_path("--output-directory")

    def queue(self, prompt: dict[str, Any]) -> str:
        response = self.post("/prompt", {"prompt": prompt})
        if response.get("node_errors"):
            raise RuntimeError(f"ComfyUI rejected prompt: {response['node_errors']}")
        prompt_id = response.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI returned no prompt_id: {response}")
        return str(prompt_id)

    def wait(self, prompt_id: str, timeout: float = 180.0, poll: float = 0.5) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            history = self.get(f"/history/{prompt_id}")
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") != "success":
                    raise RuntimeError(f"ComfyUI execution failed: {status}")
                return entry
            time.sleep(poll)
        raise TimeoutError(f"Timed out waiting for ComfyUI prompt {prompt_id}")


def plugin_revision() -> str:
    candidates = []
    if os.environ.get("ADVANCED_LIVEPORTRAIT_DIR"):
        candidates.append(Path(os.environ["ADVANCED_LIVEPORTRAIT_DIR"]))
    if os.environ.get("COMFYUI_DIR"):
        candidates.append(Path(os.environ["COMFYUI_DIR"]) / "custom_nodes" / "ComfyUI-AdvancedLivePortrait")
    for node_dir in candidates:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=node_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return result.stdout.strip()
        except Exception:
            source = node_dir / "nodes.py"
            if source.is_file():
                return f"nodes.py:{sha256_file(source)}"
    return "unknown"


def validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported batch spec schema: {spec.get('schema_version')}")
    spec["batch_id"] = slug(spec.get("batch_id", ""))
    if not spec.get("purpose"):
        raise ValueError("batch spec requires purpose")
    if not spec.get("anchor"):
        raise ValueError("batch spec requires anchor")
    candidates = spec.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 12:
        raise ValueError("Every calibration batch must contain exactly 12 candidates")
    fixed = dict(EDITOR_DEFAULTS)
    fixed.update(spec.get("fixed", {}))
    if fixed["sample_parts"] not in SAMPLE_PARTS:
        raise ValueError(f"Invalid sample_parts: {fixed['sample_parts']}")
    for key in EDITOR_DEFAULTS:
        if key not in fixed:
            raise ValueError(f"Missing fixed editor parameter: {key}")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in candidates:
        candidate_id = slug(raw.get("id", ""))
        if candidate_id in seen:
            raise ValueError(f"Duplicate candidate id: {candidate_id}")
        seen.add(candidate_id)
        controls = raw.get("controls", {})
        if not isinstance(controls, dict):
            raise ValueError(f"Candidate {candidate_id} controls must be an object")
        for key, value in controls.items():
            if key not in MANUAL_CONTROLS:
                raise ValueError(f"Candidate {candidate_id} uses unsupported control {key}")
            low, high = CONTROL_RANGES[key]
            if not isinstance(value, (int, float)) or not low <= float(value) <= high:
                raise ValueError(f"Candidate {candidate_id} {key}={value} outside [{low}, {high}]")
        normalized.append(
            {
                "id": candidate_id,
                "label": str(raw.get("label") or candidate_id),
                "controls": {key: float(value) for key, value in controls.items()},
            }
        )
    spec["fixed"] = fixed
    spec["candidates"] = normalized
    spec.setdefault("target", spec["batch_id"])
    spec.setdefault("sample", None)
    spec.setdefault("stage", "raw_calibration")
    spec.setdefault("refinement_round", 0)
    return spec


def load_spec(path: Path) -> dict[str, Any]:
    return validate_spec(read_json(path))


def effective_parameters(spec: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    parameters = dict(spec["fixed"])
    parameters.update(candidate["controls"])
    return parameters


def build_expression_prompt(
    *,
    anchor_input_name: str,
    sample_input_name: str | None,
    parameters: dict[str, Any],
    save_prefix: str,
    exp_file_name: str,
) -> dict[str, Any]:
    editor_inputs: dict[str, Any] = {
        "src_image": ["1", 0],
        **{name: parameters[name] for name in MANUAL_CONTROLS},
        "src_ratio": parameters["src_ratio"],
        "sample_ratio": parameters["sample_ratio"],
        "sample_parts": parameters["sample_parts"],
        "crop_factor": parameters["crop_factor"],
    }
    prompt: dict[str, Any] = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": anchor_input_name},
            "_meta": {"title": "Immutable calibration anchor"},
        },
        "2": {
            "class_type": "ExpressionEditor",
            "inputs": editor_inputs,
            "_meta": {"title": "Calibrated expression controls"},
        },
        "3": {
            "class_type": "SaveImage",
            "inputs": {"images": ["2", 0], "filename_prefix": save_prefix},
            "_meta": {"title": "Save raw calibration candidate"},
        },
        "4": {
            "class_type": "SaveExpData",
            "inputs": {"save_exp": ["2", 2], "file_name": exp_file_name},
            "_meta": {"title": "Save hidden expression tensor"},
        },
    }
    if sample_input_name:
        prompt["5"] = {
            "class_type": "LoadImage",
            "inputs": {"image": sample_input_name},
            "_meta": {"title": "Optional sampled expression"},
        }
        editor_inputs["sample_image"] = ["5", 0]
    return prompt


class _ExpressionSetStub:
    pass


def load_exp(path: Path) -> Any:
    try:
        import dill
    except ImportError as exc:
        raise RuntimeError("Expression export requires the active ComfyUI Python environment (dill missing)") from exc

    class CompatUnpickler(dill.Unpickler):
        def find_class(self, module: str, name: str) -> Any:
            if name == "ExpressionSet" and module.endswith("ComfyUI-AdvancedLivePortrait.nodes"):
                return _ExpressionSetStub
            return super().find_class(module, name)

    with path.open("rb") as handle:
        return CompatUnpickler(handle).load()


def _primitive(value: Any) -> Any:
    if hasattr(value, "detach"):
        tensor = value.detach().cpu()
        return tensor.item() if tensor.numel() == 1 else tensor.tolist()
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    if hasattr(value, "tolist"):
        return value.tolist()
    return float(value)


def export_exp(exp_path: Path, json_path: Path, csv_path: Path) -> dict[str, Any]:
    expression = load_exp(exp_path)
    e = _primitive(expression.e)
    r = _primitive(expression.r)
    s = _primitive(expression.s)
    t = _primitive(expression.t)
    if len(e) != 1 or len(e[0]) != 21 or any(len(axis) != 3 for axis in e[0]):
        raise ValueError(f"Unexpected expression tensor shape in {exp_path}")
    codes = []
    for index, coordinates in enumerate(e[0]):
        for axis, value in enumerate(coordinates):
            codes.append(
                {
                    "code": index * 10 + axis,
                    "landmark_index": index,
                    "axis": axis,
                    "value": float(value),
                    "value_x1000": float(value) * 1000.0,
                }
            )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_file": exp_path.name,
        "e_shape": [1, 21, 3],
        "e": e,
        "r": r,
        "s": s,
        "t": t,
        "codes": codes,
        "expression_hash": sha256_json({"e": e, "r": r, "s": s, "t": t}),
    }
    write_json(json_path, payload)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=("code", "landmark_index", "axis", "value", "value_x1000")
        )
        writer.writeheader()
        writer.writerows(codes)
    return payload


def image_pixel_hash(path: Path) -> str:
    try:
        from PIL import Image
    except ImportError:
        return sha256_file(path)
    with Image.open(path) as image:
        normalized = image.convert("RGBA")
        digest = hashlib.sha256()
        digest.update(f"{normalized.width}x{normalized.height}:RGBA".encode("ascii"))
        digest.update(normalized.tobytes())
        return digest.hexdigest()


def output_image_from_history(entry: dict[str, Any], node_id: str = "3") -> dict[str, str]:
    images = entry.get("outputs", {}).get(node_id, {}).get("images", [])
    if not images:
        raise RuntimeError(f"ComfyUI history contains no image for node {node_id}")
    return images[0]


def reviewer_html(manifest: dict[str, Any]) -> str:
    embedded = json.dumps(manifest, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Lys calibration review — {manifest['batch_id']}</title>
<style>
:root{{--bg:#15171b;--panel:#20242a;--ink:#f2f2f2;--muted:#aeb4be;--accent:#bc5361;--line:#383e47}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px system-ui,sans-serif}}
header{{position:sticky;top:0;z-index:5;background:#121419ee;border-bottom:1px solid var(--line);padding:12px 18px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
h1{{font-size:18px;margin:0 auto 0 0}}button,.button{{background:#303640;color:var(--ink);border:1px solid #4b5360;border-radius:6px;padding:8px 11px;cursor:pointer}}button.primary{{background:#7f3540;border-color:#a94957}}
main{{display:grid;grid-template-columns:280px 1fr;gap:18px;padding:18px}}aside{{position:sticky;top:76px;height:max-content;background:var(--panel);padding:12px;border-radius:10px}}aside img{{width:100%;display:block;border-radius:6px}}.muted{{color:var(--muted)}}
#grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}}.card.keep{{border-color:#4ea56b;box-shadow:0 0 0 1px #4ea56b}}.card img{{display:block;width:100%;aspect-ratio:1;object-fit:contain;background:#bbb;cursor:zoom-in}}.content{{padding:11px}}.candidate-id{{font-weight:650}}.parameters{{font:12px ui-monospace,monospace;color:#f0b5bd;background:#17191e;padding:7px;border-radius:5px;margin:7px 0;white-space:pre-wrap}}body.hide-parameters .parameters{{display:none}}.scores{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}}label{{display:block;color:var(--muted);font-size:12px}}select,textarea{{width:100%;margin-top:3px;background:#17191e;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:6px}}textarea{{min-height:58px;resize:vertical}}.keep-row{{display:flex;align-items:center;gap:7px;margin:8px 0;color:var(--ink)}}.keep-row input{{width:18px;height:18px}}#progress{{font-variant-numeric:tabular-nums}}@media(max-width:850px){{main{{grid-template-columns:1fr}}aside{{position:static}}}}
</style></head><body class=\"hide-parameters\">
<header><h1>Lys calibration: {manifest['batch_id']}</h1><span id=\"progress\"></span><button id=\"toggle\">Reveal parameters</button><label class=\"button\">Import review<input id=\"import\" type=\"file\" accept=\"application/json\" hidden></label><button id=\"exportJson\" class=\"primary\">Export JSON</button><button id=\"exportCsv\">Export CSV</button></header>
<main><aside><img src=\"anchor.png\" alt=\"Canonical anchor\"><h2>Canonical anchor</h2><p>{manifest.get('purpose','')}</p><p class=\"muted\">Score 1–5; higher is always better. Review with parameters hidden first. Changes save automatically in this browser.</p></aside><section id=\"grid\"></section></main>
<script>const manifest={embedded};const key=`lys-review:${{manifest.batch_id}}`;let state=JSON.parse(localStorage.getItem(key)||'{{}}');
const order=manifest.review_order;const byId=Object.fromEntries(manifest.candidates.map(c=>[c.candidate_id,c]));const grid=document.querySelector('#grid');
function row(id){{return state[id]||(state[id]={{identity:null,expression:null,cleanliness:null,keep:false,notes:''}})}}
function save(){{localStorage.setItem(key,JSON.stringify(state));renderProgress()}}function renderProgress(){{let done=order.filter(id=>{{const r=row(id);return r.identity&&r.expression&&r.cleanliness}}).length;document.querySelector('#progress').textContent=`${{done}} / ${{order.length}} scored`}}
function score(label,name,r,id){{const wrap=document.createElement('label');wrap.textContent=label;const s=document.createElement('select');s.innerHTML='<option value=\"\">—</option>'+[1,2,3,4,5].map(v=>`<option>${{v}}</option>`).join('');s.value=r[name]??'';s.onchange=()=>{{r[name]=s.value?Number(s.value):null;save()}};wrap.append(s);return wrap}}
for(const id of order){{const c=byId[id],r=row(id),card=document.createElement('article');card.className='card'+(r.keep?' keep':'');const img=document.createElement('img');img.src=c.image;img.alt=id;img.onclick=()=>open(c.image,'_blank');const content=document.createElement('div');content.className='content';content.innerHTML=`<div class=\"candidate-id\">${{id}}</div><div class=\"parameters\">${{JSON.stringify(c.effective_parameters,null,2)}}</div>`;const scores=document.createElement('div');scores.className='scores';scores.append(score('Identity','identity',r,id),score('Expression','expression',r,id),score('Cleanliness','cleanliness',r,id));content.append(scores);const keep=document.createElement('label');keep.className='keep-row';keep.innerHTML='<input type=\"checkbox\"> Keep';keep.querySelector('input').checked=r.keep;keep.querySelector('input').onchange=e=>{{r.keep=e.target.checked;card.classList.toggle('keep',r.keep);save()}};content.append(keep);const notes=document.createElement('textarea');notes.placeholder='Optional notes';notes.value=r.notes;notes.oninput=()=>{{r.notes=notes.value;save()}};content.append(notes);card.append(img,content);grid.append(card)}}renderProgress();
document.querySelector('#toggle').onclick=e=>{{document.body.classList.toggle('hide-parameters');e.target.textContent=document.body.classList.contains('hide-parameters')?'Reveal parameters':'Hide parameters'}};
function payload(){{return{{schema_version:1,batch_id:manifest.batch_id,exported_at:new Date().toISOString(),reviews:order.map(candidate_id=>({{candidate_id,...row(candidate_id)}}))}}}}function download(name,type,text){{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{{type}}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}}
document.querySelector('#exportJson').onclick=()=>download(`${{manifest.batch_id}}_review.json`,'application/json',JSON.stringify(payload(),null,2));document.querySelector('#exportCsv').onclick=()=>{{const q=v=>'\"'+String(v??'').replaceAll('"','""')+'\"';let lines=['candidate_id,identity,expression,cleanliness,keep,notes'];for(const r of payload().reviews)lines.push([r.candidate_id,r.identity??'',r.expression??'',r.cleanliness??'',r.keep,r.notes].map(q).join(','));download(`${{manifest.batch_id}}_review.csv`,'text/csv',lines.join('\n'))}};
document.querySelector('#import').onchange=async e=>{{const data=JSON.parse(await e.target.files[0].text());if(data.batch_id!==manifest.batch_id){{alert('Review belongs to another batch');return}}state=Object.fromEntries(data.reviews.map(r=>[r.candidate_id,r]));localStorage.setItem(key,JSON.stringify(state));location.reload()}};
</script></body></html>"""


def write_reviewer(batch_dir: Path, manifest: dict[str, Any]) -> None:
    (batch_dir / "review.html").write_text(reviewer_html(manifest), encoding="utf-8")


def generate_batch(
    spec_path: Path,
    *,
    api_url: str = API_DEFAULT,
    force: bool = False,
    timeout: float = 180.0,
    paths: ProjectPaths | None = None,
) -> Path:
    paths = paths or ProjectPaths.discover()
    spec = load_spec(spec_path)
    client = ComfyClient(api_url)
    input_root, output_root = client.system_paths()
    batch_dir = paths.generated_root / spec["batch_id"]
    for name in ("images", "exp_data", "prompts"):
        (batch_dir / name).mkdir(parents=True, exist_ok=True)

    anchor_source = (paths.lys_root / spec["anchor"]).resolve()
    if anchor_source.parent != paths.lys_root.resolve() or not anchor_source.is_file():
        raise ValueError(f"Anchor must be an existing file directly under {paths.lys_root}")
    anchor_hash = sha256_file(anchor_source)
    staged_dir = input_root / "Lys_Calibration"
    anchor_input = f"Lys_Calibration/{anchor_hash[:12]}_{slug(anchor_source.stem)}{anchor_source.suffix.lower()}"
    copy_verified(anchor_source, input_root / Path(anchor_input))
    copy_verified(anchor_source, batch_dir / "anchor.png")

    sample_input = None
    sample_hash = None
    if spec.get("sample"):
        sample_source = (paths.lys_root / spec["sample"]).resolve()
        if sample_source.parent != paths.lys_root.resolve() or not sample_source.is_file():
            raise ValueError(f"Sample must be an existing file directly under {paths.lys_root}")
        sample_hash = sha256_file(sample_source)
        sample_input = f"Lys_Calibration/{sample_hash[:12]}_{slug(sample_source.stem)}{sample_source.suffix.lower()}"
        copy_verified(sample_source, input_root / Path(sample_input))

    manifest_path = batch_dir / "manifest.json"
    existing = read_json(manifest_path) if manifest_path.is_file() else {}
    existing_by_id = {item["candidate_id"]: item for item in existing.get("candidates", [])}
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": spec["batch_id"],
        "purpose": spec["purpose"],
        "target": spec["target"],
        "stage": spec["stage"],
        "refinement_round": spec["refinement_round"],
        "created_at": existing.get("created_at", utc_now()),
        "updated_at": utc_now(),
        "source_spec": str(spec_path.resolve()),
        "spec_hash": sha256_json(spec),
        "anchor": {"name": spec["anchor"], "sha256": anchor_hash},
        "sample": {"name": spec["sample"], "sha256": sample_hash} if sample_hash else None,
        "advanced_liveportrait_revision": plugin_revision(),
        "comfy_api": api_url,
        "candidates": [],
    }
    for candidate in spec["candidates"]:
        candidate_id = candidate["id"]
        image_destination = batch_dir / "images" / f"{candidate_id}.png"
        exp_destination = batch_dir / "exp_data" / f"{candidate_id}.exp"
        exp_json_path = batch_dir / "exp_data" / f"{candidate_id}.json"
        exp_csv_path = batch_dir / "exp_data" / f"{candidate_id}.csv"
        prompt_path = batch_dir / "prompts" / f"{candidate_id}.json"
        if (
            not force
            and candidate_id in existing_by_id
            and image_destination.is_file()
            and exp_destination.is_file()
            and exp_json_path.is_file()
        ):
            manifest["candidates"].append(existing_by_id[candidate_id])
            continue

        parameters = effective_parameters(spec, candidate)
        exp_file_name = slug(f"lys_cal_{spec['batch_id']}_{candidate_id}")
        save_prefix = f"Lys_Comfy/Calibration/{spec['batch_id']}/{candidate_id}"
        prompt = build_expression_prompt(
            anchor_input_name=anchor_input,
            sample_input_name=sample_input,
            parameters=parameters,
            save_prefix=save_prefix,
            exp_file_name=exp_file_name,
        )
        write_json(prompt_path, prompt)
        prompt_id = client.queue(prompt)
        history = client.wait(prompt_id, timeout=timeout)
        output_image = output_image_from_history(history)
        source_image = output_root / output_image.get("subfolder", "") / output_image["filename"]
        copy_verified(source_image, image_destination)
        source_exp = output_root / "exp_data" / f"{exp_file_name}.exp"
        copy_verified(source_exp, exp_destination)
        exp_payload = export_exp(exp_destination, exp_json_path, exp_csv_path)
        item = {
            "candidate_id": candidate_id,
            "label": candidate["label"],
            "controls": candidate["controls"],
            "effective_parameters": parameters,
            "image": f"images/{candidate_id}.png",
            "exp_binary": f"exp_data/{candidate_id}.exp",
            "exp_json": f"exp_data/{candidate_id}.json",
            "exp_csv": f"exp_data/{candidate_id}.csv",
            "prompt": f"prompts/{candidate_id}.json",
            "prompt_id": prompt_id,
            "pixel_hash": image_pixel_hash(image_destination),
            "expression_hash": exp_payload["expression_hash"],
        }
        manifest["candidates"].append(item)
        manifest["updated_at"] = utc_now()
        write_json(manifest_path, manifest)

    review_order = [item["candidate_id"] for item in manifest["candidates"]]
    random.Random(int(hashlib.sha256(spec["batch_id"].encode()).hexdigest()[:16], 16)).shuffle(review_order)
    manifest["review_order"] = review_order
    if spec["stage"] == "baseline":
        manifest["repeatability"] = {
            "pixel_identical": len({item["pixel_hash"] for item in manifest["candidates"]}) == 1,
            "expression_identical": len({item["expression_hash"] for item in manifest["candidates"]}) == 1,
        }
    write_json(batch_dir / "batch_spec.json", spec)
    write_json(manifest_path, manifest)
    write_reviewer(batch_dir, manifest)
    return batch_dir


def flatten_expression(payload: dict[str, Any]) -> list[float]:
    return [float(value) for landmark in payload["e"][0] for value in landmark]


def _linear_fit(xs: list[float], ys: list[list[float]]) -> tuple[list[float], list[float], float]:
    dimensions = len(ys[0])
    mean_x = sum(xs) / len(xs)
    mean_y = [sum(row[d] for row in ys) / len(ys) for d in range(dimensions)]
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slopes = [0.0] * dimensions
    if denominator:
        for d in range(dimensions):
            slopes[d] = sum((x - mean_x) * (row[d] - mean_y[d]) for x, row in zip(xs, ys)) / denominator
    intercepts = [mean_y[d] - slopes[d] * mean_x for d in range(dimensions)]
    sse = sum(
        (row[d] - (intercepts[d] + slopes[d] * x)) ** 2
        for x, row in zip(xs, ys)
        for d in range(dimensions)
    )
    sst = sum((row[d] - mean_y[d]) ** 2 for row in ys for d in range(dimensions))
    r2 = 1.0 if sst == 0.0 and sse == 0.0 else (1.0 - sse / sst if sst else 0.0)
    return slopes, intercepts, r2


def analyze_batch(batch_dir: Path, baseline_dir: Path) -> dict[str, Any]:
    manifest = read_json(batch_dir / "manifest.json")
    baseline_manifest = read_json(baseline_dir / "manifest.json")
    baseline_item = baseline_manifest["candidates"][0]
    baseline = read_json(baseline_dir / baseline_item["exp_json"])
    baseline_e = flatten_expression(baseline)
    baseline_r = [float(v) for v in baseline["r"]]
    candidate_rows = []
    control_groups: dict[str, list[dict[str, Any]]] = {}
    for item in manifest["candidates"]:
        payload = read_json(batch_dir / item["exp_json"])
        values = flatten_expression(payload)
        delta = [value - base for value, base in zip(values, baseline_e)]
        rotation_delta = [float(value) - base for value, base in zip(payload["r"], baseline_r)]
        affected = [
            {"code": (index // 3) * 10 + index % 3, "delta": value, "delta_x1000": value * 1000.0}
            for index, value in enumerate(delta)
            if abs(value) > 1e-8
        ]
        active = [(name, value) for name, value in item["controls"].items() if abs(float(value)) > 1e-12]
        row = {
            "candidate_id": item["candidate_id"],
            "controls": item["controls"],
            "active_controls": active,
            "l2_tensor_delta": math.sqrt(sum(value * value for value in delta)),
            "rotation_delta": rotation_delta,
            "affected_codes": affected,
            "pixel_hash": item["pixel_hash"],
        }
        candidate_rows.append(row)
        if len(active) == 1:
            name, value = active[0]
            control_groups.setdefault(name, []).append(
                {"value": float(value), "tensor": values, "rotation": [float(v) for v in payload["r"]]}
            )

    summaries = {}
    for name, rows in control_groups.items():
        rows.sort(key=lambda row: row["value"])
        xs = [row["value"] for row in rows]
        slopes, _, r2 = _linear_fit(xs, [row["tensor"] for row in rows])
        rotation_slopes, _, rotation_r2 = _linear_fit(xs, [row["rotation"] for row in rows])
        summaries[name] = {
            "tested_values": xs,
            "tensor_linearity_r2": r2,
            "rotation_linearity_r2": rotation_r2,
            "tensor_slope_by_code": [
                {"code": (index // 3) * 10 + index % 3, "slope_per_unit": slope}
                for index, slope in enumerate(slopes)
                if abs(slope) > 1e-10
            ],
            "rotation_slope_per_unit": rotation_slopes,
        }
    analysis = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": manifest["batch_id"],
        "baseline_batch_id": baseline_manifest["batch_id"],
        "created_at": utc_now(),
        "candidates": candidate_rows,
        "controls": summaries,
    }
    write_json(batch_dir / "analysis.json", analysis)
    with (batch_dir / "analysis_candidates.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("candidate_id", "controls", "l2_tensor_delta", "rotation_pitch", "rotation_yaw", "rotation_roll", "affected_code_count"),
        )
        writer.writeheader()
        for row in candidate_rows:
            writer.writerow(
                {
                    "candidate_id": row["candidate_id"],
                    "controls": json.dumps(row["controls"], sort_keys=True),
                    "l2_tensor_delta": row["l2_tensor_delta"],
                    "rotation_pitch": row["rotation_delta"][0],
                    "rotation_yaw": row["rotation_delta"][1],
                    "rotation_roll": row["rotation_delta"][2],
                    "affected_code_count": len(row["affected_codes"]),
                }
            )
    return analysis


def review_weight(review: dict[str, Any]) -> float:
    return (
        0.4 * float(review.get("identity") or 0)
        + 0.4 * float(review.get("expression") or 0)
        + 0.2 * float(review.get("cleanliness") or 0)
    )


def validate_review(review: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    if review.get("schema_version") != SCHEMA_VERSION or review.get("batch_id") != manifest["batch_id"]:
        raise ValueError("Review schema or batch id does not match manifest")
    expected = {item["candidate_id"] for item in manifest["candidates"]}
    actual = {item.get("candidate_id") for item in review.get("reviews", [])}
    if expected != actual:
        raise ValueError("Review candidates do not exactly match the batch manifest")
    for item in review["reviews"]:
        for field in ("identity", "expression", "cleanliness"):
            value = item.get(field)
            if value is not None and (not isinstance(value, (int, float)) or not 1 <= value <= 5):
                raise ValueError(f"{item['candidate_id']} has invalid {field} score")
        item["keep"] = bool(item.get("keep", False))
        item["notes"] = str(item.get("notes", ""))
        item["weighted_score"] = review_weight(item)
        item["passes"] = bool(
            item["keep"]
            and all(float(item.get(field) or 0) >= 4 for field in ("identity", "expression", "cleanliness"))
        )
    return review


def ingest_review(batch_dir: Path, review_path: Path) -> dict[str, Any]:
    manifest = read_json(batch_dir / "manifest.json")
    review = validate_review(read_json(review_path), manifest)
    write_json(batch_dir / "review.json", review)
    with (batch_dir / "review.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ("candidate_id", "identity", "expression", "cleanliness", "keep", "weighted_score", "passes", "notes")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: item.get(field) for field in fields} for item in review["reviews"])
    return review


def build_control_atlas(batch_dirs: Iterable[Path], output_dir: Path) -> dict[str, Any]:
    controls: dict[str, list[dict[str, Any]]] = {}
    for batch_dir in batch_dirs:
        manifest = read_json(batch_dir / "manifest.json")
        review = read_json(batch_dir / "review.json")
        review_by_id = {item["candidate_id"]: item for item in review["reviews"]}
        analysis = read_json(batch_dir / "analysis.json") if (batch_dir / "analysis.json").is_file() else None
        analysis_by_id = {item["candidate_id"]: item for item in analysis["candidates"]} if analysis else {}
        for candidate in manifest["candidates"]:
            active = [(k, v) for k, v in candidate["controls"].items() if abs(float(v)) > 1e-12]
            if len(active) != 1:
                continue
            name, value = active[0]
            rating = review_by_id[candidate["candidate_id"]]
            controls.setdefault(name, []).append(
                {
                    "value": float(value),
                    "batch_id": manifest["batch_id"],
                    "candidate_id": candidate["candidate_id"],
                    "scores": {field: rating.get(field) for field in ("identity", "expression", "cleanliness")},
                    "keep": rating["keep"],
                    "passes": rating.get("passes", False),
                    "weighted_score": rating.get("weighted_score", review_weight(rating)),
                    "analysis": analysis_by_id.get(candidate["candidate_id"]),
                }
            )
    atlas_controls = {}
    for name, entries in controls.items():
        entries.sort(key=lambda item: item["value"])
        approved = [item for item in entries if item["passes"]]
        atlas_controls[name] = {
            "entries": entries,
            "approved_values": [item["value"] for item in approved],
            "best_value": max(entries, key=lambda item: item["weighted_score"])["value"] if entries else None,
        }
    atlas = {"schema_version": SCHEMA_VERSION, "created_at": utc_now(), "controls": atlas_controls}
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "control_atlas.json", atlas)
    with (output_dir / "control_atlas.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ("control", "value", "identity", "expression", "cleanliness", "keep", "passes", "weighted_score", "batch_id", "candidate_id")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for name, data in atlas_controls.items():
            for item in data["entries"]:
                writer.writerow(
                    {
                        "control": name,
                        "value": item["value"],
                        **item["scores"],
                        "keep": item["keep"],
                        "passes": item["passes"],
                        "weighted_score": item["weighted_score"],
                        "batch_id": item["batch_id"],
                        "candidate_id": item["candidate_id"],
                    }
                )
    return atlas


def _three_levels(values: list[float], label: str) -> list[float]:
    unique = sorted(set(float(value) for value in values))
    if len(unique) < 3:
        raise ValueError(f"Need at least three human-approved values for {label}; found {unique}")
    return [unique[0], unique[len(unique) // 2], unique[-1]]


def interaction_spec(recipe_name: str, atlas: dict[str, Any], anchor: str = "anchor 1.png") -> dict[str, Any]:
    if recipe_name not in RECIPE_INTERACTIONS:
        raise ValueError(f"Unknown interaction recipe: {recipe_name}")
    definition = RECIPE_INTERACTIONS[recipe_name]
    primary = definition["primary"]
    secondary = definition["secondary"]
    primary_values = _three_levels(atlas["controls"][primary]["approved_values"], primary)
    secondary_values = [0.0] + _three_levels(atlas["controls"][secondary]["approved_values"], secondary)
    fixed_controls: dict[str, float] = {}
    for control, strategy in definition.get("fixed_from_atlas", {}).items():
        entries = atlas["controls"][control]["entries"]
        if strategy == "best_positive":
            entries = [entry for entry in entries if entry["value"] > 0]
        if not entries:
            raise ValueError(f"No atlas values satisfy {strategy} for {control}")
        fixed_controls[control] = max(entries, key=lambda item: item["weighted_score"])["value"]
    candidates = []
    for p_index, primary_value in enumerate(primary_values, 1):
        for s_index, secondary_value in enumerate(secondary_values, 1):
            controls = {**fixed_controls, primary: primary_value}
            if secondary_value:
                controls[secondary] = secondary_value
            if definition.get("compensate_wink"):
                wink = float(controls.get("wink", 0.0))
                controls["rotate_yaw"] = -0.1 * wink
                controls["rotate_roll"] = 0.1 * wink
            candidates.append(
                {
                    "id": f"p{p_index}_s{s_index}",
                    "label": f"{primary}={primary_value:g}, {secondary}={secondary_value:g}",
                    "controls": controls,
                }
            )
    return validate_spec(
        {
            "schema_version": SCHEMA_VERSION,
            "batch_id": f"interaction_{recipe_name}_r0",
            "purpose": f"3x4 human-reviewed interaction grid for {recipe_name}",
            "target": recipe_name,
            "stage": "interaction",
            "refinement_round": 0,
            "anchor": anchor,
            "sample": None,
            "fixed": EDITOR_DEFAULTS,
            "mask_recommendation": definition["mask"],
            "interaction": {"primary": primary, "secondary": secondary, "fixed_controls": fixed_controls},
            "candidates": candidates,
        }
    )


def _neighbor_step(values: list[float], fallback: float) -> float:
    ordered = sorted(set(values))
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > a]
    return min(gaps) / 2.0 if gaps else fallback


def refinement_spec(batch_dir: Path) -> dict[str, Any]:
    spec = read_json(batch_dir / "batch_spec.json")
    review = read_json(batch_dir / "review.json")
    review = validate_review(review, read_json(batch_dir / "manifest.json"))
    if any(item["passes"] for item in review["reviews"]):
        raise ValueError("Batch already has an accepted candidate; refinement is not needed")
    round_number = int(spec.get("refinement_round", 0)) + 1
    if round_number > 2:
        raise RuntimeError("Two refinement rounds exhausted; stop and diagnose before further generation")
    interaction = spec.get("interaction")
    if not interaction:
        raise ValueError("Refinement requires an interaction batch")
    primary, secondary = interaction["primary"], interaction["secondary"]
    by_id = {item["candidate_id"]: item for item in review["reviews"]}
    best_candidate = max(spec["candidates"], key=lambda item: by_id[item["id"]]["weighted_score"])
    p_best = float(best_candidate["controls"].get(primary, 0.0))
    s_best = float(best_candidate["controls"].get(secondary, 0.0))
    p_values_old = [float(item["controls"].get(primary, 0.0)) for item in spec["candidates"]]
    s_values_old = [float(item["controls"].get(secondary, 0.0)) for item in spec["candidates"]]
    p_step = _neighbor_step(p_values_old, max(abs(p_best) * 0.25, 0.05))
    s_step = _neighbor_step(s_values_old, max(abs(s_best) * 0.25, 0.05))
    p_low, p_high = CONTROL_RANGES[primary]
    s_low, s_high = CONTROL_RANGES[secondary]
    p_values = [max(p_low, min(p_high, p_best + offset * p_step)) for offset in (-1, 0, 1)]
    s_values = [max(s_low, min(s_high, s_best + offset * s_step)) for offset in (-1.5, -0.5, 0.5, 1.5)]
    candidates = []
    fixed = dict(interaction.get("fixed_controls", {}))
    definition = RECIPE_INTERACTIONS[spec["target"]]
    for p_index, p_value in enumerate(p_values, 1):
        for s_index, s_value in enumerate(s_values, 1):
            controls = {**fixed, primary: p_value, secondary: s_value}
            if definition.get("compensate_wink"):
                wink = float(controls.get("wink", 0.0))
                controls["rotate_yaw"] = -0.1 * wink
                controls["rotate_roll"] = 0.1 * wink
            candidates.append(
                {"id": f"p{p_index}_s{s_index}", "label": f"refined {primary}={p_value:g}, {secondary}={s_value:g}", "controls": controls}
            )
    refined = dict(spec)
    refined["batch_id"] = f"interaction_{spec['target']}_r{round_number}"
    refined["purpose"] = f"Refinement round {round_number} around {best_candidate['id']}"
    refined["refinement_round"] = round_number
    refined["candidates"] = candidates
    return validate_spec(refined)


def load_recipe_library(path: Path) -> dict[str, Any]:
    if path.is_file():
        library = read_json(path)
        if library.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported recipe library schema")
        return library
    return {"schema_version": SCHEMA_VERSION, "updated_at": utc_now(), "recipes": {}}


def promote_recipe(batch_dir: Path, recipe_name: str, library_path: Path) -> dict[str, Any]:
    manifest = read_json(batch_dir / "manifest.json")
    spec = read_json(batch_dir / "batch_spec.json")
    review = validate_review(read_json(batch_dir / "review.json"), manifest)
    passing = [item for item in review["reviews"] if item["passes"]]
    if not passing:
        raise ValueError("No candidate meets the 4/5 + keep acceptance rule")
    winner = max(passing, key=lambda item: item["weighted_score"])
    candidate = next(item for item in manifest["candidates"] if item["candidate_id"] == winner["candidate_id"])
    recipe_key = slug(recipe_name)
    asset_dir = library_path.parent / "recipe_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    exp_asset = asset_dir / f"{recipe_key}.exp"
    copy_verified(batch_dir / candidate["exp_binary"], exp_asset)
    library = load_recipe_library(library_path)
    library["recipes"][recipe_key] = {
        "name": recipe_key,
        "target": spec["target"],
        "source_batch": manifest["batch_id"],
        "candidate_id": candidate["candidate_id"],
        "controls": candidate["controls"],
        "effective_parameters": candidate["effective_parameters"],
        "scores": {field: winner[field] for field in ("identity", "expression", "cleanliness", "weighted_score")},
        "exp_file": str(exp_asset.relative_to(library_path.parent)).replace("\\", "/"),
        "mask": spec.get("mask_recommendation", RECIPE_INTERACTIONS.get(spec["target"], {}).get("mask", "subtle")),
        "validated_views": [],
        "polish": {
            "scale": 1.5,
            "denoise_values": [0.08, 0.12, 0.16],
            "seed": 728451903,
            "checkpoint": "cyberrealisticPony_v180Coreshift.safetensors",
        },
        "approved_at": utc_now(),
    }
    library["updated_at"] = utc_now()
    write_json(library_path, library)
    return library["recipes"][recipe_key]


def build_replay_prompt(anchor_input: str, exp_name: str, save_prefix: str, src_ratio: float = 1.0) -> dict[str, Any]:
    parameters = dict(EDITOR_DEFAULTS)
    parameters["src_ratio"] = src_ratio
    prompt = build_expression_prompt(
        anchor_input_name=anchor_input,
        sample_input_name=None,
        parameters=parameters,
        save_prefix=save_prefix,
        exp_file_name=slug(save_prefix.replace("/", "_")),
    )
    prompt.pop("4")
    prompt["4"] = {
        "class_type": "LoadExpData",
        "inputs": {"file_name": exp_name, "ratio": 1.0},
        "_meta": {"title": "Load approved expression recipe"},
    }
    prompt["2"]["inputs"]["add_exp"] = ["4", 0]
    return prompt


def validate_recipe_views(
    recipe_name: str,
    *,
    api_url: str = API_DEFAULT,
    paths: ProjectPaths | None = None,
    anchors: tuple[str, ...] = ("anchor 1.png", "anchor 2.png", "anchor 3.png"),
) -> Path:
    paths = paths or ProjectPaths.discover()
    library = load_recipe_library(paths.recipe_library)
    recipe = library["recipes"][recipe_name]
    client = ComfyClient(api_url)
    input_root, output_root = client.system_paths()
    exp_source = paths.calibration_dir / recipe["exp_file"]
    exp_name = slug(f"lys_recipe_{recipe_name}")
    copy_verified(exp_source, output_root / "exp_data" / f"{exp_name}.exp")
    validation_dir = paths.generated_root / "Validation" / recipe_name
    validation_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for anchor_name in anchors:
        anchor_source = paths.lys_root / anchor_name
        anchor_hash = sha256_file(anchor_source)
        anchor_input = f"Lys_Calibration/{anchor_hash[:12]}_{slug(anchor_source.stem)}{anchor_source.suffix.lower()}"
        copy_verified(anchor_source, input_root / Path(anchor_input))
        view = slug(anchor_source.stem)
        prompt = build_replay_prompt(anchor_input, exp_name, f"Lys_Comfy/Calibration/Validation/{recipe_name}/{view}")
        write_json(validation_dir / f"prompt_{view}.json", prompt)
        prompt_id = client.queue(prompt)
        history = client.wait(prompt_id)
        output = output_image_from_history(history, "3")
        source_image = output_root / output.get("subfolder", "") / output["filename"]
        destination = validation_dir / f"{view}.png"
        copy_verified(source_image, destination)
        records.append({"view": view, "anchor": anchor_name, "image": destination.name, "pixel_hash": image_pixel_hash(destination), "prompt_id": prompt_id})
    write_json(validation_dir / "manifest.json", {"schema_version": 1, "recipe": recipe_name, "created_at": utc_now(), "views": records})
    return validation_dir


def mask_file_for_recipe(paths: ProjectPaths, mask_name: str) -> Path:
    mapping = {
        "subtle": "Lys_face_mask_subtle_soft.png",
        "midface": "Lys_face_mask_midface_soft.png",
        "openjaw": "Lys_face_mask_openjaw_soft.png",
    }
    if mask_name not in mapping:
        raise ValueError(f"Unknown adaptive mask {mask_name}")
    return paths.workflow_dir / mapping[mask_name]


def build_polish_prompt(
    *,
    anchor_input: str,
    raw_input: str,
    mask_input: str,
    denoise: float,
    save_prefix: str,
    checkpoint: str,
    seed: int,
) -> dict[str, Any]:
    positive = (
        "score_9, score_8_up, score_7_up, western comic illustration, precise controlled ink lines, "
        "crisp eyelashes, defined cool grey irises, clean eyebrow strokes, sharp dark-red lip contour, "
        "restrained comic shading, preserve exact Lys facial identity, proportions, makeup and expression"
    )
    negative = (
        "score_6, score_5, score_4, changed facial structure, changed eye shape, changed eyebrow arch, "
        "changed nose, changed lips, changed hairline, different makeup, photorealistic pores, airbrushed skin, "
        "painterly blur, plastic skin, oversharpening, halos, noisy ink, anime, manga, 3d render, text, watermark"
    )
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": anchor_input}},
        "2": {"class_type": "LoadImage", "inputs": {"image": raw_input}},
        "3": {"class_type": "LoadImageMask", "inputs": {"image": mask_input, "channel": "red"}},
        "4": {"class_type": "ImageCrop", "inputs": {"image": ["2", 0], "width": 832, "height": 832, "x": 211, "y": 211}},
        "5": {"class_type": "CropMask", "inputs": {"mask": ["3", 0], "x": 211, "y": 211, "width": 832, "height": 832}},
        "6": {"class_type": "ImageScale", "inputs": {"image": ["4", 0], "upscale_method": "lanczos", "width": 1248, "height": 1248, "crop": "disabled"}},
        "7": {"class_type": "MaskToImage", "inputs": {"mask": ["5", 0]}},
        "8": {"class_type": "ImageScale", "inputs": {"image": ["7", 0], "upscale_method": "lanczos", "width": 1248, "height": 1248, "crop": "disabled"}},
        "9": {"class_type": "ImageToMask", "inputs": {"image": ["8", 0], "channel": "red"}},
        "10": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "11": {"class_type": "CLIPSetLastLayer", "inputs": {"clip": ["10", 1], "stop_at_clip_layer": -2}},
        "12": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["11", 0], "text": positive}},
        "13": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["11", 0], "text": negative}},
        "14": {"class_type": "InpaintModelConditioning", "inputs": {"positive": ["12", 0], "negative": ["13", 0], "vae": ["10", 2], "pixels": ["6", 0], "mask": ["9", 0], "noise_mask": True}},
        "15": {"class_type": "KSampler", "inputs": {"model": ["10", 0], "positive": ["14", 0], "negative": ["14", 1], "latent_image": ["14", 2], "seed": seed, "steps": 28, "cfg": 5.0, "sampler_name": "dpmpp_2m_sde", "scheduler": "karras", "denoise": denoise}},
        "16": {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0], "vae": ["10", 2]}},
        "17": {"class_type": "ImageScale", "inputs": {"image": ["16", 0], "upscale_method": "lanczos", "width": 832, "height": 832, "crop": "disabled"}},
        "18": {"class_type": "ImageCompositeMasked", "inputs": {"destination": ["1", 0], "source": ["17", 0], "x": 211, "y": 211, "resize_source": False, "mask": ["5", 0]}},
        "19": {"class_type": "SaveImage", "inputs": {"images": ["18", 0], "filename_prefix": save_prefix}},
    }


def polish_recipe(recipe_name: str, *, api_url: str = API_DEFAULT, paths: ProjectPaths | None = None) -> Path:
    paths = paths or ProjectPaths.discover()
    library = load_recipe_library(paths.recipe_library)
    recipe = library["recipes"][recipe_name]
    validation_dir = paths.generated_root / "Validation" / recipe_name
    raw_source = validation_dir / "anchor_1.png"
    if not raw_source.is_file():
        raise FileNotFoundError("Run validate-views before polishing; front replay is missing")
    client = ComfyClient(api_url)
    input_root, output_root = client.system_paths()
    anchor_source = paths.lys_root / "anchor 1.png"
    mask_source = mask_file_for_recipe(paths, recipe["mask"])
    staged = input_root / "Lys_Calibration"
    anchor_input = f"Lys_Calibration/{sha256_file(anchor_source)[:12]}_anchor_1.png"
    raw_input = f"Lys_Calibration/{sha256_file(raw_source)[:12]}_{recipe_name}_raw.png"
    mask_input = f"Lys_Calibration/{sha256_file(mask_source)[:12]}_{recipe['mask']}_mask.png"
    copy_verified(anchor_source, input_root / Path(anchor_input))
    copy_verified(raw_source, input_root / Path(raw_input))
    copy_verified(mask_source, input_root / Path(mask_input))
    polish_dir = paths.generated_root / "Polish" / recipe_name
    polish_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for denoise in recipe["polish"]["denoise_values"]:
        tag = f"d{int(round(float(denoise) * 100)):03d}"
        prompt = build_polish_prompt(
            anchor_input=anchor_input,
            raw_input=raw_input,
            mask_input=mask_input,
            denoise=float(denoise),
            save_prefix=f"Lys_Comfy/Calibration/Polish/{recipe_name}/{tag}",
            checkpoint=recipe["polish"]["checkpoint"],
            seed=int(recipe["polish"]["seed"]),
        )
        write_json(polish_dir / f"prompt_{tag}.json", prompt)
        prompt_id = client.queue(prompt)
        history = client.wait(prompt_id, timeout=300.0)
        output = output_image_from_history(history, "19")
        source = output_root / output.get("subfolder", "") / output["filename"]
        destination = polish_dir / f"{tag}.png"
        copy_verified(source, destination)
        records.append({"candidate_id": tag, "label": f"image wash denoise {denoise}", "controls": {"denoise": denoise}, "effective_parameters": {"denoise": denoise, **recipe["polish"]}, "image": destination.name, "pixel_hash": image_pixel_hash(destination)})
    manifest = {
        "schema_version": 1,
        "batch_id": f"polish_{recipe_name}",
        "purpose": f"Finalist-only 1.5x Lanczos image wash for {recipe_name}",
        "target": recipe_name,
        "anchor": {"name": "anchor 1.png", "sha256": sha256_file(anchor_source)},
        "candidates": records,
        "review_order": [record["candidate_id"] for record in records],
    }
    copy_verified(anchor_source, polish_dir / "anchor.png")
    write_json(polish_dir / "manifest.json", manifest)
    write_reviewer(polish_dir, manifest)
    return polish_dir

