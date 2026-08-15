# ComfyUI Character Sheet — Expression Wizard

Expression Wizard is a localhost application for controlled,
human-in-the-loop calibration of AdvancedLivePortrait's `ExpressionEditor`.
It builds deterministic 12-candidate experiments through a running ComfyUI
instance and exposes the same operations through a browser UI, REST API, CLI,
and a dependency-free stdio MCP bridge for Codex.

Expression Wizard performs raw facial-expression geometry only. It does not
load an SDXL/Pony checkpoint, run diffusion, add prompts, apply masks, upscale,
or image-wash the source.

## Features

- Live parameter names, limits, steps, and defaults from ComfyUI's
  `ExpressionEditor` schema.
- Exactly 12 candidates in Sweep, 4×3 Grid, and Manual modes.
- Progressive Grid and Focus views with complete effective parameters.
- PNG/JPEG/WebP uploads plus optional named character anchors.
- Source-resolution preservation and per-candidate dimension validation.
- Persistent jobs, cancellation after the active prompt, restart recovery,
  and retry without regenerating completed candidates.
- Exact Comfy workflow JSON, binary `.exp`, tensor JSON/CSV, hashes, manifests,
  and numeric side-effect analysis.
- REST, CLI, and nine structured MCP tools.
- Backward-compatible calibration reviewer routes.

## Requirements

- Windows, Linux, or macOS with Python 3.10+.
- Pillow (`pip install -r requirements.txt`).
- A running ComfyUI instance, normally at `http://127.0.0.1:8188`.
- ComfyUI-AdvancedLivePortrait installed and exposing these nodes:
  `ExpressionEditor`, `SaveExpData`, `LoadImage`, and `SaveImage`.

Using the Python environment bundled with ComfyUI is recommended because it
already contains Pillow and the image libraries needed by the workflow.

## Optional character anchors

Place any of these files directly in the repository root to make them appear
as named source cards:

- `anchor 1.png`
- `anchor 2.png`
- `anchor 3.png`

Anchors are optional. You can upload any PNG, JPEG, or WebP from the Explore
page instead. Generated data is written to
`ComfyUI_Generated/Expression_Wizard/` and is ignored by Git.

## Start

### Windows launcher

If `python` on `PATH` is not your ComfyUI Python, set
`EXPRESSION_WIZARD_PYTHON` first:

```powershell
$env:EXPRESSION_WIZARD_PYTHON = 'C:\path\to\ComfyUI\.venv\Scripts\python.exe'
& '.\Expression Wizard.cmd'
```

The launcher opens `http://127.0.0.1:8765/`, reuses an existing Expression
Wizard server, and reports a foreign port conflict without stopping it.

### Direct

```powershell
python .\ComfyUI_Workflows\expression_wizard\server.py `
  --host 127.0.0.1 --port 8765 `
  --api http://127.0.0.1:8188 --open-browser
```

### Laptop UI development

Keep the production backend and ComfyUI running on the desktop in LAN mode.
On the laptop, set the same remote URL and token used by the CLI/MCP bridge,
then start the development launcher:

```powershell
$env:EXPRESSION_WIZARD_URL = 'http://192.168.2.200:8765'
$env:EXPRESSION_WIZARD_TOKEN = 'paste-the-desktop-token'
& '.\Expression Wizard Dev.cmd'
```

The launcher opens `http://127.0.0.1:8766/`. HTML, JavaScript, and CSS are
served directly from the laptop checkout with caching disabled, while `/api/`
requests and generated images are authenticated and proxied to the desktop.
The development proxy binds to loopback only and never sends the desktop token
to browser JavaScript.

### Laptop-owned backend with desktop ComfyUI

Expression Wizard can keep its anchors, jobs, generated previews, and `.exp`
copies on the laptop while using only the desktop GPU and ComfyUI. The desktop
runs `Expression Wizard Comfy Gateway.cmd` on port 8189; the laptop runs
`Expression Wizard Laptop.cmd`. ComfyUI itself remains on `127.0.0.1:8188`.

See [LAN_USAGE.md](LAN_USAGE.md) for the firewall, token, and environment setup.

## CLI

The server must be running before using the CLI.

```powershell
$cli = '.\ComfyUI_Workflows\expression_wizard\cli.py'
python $cli schema
python $cli sweep blink -20 5 --source anchor_1 --wait
python $cli grid smile -0.1 0.3 blink -4 2 --source anchor_1
python $cli list
python $cli analyze JOB_ID
python $cli workflow JOB_ID candidate_01
```

Uploaded sources use the `upload_...` identifier returned by the upload API.

## Codex MCP bridge

Start Expression Wizard, then add this stdio server to your Codex MCP
configuration. Replace the two paths with absolute paths on your machine:

```toml
[mcp_servers.expressionWizard]
command = 'C:\path\to\ComfyUI\.venv\Scripts\python.exe'
args = [ 'C:\path\to\ComfyUI-Character-Sheet\ComfyUI_Workflows\expression_wizard\mcp_server.py' ]
startup_timeout_sec = 30
tool_timeout_sec = 600
```

Open a new Codex task after editing the configuration. The MCP bridge exposes:

- `get_expression_schema`
- `create_experiment`
- `list_experiments`
- `get_experiment`
- `cancel_experiment`
- `retry_experiment`
- `analyze_experiment`
- `get_candidate_workflow`
- `validate_workflow`

## REST API

- `GET /api/explore/config`
- `POST /api/explore/assets`
- `POST /api/explore/jobs`
- `GET /api/explore/jobs`
- `GET /api/explore/jobs/{id}`
- `POST /api/explore/jobs/{id}/retry`
- `DELETE /api/explore/jobs/{id}`
- `GET /api/explore/jobs/{id}/analysis`
- `GET /api/explore/jobs/{id}/candidates/{candidate}/workflow`
- `POST /api/explore/validate-workflow`

## Tests

```powershell
python -m unittest discover `
  -s .\ComfyUI_Workflows\expression_wizard\tests -v
```

The test suite covers sweep spacing and reversal, grid construction, manual
validation, forced sample-free settings, upload decoding and dimensions, path
safety, numeric analysis, and MCP initialization/tool results.


## Secure laptop access

Use the authenticated LAN mode described in [LAN_USAGE.md](LAN_USAGE.md). ComfyUI remains private on the desktop; the laptop connects only to Expression Wizard.
