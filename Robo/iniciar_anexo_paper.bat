@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PAPER_MODE=1"

if not exist ".venv_local\Scripts\python.exe" (
    echo [ERRO] Ambiente Python nao encontrado: %CD%\.venv_local\Scripts\python.exe
    exit /b 1
)

for %%F in (.env-sol proximos_Ativos\.env-avax .env-btc) do (
    if not exist "%%F" (
        echo [ERRO] Perfil nao encontrado: %CD%\%%F
        exit /b 1
    )
)

rem O anexo e iniciado em paper/read-only. Para habilitar ordens reais,
rem altere os gates de escrita diretamente nos perfis e fa?a uma revis?o.
echo Iniciando perfil do anexo em modo paper/read-only...
start "Genesis SOL - Pullback 4h" cmd /k call "%CD%\iniciar_robo.bat" .env-sol PULLBACK_TREND_FOLLOWING
start "Genesis AVAX - Pullback 4h" cmd /k call "%CD%\iniciar_robo.bat" proximos_Ativos\.env-avax PULLBACK_TREND_FOLLOWING
start "Genesis BTC - Market Making 5m" cmd /k call "%CD%\iniciar_robo.bat" .env-btc MARKET_MAKING_LATERAL

echo Perfis iniciados:
echo   SOL/USDT  - Pullback Trend Following (contexto 4h)
echo   AVAX      - Pullback Trend Following (contexto 4h)
echo   BTC       - Market Making Lateral (5m)
endlocal
