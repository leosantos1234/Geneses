@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%CD%\.venv_local\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" -m uvicorn api_ui:app --host 0.0.0.0 --port 8010
