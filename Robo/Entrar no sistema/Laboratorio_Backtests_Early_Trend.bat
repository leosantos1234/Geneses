@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

rem Localiza a pasta Backtest_* sem depender do acento do nome "estratégias".
for /d %%D in ("Backtest_*") do set "BACKTEST_ROOT=%%~fD"
if not defined BACKTEST_ROOT (
    echo [ERRO] Pasta Backtest_* nao encontrada em %CD%
    pause
    exit /b 1
)
set "LAB_DIR=%BACKTEST_ROOT%\Baktest_aut"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PAPER_MODE=1"
set "ENABLE_LIVE_TRADING=0"
set "ALLOW_EXCHANGE_WRITE=0"
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

echo ================================================================
echo GENESIS - LABORATORIO LOCAL DE BACKTESTS - EARLY TREND
echo Somente simulacao. Nenhuma ordem sera enviada.
echo Ativos padrao: BTC, ETH, BNB, SOL, XRP e AVAX
echo Filtro padrao: retorno composto acima de 300%%
echo ================================================================
echo.

"%CD%\.venv_local\Scripts\python.exe" "%LAB_DIR%\laboratorio_backtests.py" --strategy EARLY_TREND %*

echo.
echo Resultados: %LAB_DIR%
pause
endlocal
