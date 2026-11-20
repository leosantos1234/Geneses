@echo off
setlocal EnableExtensions

set "ENV_FILE=%~1"
set "PORT=%~2"
set "ROBO_DIR=%~dp0"
set "AGENT_DIR=%ROBO_DIR%..\Agente_IA"
set "PYTHON_EXE=%ROBO_DIR%.venv_local\Scripts\python.exe"
if not defined ENV_FILE set "ENV_FILE=.env-btc"
if not defined PORT set "PORT=8000"

if not exist "%PYTHON_EXE%" (
    echo [ERRO] Python nao encontrado: %PYTHON_EXE%
    pause
    exit /b 1
)
if not exist "%AGENT_DIR%\api_agente_ia.py" (
    echo [ERRO] API da Agente IA nao encontrada: %AGENT_DIR%\api_agente_ia.py
    pause
    exit /b 1
)

rem Evita duas instancias da mesma API disputando a mesma porta.
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo [INFO] Porta %PORT% ja esta em uso pelo PID %%P. API nao sera duplicada.
    exit /b 0
)

set "RESTART_DELAY=15"
cd /d "%AGENT_DIR%"

:RUN_API
echo [%DATE% %TIME%] Iniciando IA %ENV_FILE% na porta %PORT%...
set "ENV_FILE=%ENV_FILE%"
"%PYTHON_EXE%" -m uvicorn api_agente_ia:app --host 127.0.0.1 --port %PORT% --log-level info
set "EXIT_CODE=%ERRORLEVEL%"
echo IA %ENV_FILE% encerrada com codigo %EXIT_CODE%.

rem Ctrl+C encerra de forma intencional; nao reiniciar nesse caso.
if "%EXIT_CODE%"=="0" goto STOP_API
if "%EXIT_CODE%"=="-1073741510" goto STOP_API

echo [ALERTA] IA %ENV_FILE% caiu inesperadamente.
echo [ALERTA] Codigo=%EXIT_CODE%. Tentativa de reinicio em %RESTART_DELAY%s.
timeout /t %RESTART_DELAY% /nobreak >nul
goto RUN_API

:STOP_API
echo IA %ENV_FILE% parada.
pause
exit /b %EXIT_CODE%
