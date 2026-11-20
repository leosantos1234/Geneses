@echo off
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\supervisor_runner.ps1 -Root "%CD%" -PythonExe "python" -ConfigPath "ops/services.json" -IntervalSec 60
