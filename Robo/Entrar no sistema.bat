@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PAPER_MODE=0"

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

start "Genesis SOL - Pullback REAL" cmd /k call "%CD%\iniciar_robo.bat" .env-sol PULLBACK_TREND_FOLLOWING
start "Genesis AVAX - Pullback REAL" cmd /k call "%CD%\iniciar_robo.bat" proximos_Ativos\.env-avax PULLBACK_TREND_FOLLOWING
start "Genesis BTC - Market Making REAL" cmd /k call "%CD%\iniciar_robo.bat" .env-btc MARKET_MAKING_LATERAL

echo Workers reais iniciados.
echo Para interromper, use Ctrl+C nas janelas dos workers.
endlocal
