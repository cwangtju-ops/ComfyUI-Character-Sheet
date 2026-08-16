# Secure LAN use and laptop development

The recommended arrangement keeps both ComfyUI Powerhouse and the Expression
Wizard backend on the desktop. The laptop uses a browser, CLI, or Codex MCP
client over Wi-Fi.

```text
Laptop browser / CLI / Codex
             |
             | authenticated TCP 8765 on private Wi-Fi
             v
Expression Wizard backend on desktop
             |
             | localhost TCP 8188 + local ComfyUI files
             v
ComfyUI Powerhouse backend on desktop
```

Do not expose ComfyUI port 8188. Expression Wizard needs local access to
ComfyUI's input, output, and `.exp` files, so its backend belongs on the
desktop.

## One-time desktop setup

1. On the laptop, run `ipconfig` and note its Wi-Fi IPv4 address, for example
   `192.168.1.42`.
2. On the desktop, make sure the Wi-Fi network profile is **Private**.
3. Open PowerShell on the desktop with **Run as administrator**.
4. From this repository, allow only that laptop through Windows Firewall:

```powershell
.\scripts\Setup-LanAccess.ps1 -LaptopAddress 192.168.1.42
```

Run the script again whenever the laptop's IPv4 address changes. It updates the
existing rule instead of creating duplicates.

## Start on the desktop

Keep ComfyUI listening only on `127.0.0.1:8188`. If needed, configure the
Python used by the launcher:

```powershell
$env:EXPRESSION_WIZARD_PYTHON = 'C:\path\to\ComfyUI\.venv\Scripts\python.exe'
$env:EXPRESSION_WIZARD_DATA_ROOT = 'C:\Codex Projects\ComfyUI\Character Sheet_Lys'
& '.\Expression Wizard LAN.cmd'
```

The console prints:

- A laptop URL such as `http://192.168.1.10:8765/`.
- A random persistent access token.
- The token-file location.

Keep that console open. Press Ctrl+C to stop the backend.

## Open from the laptop

1. Open the printed laptop URL in a browser.
2. Paste the access token shown on the desktop.
3. The browser receives a 12-hour HttpOnly, SameSite session cookie. The login
   page does not save the token in browser storage.

The token is reused between restarts. To rotate it, stop Expression Wizard,
delete the displayed token file on the desktop, and start LAN mode again.

## Laptop CLI or MCP access

The CLI and MCP bridge support a remote URL and bearer token:

```powershell
$env:EXPRESSION_WIZARD_URL = 'http://192.168.1.10:8765'
$env:EXPRESSION_WIZARD_TOKEN = 'paste-the-desktop-token'
python .\ComfyUI_Workflows\expression_wizard\cli.py schema
```

For Codex on the laptop:

```toml
[mcp_servers.expressionWizard]
command = 'C:\path\to\python.exe'
args = [ 'C:\path\to\ComfyUI-Character-Sheet\ComfyUI_Workflows\expression_wizard\mcp_server.py' ]
startup_timeout_sec = 30
tool_timeout_sec = 600

[mcp_servers.expressionWizard.env]
EXPRESSION_WIZARD_URL = 'http://192.168.1.10:8765'
EXPRESSION_WIZARD_TOKEN = 'paste-the-desktop-token'
```

Open a new Codex task after changing MCP configuration.

## Continue development from the laptop

You do not need the laptop's ComfyUI app to use the desktop backend or for most Expression Wizard development. Keep the laptop app only if you also want standalone local generation when the desktop is unavailable.

```powershell
git clone https://github.com/cwangtju-ops/ComfyUI-Character-Sheet.git
cd ComfyUI-Character-Sheet
git switch -c your-feature-branch
```

Edit and run unit tests on the laptop, then push the branch. On the desktop:

```powershell
git fetch origin
git switch your-feature-branch
git pull --ff-only
```

Because the launcher can run directly from the Git checkout while `EXPRESSION_WIZARD_DATA_ROOT` points at the Lys workspace, no source-file copying is required. Stop the desktop backend with Ctrl+C and restart `Expression Wizard LAN.cmd`
to test the new code. Commit generated images only when deliberately adding
test fixtures; `ComfyUI_Generated/` is ignored by default.

### Develop the UI without redeploying the backend

For HTML, CSS, and JavaScript work, leave the stable backend running on the
desktop and launch the UI development proxy from the laptop checkout:

```powershell
& '.\Expression Wizard Dev.cmd'
```

Open `http://127.0.0.1:8766/`. Static files are read from the laptop on every
request, so a browser refresh shows local UI changes immediately. API calls,
uploads, job operations, and generated images are forwarded to the desktop
backend with the bearer token injected by the local proxy. The token is not
available to browser JavaScript.

This mode deliberately listens only on the laptop loopback interface. It is a
development convenience, not another LAN service. Python backend changes still
require deployment to the desktop until the remote Comfy transport phase is
implemented.

## Distributed mode: backend and data on the laptop

This mode keeps only ComfyUI and the GPU workload on the desktop:

```text
Laptop browser / Codex -> laptop Expression Wizard (127.0.0.1:8765)
                                  |
                                  | authenticated TCP 8189
                                  v
                         desktop Comfy gateway
                                  |
                                  | localhost TCP 8188 + Comfy files
                                  v
                         desktop ComfyUI / GPU
```

The laptop owns anchors, uploads, job manifests, prompts, preview copies, and
`.exp` copies. Because `.exp` files contain Torch tensors, the desktop gateway
decodes them inside the active ComfyUI Python environment and returns ordinary
JSON for laptop-side analysis. The desktop otherwise retains only ComfyUI's
normal input/output artifacts.

