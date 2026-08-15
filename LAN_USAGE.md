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

Stop the desktop backend with Ctrl+C and restart `Expression Wizard LAN.cmd`
to test the new code. Commit generated images only when deliberately adding
test fixtures; `ComfyUI_Generated/` is ignored by default.

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

