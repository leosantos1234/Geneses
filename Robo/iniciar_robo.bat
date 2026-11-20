@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "ENV_FILE=%~1"
if not defined ENV_FILE set "ENV_FILE=.env-btc"
set "GENESIS_STRATEGY_SELECTION=%~2"
if /I "%~2"=="PULLBACK_TREND_FOLLOWING" title Genesis SOL - PULLBACK_TREND_FOLLOWING [REAL]
if /I "%~2"=="SWING_AND_PULLBACK" title Genesis AVAX - SWING_AND_PULLBACK [REAL]
if /I "%~2"=="MARKET_MAKING_LATERAL" title Genesis BTC - MARKET_MAKING_LATERAL [REAL]
if /I "%PAPER_MODE%"=="1" (
    rem Gate seguro: nenhuma ordem real pode ser enviada neste modo.
    set "ENABLE_LIVE_TRADING=false"
    set "ALLOW_EXCHANGE_WRITE=false"
    set "OKX_READ_ONLY=true"
    set "ALLOW_MAINNET_TRADING=false"
    set "ALLOW_MAINNET_FROM_ENVFILE=false"
)
if /I "%~2"=="PULLBACK_TREND_FOLLOWING" set "PULLBACK_ENABLED=1"
if /I "%~2"=="PULLBACK_TREND_FOLLOWING" set "PULLBACK_HISTORICAL_BACKTEST_ENABLED=1"
if /I "%~2"=="SWING_AND_PULLBACK" set "GENESIS_STRATEGY_SELECTION=SWING_AND_PULLBACK"
if /I "%~2"=="SWING_AND_PULLBACK" set "SWING_ENABLED=1"
if /I "%~2"=="SWING_AND_PULLBACK" set "PULLBACK_ENABLED=1"
if /I "%~2"=="SWING_AND_PULLBACK" set "SWING_HISTORICAL_BACKTEST_ENABLED=1"
if /I "%~2"=="SWING_AND_PULLBACK" set "PULLBACK_HISTORICAL_BACKTEST_ENABLED=1"
if /I "%~2"=="SWING_AND_PULLBACK" set "SWING_LIVE_EXECUTOR_ENABLED=1"
if /I "%~2"=="SWING_AND_PULLBACK" set "PULLBACK_LIVE_EXECUTOR_ENABLED=1"
set "PYTHON_EXE=%CD%\.venv_local\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [ERRO] Ambiente Python nao encontrado: %PYTHON_EXE%
    pause
    exit /b 1
)
if not exist "%CD%\%ENV_FILE%" (
    echo [ERRO] Arquivo de configuracao nao encontrado: %CD%\%ENV_FILE%
    pause
    exit /b 1
)

rem Nao abrir uma segunda instancia do mesmo ativo. O worker principal
rem mantem este lock enquanto estiver ativo; isso evita loops de restart
rem e consoles extras quando o launcher for executado novamente.
set "WORKER_LOCK_FILE="
if /I "%ENV_FILE%"==".env-sol" set "WORKER_LOCK_FILE=%CD%\pids\genesis_worker_SOLUSDT.lock"
if /I "%ENV_FILE%"==".env-btc" set "WORKER_LOCK_FILE=%CD%\pids\genesis_worker_BTCUSDT.lock"
if /I "%ENV_FILE%"=="proximos_Ativos\.env-avax" set "WORKER_LOCK_FILE=%CD%\pids\genesis_worker_AVAXUSDT.lock"
if defined WORKER_LOCK_FILE if exist "%WORKER_LOCK_FILE%" (
    echo [INFO] Worker ja ativo para %ENV_FILE%; nenhuma instancia nova sera iniciada.
    exit /b 0
)

rem Acesso direto a exchange e servicos locais; nao herdar proxy invalido.
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="
set "http_proxy="
set "https_proxy="
set "all_proxy="

rem Inicia o LSTM local configurado para o ativo (se houver). O script
rem permanece responsivo mesmo quando o LSTM estiver desabilitado ou falhar.
if exist "%CD%\estrategias\lstm_service.py" (
    "%PYTHON_EXE%" -c "from ui_backend.lstm_manager import ensure_lstm_service; print(ensure_lstm_service(r'%ENV_FILE%'))"
)
set "PYTHONPATH=%CD%;%CD%\..\Agente_IA"
rem O trading_bot_geneses.py carrega GENESIS_DATABASE_URL do .env do ativo.
rem Evita que uma URL antiga herdada do shell sobrescreva o arquivo selecionado.
set "GENESIS_DATABASE_URL="
echo Iniciando worker do Robo Fluxo com %ENV_FILE%
echo Para parar: Ctrl+C
set "RESTART_DELAY=15"

:RUN_WORKER
echo [%DATE% %TIME%] Worker ativo: %ENV_FILE%
"%PYTHON_EXE%" trading_bot_geneses.py "%ENV_FILE%"
set "EXIT_CODE=%ERRORLEVEL%"
echo Worker encerrado com codigo %EXIT_CODE%.

rem Ctrl+C encerra de forma intencional; nao reiniciar nesse caso.
if "%EXIT_CODE%"=="0" goto STOP_WORKER
if "%EXIT_CODE%"=="-1073741510" goto STOP_WORKER

echo [ALERTA] Worker %ENV_FILE% caiu inesperadamente.
echo [ALERTA] Codigo=%EXIT_CODE%. Tentativa de reinicio em %RESTART_DELAY%s.
timeout /t %RESTART_DELAY% /nobreak >nul
goto RUN_WORKER

:STOP_WORKER
echo Worker %ENV_FILE% parado.
pause
exit /b %EXIT_CODE%
