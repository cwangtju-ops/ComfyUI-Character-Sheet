from __future__ import annotations

import json
import sys
from typing import Any

from wizard_core import rest_request


TOOLS = [
    {"name": "get_expression_schema", "description": "Get the live AdvancedLivePortrait ExpressionEditor controls, ranges, defaults, ComfyUI status, and available sources.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "create_experiment", "description": "Create a deterministic 12-candidate sweep, 4x3 grid, or manual Expression Wizard experiment.", "inputSchema": {"type": "object", "required": ["request"], "properties": {"request": {"type": "object", "description": "Expression Wizard job request using source, mode, fixed, and sweep/grid/manual."}}, "additionalProperties": False}},
    {"name": "list_experiments", "description": "List persistent Expression Wizard experiments, newest first.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_experiment", "description": "Get job state, progress, source dimensions, and completed candidate metadata.", "inputSchema": {"type": "object", "required": ["job_id"], "properties": {"job_id": {"type": "string"}}, "additionalProperties": False}},
    {"name": "cancel_experiment", "description": "Request cancellation after the currently running ComfyUI candidate.", "inputSchema": {"type": "object", "required": ["job_id"], "properties": {"job_id": {"type": "string"}}, "additionalProperties": False}},
    {"name": "retry_experiment", "description": "Resume only the missing candidates of a failed, interrupted, or cancelled experiment.", "inputSchema": {"type": "object", "required": ["job_id"], "properties": {"job_id": {"type": "string"}}, "additionalProperties": False}},
    {"name": "analyze_experiment", "description": "Return tensor strength, affected landmark codes, rotation/scale/translation side effects, sweep linearity, and fixed-control violations.", "inputSchema": {"type": "object", "required": ["job_id"], "properties": {"job_id": {"type": "string"}}, "additionalProperties": False}},
    {"name": "get_candidate_workflow", "description": "Get the exact ComfyUI API workflow JSON used for one generated candidate.", "inputSchema": {"type": "object", "required": ["job_id", "candidate_id"], "properties": {"job_id": {"type": "string"}, "candidate_id": {"type": "string"}}, "additionalProperties": False}},
    {"name": "validate_workflow", "description": "Validate a proposed ComfyUI API workflow against the running ComfyUI object_info schema without executing it.", "inputSchema": {"type": "object", "required": ["workflow"], "properties": {"workflow": {"type": "object"}}, "additionalProperties": False}},
]


def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    if name == "get_expression_schema": return rest_request("GET", "/api/explore/config")
    if name == "create_experiment": return rest_request("POST", "/api/explore/jobs", arguments["request"])
    if name == "list_experiments": return rest_request("GET", "/api/explore/jobs")
    if name == "get_experiment": return rest_request("GET", f"/api/explore/jobs/{arguments['job_id']}")
    if name == "cancel_experiment": return rest_request("DELETE", f"/api/explore/jobs/{arguments['job_id']}")
    if name == "retry_experiment": return rest_request("POST", f"/api/explore/jobs/{arguments['job_id']}/retry")
    if name == "analyze_experiment": return rest_request("GET", f"/api/explore/jobs/{arguments['job_id']}/analysis")
    if name == "get_candidate_workflow": return rest_request("GET", f"/api/explore/jobs/{arguments['job_id']}/candidates/{arguments['candidate_id']}/workflow")
    if name == "validate_workflow": return rest_request("POST", "/api/explore/validate-workflow", {"workflow": arguments["workflow"]})
    raise ValueError(f"Unknown Expression Wizard tool: {name}")


def response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    value = {"jsonrpc": "2.0", "id": request_id}
    if error is not None: value["error"] = error
    else: value["result"] = result
    return value


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return response(request_id, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "expression-wizard", "version": "1.0.0"}})
    if method in {"notifications/initialized", "notifications/cancelled"}: return None
    if method == "ping": return response(request_id, {})
    if method == "tools/list": return response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        name, arguments = params.get("name"), params.get("arguments") or {}
        try:
            result = call_tool(name, arguments)
            return response(request_id, {"content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}], "structuredContent": result, "isError": False})
        except Exception as exc:
            return response(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
    if request_id is None: return None
    return response(request_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw: continue
        try:
            message = json.loads(raw)
            output = handle(message)
        except Exception as exc:
            output = response(None, error={"code": -32700, "message": str(exc)})
        if output is not None:
            sys.stdout.write(json.dumps(output, separators=(",", ":"), ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
