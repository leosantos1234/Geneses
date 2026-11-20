@echo off
setlocal EnableExtensions
set "ROBO_DIR=%~dp0"
set "UI_DIR=%ROBO_DIR%ui\lovable_export\genesis-quant-terminal-main"
if not exist "%UI_DIR%\package.json" (
    echo [ERRO] Frontend nao encontrado: %UI_DIR%
    pause
    exit /b 1
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERRO] npm nao foi encontrado no PATH.
    pause
    exit /b 1
)
cd /d "%UI_DIR%"
set "VITE_API_BASE=http://localhost:8010"
echo Iniciando painel em http://localhost:8080
echo Para parar: Ctrl+C
npm.cmd run dev -- --host 0.0.0.0 --port 8080
set "EXIT_CODE=%ERRORLEVEL%"
echo Painel encerrado com codigo %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
