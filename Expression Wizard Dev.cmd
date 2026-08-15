@echo off
setlocal
title Expression Wizard UI Development

set "EW_ROOT=%~dp0"
set "EW_PROXY=%EW_ROOT%ComfyUI_Workflows\expression_wizard\dev_proxy.py"
set "EW_URL=http://127.0.0.1:8766/"

if defined EXPRESSION_WIZARD_DEV_PYTHON (
  set "EW_PYTHON=%EXPRESSION_WIZARD_DEV_PYTHON%"
) else if defined EXPRESSION_WIZARD_PYTHON (
  set "EW_PYTHON=%EXPRESSION_WIZARD_PYTHON%"
) else if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  set "EW_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
) else (
  set "EW_PYTHON=python"
)

if not exist "%EW_PROXY%" (
  echo Expression Wizard could not find the UI development proxy:
  echo %EW_PROXY%
  pause
  exit /b 1
)

if not defined EXPRESSION_WIZARD_URL (
  echo EXPRESSION_WIZARD_URL is not set.
  echo Set it to the desktop backend, for example http://192.168.2.200:8765
  pause
  exit /b 1
)

if not defined EXPRESSION_WIZARD_TOKEN (
  echo EXPRESSION_WIZARD_TOKEN is not set.
  echo Save the persistent token from the desktop Expression Wizard LAN server.
  pause
  exit /b 1
)

"%EW_PYTHON%" --version >nul 2>&1
if errorlevel 1 (
  echo Expression Wizard could not start Python.
  echo Set EXPRESSION_WIZARD_DEV_PYTHON to a Python 3.10 or newer executable.
  pause
  exit /b 1
)

echo Starting the laptop UI development proxy at %EW_URL%
echo UI files come from this checkout; API and generated files come from the desktop.
"%EW_PYTHON%" "%EW_PROXY%" --host 127.0.0.1 --port 8766 --open-browser
if errorlevel 1 (
  echo.
  echo Expression Wizard UI development proxy stopped with an error.
  pause
)
exit /b %errorlevel%
