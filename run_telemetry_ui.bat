@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" telemetry-viewer\telemetry_ui.py
) else (
  python telemetry-viewer\telemetry_ui.py
)
