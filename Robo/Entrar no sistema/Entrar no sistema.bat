@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PAPER_MODE=0"
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="
set "http_proxy="
set "https_proxy="
set "all_proxy="

if not exist ".venv_local\Scripts\python.exe" (
    echo [ERRO] Ambiente Python nao encontrado: %CD%\.venv_local\Scripts\python.exe
    pause
    exit /b 1
)

for %%F in (.env-sol proximos_Ativos\.env-avax .env-btc) do (
    if not exist "%%F" (
        echo [ERRO] Perfil nao encontrado: %CD%\%%F
        pause
        exit /b 1
    )
)

echo ================================================================
echo ATENCAO: iniciando o sistema em OPERACAO REAL na OKX.
echo SOL/USDT = PULLBACK_TREND_FOLLOWING - candle de 4h
echo AVAX     = PULLBACK_TREND_FOLLOWING - candle de 4h
echo BTC      = MARKET_MAKING_LATERAL - candle de 5m
echo ================================================================

echo Inicializando telas do sistema...
start "Genesis UI API" cmd /k call "%CD%\start_ui_api.bat"
start "Genesis UI Frontend" cmd /k call "%CD%\start_lovable_frontend.bat"
timeout /t 5 >nul
start "" "http://localhost:8080"

echo Inicializando servicos LSTM locais...
"%CD%\.venv_local\Scripts\python.exe" -c "from ui_backend.lstm_manager import ensure_lstm_service; print(ensure_lstm_service(r'.env-sol')); print(ensure_lstm_service(r'.env-btc'))"
if errorlevel 1 echo [AVISO] Bootstrap LSTM retornou erro; o worker seguira com fallback.

if exist "pids\genesis_worker_SOLUSDT.lock" (
    echo [OK] SOLUSDT ja esta em execucao.
) else (
    start "Genesis SOL - PULLBACK_TREND_FOLLOWING [REAL]" cmd /k call "%CD%\iniciar_robo.bat" .env-sol PULLBACK_TREND_FOLLOWING
    echo [INICIADO] SOL/USDT - Pullback Trend Following.
)

if exist "pids\genesis_worker_AVAXUSDT.lock" (
    echo [OK] AVAXUSDT ja esta em execucao.
) else (
    start "Genesis AVAX - PULLBACK_TREND_FOLLOWING [REAL]" cmd /k call "%CD%\iniciar_robo.bat" proximos_Ativos\.env-avax PULLBACK_TREND_FOLLOWING
    echo [INICIADO] AVAX - Pullback Trend Following.
)

if exist "pids\genesis_worker_BTCUSDT.lock" (
    echo [OK] BTCUSDT ja esta em execucao.
) else (
    start "Genesis BTC - MARKET_MAKING_LATERAL [REAL]" cmd /k call "%CD%\iniciar_robo.bat" .env-btc MARKET_MAKING_LATERAL
    echo [INICIADO] BTC - Market Making Lateral.
)

echo Workers reais iniciados.
echo Para interromper, use Ctrl+C nas janelas dos workers.
pause
endlocal