### Desktop

After updating the desktop checkout, open an Administrator PowerShell once:

```powershell
.\scripts\Setup-LanAccess.ps1 `
  -LaptopAddress 192.168.2.242 `
  -Port 8189 `
  -RuleName 'Expression Wizard Comfy Gateway'
```

Then use an ordinary PowerShell in the repository:

```powershell
$env:EXPRESSION_WIZARD_PYTHON = 'C:\Comfy Powerhouse\Comfy Powerhouse\ComfyUI\.venv\Scripts\python.exe'
$env:EXPRESSION_WIZARD_ALLOWED_CLIENTS = '192.168.2.242'
$env:EXPRESSION_WIZARD_COMFY_ROOT = 'C:\Comfy Powerhouse\Comfy Powerhouse\ComfyUI'
& '.\Expression Wizard Comfy Gateway.cmd'
```

The gateway prints a persistent token. Leave this window running. It binds port
8189 but accepts non-loopback requests only from the listed laptop IP, and every
operation also requires the token.

The gateway prints two different persistent tokens:

- `Access token` permits only the constrained generation transport.
- `Read-only admin token` permits model/node inventory, hashes, safetensors
  validation, and workflow dependency diagnosis. It cannot modify the desktop.

### Laptop

In a PowerShell in the laptop checkout, point EW at the local anchor/data folder
and desktop gateway:

```powershell
$env:EXPRESSION_WIZARD_DATA_ROOT = 'C:\Codex Projects\ComfyUI\Character Sheet_Lys'
$env:EXPRESSION_WIZARD_COMFY_URL = 'http://192.168.2.200:8189'
$env:EXPRESSION_WIZARD_COMFY_TOKEN = 'paste-the-token-printed-on-the-desktop'
$env:EXPRESSION_WIZARD_COMFY_ADMIN_TOKEN = 'paste-the-read-only-admin-token'
$env:EXPRESSION_WIZARD_LOCAL_PORT = '8775'
& '.\Expression Wizard Laptop.cmd'
```

The browser opens the configured local port, `http://127.0.0.1:8775/` in this
example. In this mode neither the backend nor
the data directory is exposed to the Wi-Fi network. Stop the earlier desktop EW
LAN backend and the laptop UI development proxy once this distributed mode is
confirmed.

Read-only diagnostics are then available through the CLI:

```powershell
python .\ComfyUI_Workflows\expression_wizard\cli.py comfy-summary
python .\ComfyUI_Workflows\expression_wizard\cli.py comfy-models --query liveportrait
python .\ComfyUI_Workflows\expression_wizard\cli.py comfy-model 'liveportrait/example.safetensors'
python .\ComfyUI_Workflows\expression_wizard\cli.py comfy-nodes --query portrait
```

To isolate the distributed ComfyUI connection from AdvancedLivePortrait, run a
single fixed text-to-image smoke test from the laptop while both PowerShell
services remain open:

```powershell
$body = @{
  checkpoint = 'cyberrealisticPony_semiRealV6.safetensors'
  positive = 'semi-realistic cinematic portrait of an adult woman, head and shoulders, natural skin texture, detailed expressive eyes, subtle friendly smile, soft studio lighting, neutral background'
  seed = 20260816
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8775/api/manage/smoke-test' `
  -Method POST `
  -ContentType 'application/json' `
  -Body $body
```

The desktop gateway accepts only the fixed core workflow and requires a
researched Model Profile. Profile defaults supply the native resolution,
sampler, scheduler, steps, CFG, Clip Skip, VAE behavior, and prompt templates;
explicit deviations are retained in the result's `overrides`. Optional upscale
and refinement remain disabled during experiments. The returned `image.path` points to the
PNG copied under the laptop's `ComfyUI_Generated\Expression_Wizard\_smoke_tests`
folder.

To remove only the gateway firewall rule later:

```powershell
.\scripts\Remove-LanAccess.ps1 -RuleName 'Expression Wizard Comfy Gateway'
```

### Silent distributed operation

After the environment above has been verified, the persistent consoles can be
replaced by the silent launchers:

1. On the desktop, double-click `Expression Wizard Gateway Silent.vbs`.
2. On the laptop, double-click `Expression Wizard Laptop Silent.vbs`.
3. Closing the EW tab or Chrome does not stop either service. Double-clicking
   the laptop launcher again reconnects to the existing backend.
4. Use EW's **Stop backend** button or `Expression Wizard Laptop Stop.vbs` for
   the laptop service. Use `Expression Wizard Gateway Stop.vbs` on the desktop.

The standalone Stop launchers refuse to interrupt active work. EW's in-page
stop action asks before cancelling an active experiment and waits until its
current image returns. Runtime logs and PID metadata are stored outside the
repository under `%USERPROFILE%\.expression_wizard\runtime`.

## Security boundaries

- The firewall rule is Private-profile only and restricted to one laptop IPv4.
- LAN mode always requires a token, including REST, CLI, MCP, files, and legacy
  reviewer routes.
- Mutating browser requests reject mismatched `Origin` headers.
- ComfyUI remains localhost-only.
- Do not configure router port forwarding for port 8765.
- Plain HTTP is intended only for a trusted WPA2/WPA3 private LAN. Use an
  encrypted overlay such as Tailscale when crossing an untrusted network.

Remove the firewall exception when it is no longer needed:

```powershell
.\scripts\Remove-LanAccess.ps1
```

