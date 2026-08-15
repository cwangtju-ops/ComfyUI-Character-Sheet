from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from wizard_core import rest_request


def parse_assignments(values: list[str]) -> dict[str, float]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Expected name=value, received {value}")
        name, raw = value.split("=", 1)
        result[name.strip()] = float(raw)
    return result


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def wait_for_job(job_id: str) -> dict[str, Any]:
    while True:
        job = rest_request("GET", f"/api/explore/jobs/{job_id}")
        print(f"{job['status']}: {len(job.get('candidates', []))}/12", file=sys.stderr)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(1)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Expression Wizard CLI")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("schema")
    sub.add_parser("list")
    get = sub.add_parser("get"); get.add_argument("job_id")
    cancel = sub.add_parser("cancel"); cancel.add_argument("job_id")
    retry = sub.add_parser("retry"); retry.add_argument("job_id"); retry.add_argument("--wait", action="store_true")
    analyze = sub.add_parser("analyze"); analyze.add_argument("job_id")
    workflow = sub.add_parser("workflow"); workflow.add_argument("job_id"); workflow.add_argument("candidate_id")
    validate = sub.add_parser("validate-workflow"); validate.add_argument("workflow", type=Path)
    create = sub.add_parser("create"); create.add_argument("request", type=Path); create.add_argument("--wait", action="store_true")
    sweep = sub.add_parser("sweep")
    sweep.add_argument("parameter"); sweep.add_argument("start", type=float); sweep.add_argument("end", type=float)
    sweep.add_argument("--source", default="anchor_1"); sweep.add_argument("--set", action="append", default=[]); sweep.add_argument("--wait", action="store_true")
    grid = sub.add_parser("grid")
    grid.add_argument("x_parameter"); grid.add_argument("x_start", type=float); grid.add_argument("x_end", type=float)
    grid.add_argument("y_parameter"); grid.add_argument("y_start", type=float); grid.add_argument("y_end", type=float)
    grid.add_argument("--source", default="anchor_1"); grid.add_argument("--set", action="append", default=[]); grid.add_argument("--wait", action="store_true")
    return root


def create_request(args: argparse.Namespace) -> dict[str, Any]:
    source_type = "upload" if args.source.startswith("upload_") else "lys"
    fixed = parse_assignments(args.set)
    if args.command == "sweep":
        return {"source": {"type": source_type, "id": args.source}, "mode": "sweep", "fixed": fixed, "sweep": {"parameter": args.parameter, "start": args.start, "end": args.end, "count": 12}}
    return {"source": {"type": source_type, "id": args.source}, "mode": "grid", "fixed": fixed, "grid": {"x": {"parameter": args.x_parameter, "start": args.x_start, "end": args.x_end, "count": 4}, "y": {"parameter": args.y_parameter, "start": args.y_start, "end": args.y_end, "count": 3}}}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "schema": result = rest_request("GET", "/api/explore/config")
    elif args.command == "list": result = rest_request("GET", "/api/explore/jobs")
    elif args.command == "get": result = rest_request("GET", f"/api/explore/jobs/{args.job_id}")
    elif args.command == "cancel": result = rest_request("DELETE", f"/api/explore/jobs/{args.job_id}")
    elif args.command == "retry":
        result = rest_request("POST", f"/api/explore/jobs/{args.job_id}/retry")
        if args.wait: result = wait_for_job(args.job_id)
    elif args.command == "analyze": result = rest_request("GET", f"/api/explore/jobs/{args.job_id}/analysis")
    elif args.command == "workflow": result = rest_request("GET", f"/api/explore/jobs/{args.job_id}/candidates/{args.candidate_id}/workflow")
    elif args.command == "validate-workflow": result = rest_request("POST", "/api/explore/validate-workflow", {"workflow": json.loads(args.workflow.read_text(encoding="utf-8"))})
    elif args.command == "create":
        result = rest_request("POST", "/api/explore/jobs", json.loads(args.request.read_text(encoding="utf-8")))
        if args.wait: result = wait_for_job(result["job_id"])
    elif args.command in {"sweep", "grid"}:
        result = rest_request("POST", "/api/explore/jobs", create_request(args))
        if args.wait: result = wait_for_job(result["job_id"])
    else: raise AssertionError(args.command)
    print_json(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
