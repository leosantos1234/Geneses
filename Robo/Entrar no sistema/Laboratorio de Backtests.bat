@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

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
echo GENESIS - LABORATORIO LOCAL DE BACKTESTS
echo Somente simulacao. Nenhuma ordem sera enviada.
echo Ativos padrao: SOLUSDT e AVAXUSDT
echo Filtro padrao: retorno composto acima de 300%%
echo ================================================================
echo.

"%CD%\.venv_local\Scripts\python.exe" "%CD%\Backtest_estrategias\Baktest_aut\laboratorio_backtests.py" %*

echo.
echo Resultados: %CD%\Backtest_estrategias\Baktest_aut
pause
endlocal
