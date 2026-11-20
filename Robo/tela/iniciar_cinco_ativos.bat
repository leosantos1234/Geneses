@echo off
setlocal EnableExtensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."

set "ROBO_DIR=%CD%"
set "PYTHON_EXE=%ROBO_DIR%\.venv_local\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [ERRO] Ambiente Python nao encontrado: %PYTHON_EXE%
    pause
    exit /b 1
)

for %%F in (.env-btc .env-bnb .env-sol) do (
    if not exist "%ROBO_DIR%\%%F" (
        echo [ERRO] Perfil nao encontrado: %ROBO_DIR%\%%F
        pause
        exit /b 1
    )
)

echo Iniciando API do painel...
start "Genesis API do Painel" cmd /k call "%ROBO_DIR%\iniciar_api_painel.bat"
set "UI_DIR=%ROBO_DIR%\ui\lovable_export\genesis-quant-terminal-main"
if not exist "%UI_DIR%\package.json" (
    echo [ERRO] Frontend do painel nao encontrado: %UI_DIR%
    pause
    exit /b 1
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Node.js/npm nao encontrado no PATH.
    pause
    exit /b 1
)
echo Iniciando painel web em http://localhost:8080/dashboard ...
start "Genesis Painel Web" cmd /k "cd /d "%UI_DIR%" && npm.cmd run dev"
timeout /t 3 /nobreak >nul
start "" "http://localhost:8080/auth"

echo Iniciando servicos LSTM...
set "LSTM_SCRIPT=%ROBO_DIR%\estrategias\lstm_service.py"
if not exist "%LSTM_SCRIPT%" (
    echo [ERRO] Servico LSTM nao encontrado: %LSTM_SCRIPT%
    pause
    exit /b 1
)
start "Genesis LSTM BTC" cmd /k ""%PYTHON_EXE%" "%LSTM_SCRIPT%" "%ROBO_DIR%\.env-btc""
start "Genesis LSTM SOL" cmd /k ""%PYTHON_EXE%" "%LSTM_SCRIPT%" "%ROBO_DIR%\.env-sol""
start "Genesis LSTM BNB" cmd /k ""%PYTHON_EXE%" "%LSTM_SCRIPT%" "%ROBO_DIR%\.env-bnb""
echo Servicos LSTM enviados para inicializacao.

echo Iniciando APIs da Agente IA por ativo...
start "Genesis IA BTC" cmd /k call "%ROBO_DIR%\supervisionar_ia.bat" .env-btc 8000
start "Genesis IA BNB" cmd /k call "%ROBO_DIR%\supervisionar_ia.bat" .env-bnb 8003
start "Genesis IA SOL" cmd /k call "%ROBO_DIR%\supervisionar_ia.bat" .env-sol 8002
timeout /t 5 /nobreak >nul

for %%F in (.env-btc .env-bnb .env-sol) do (
    echo Iniciando worker %%F...
    start "Genesis Robo %%F" cmd /k call "%ROBO_DIR%\iniciar_robo.bat" %%F
)

echo.
echo Tres workers iniciados: BTC, BNB e SOL.
echo Cada worker usa o perfil e o risco do proprio ativo.
echo O modo de escrita permanece controlado por OKX_READ_ONLY.
pause
exit /b 0
