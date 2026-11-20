@echo off
setlocal

set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
set UI_DIR=%ROOT%\ui\lovable_export\genesis-quant-terminal-main

echo [UI Stack] Subindo API UI na porta 8010...
start "Genesis UI API" cmd /k "cd /d %ROOT% && start_ui_api.bat"

echo [UI Stack] Subindo frontend Lovable...
start "Genesis UI Frontend" cmd /k "cd /d %ROOT% && start_lovable_frontend.bat"

echo [UI Stack] Pronto. API + Frontend iniciados em janelas separadas.
endlocal
