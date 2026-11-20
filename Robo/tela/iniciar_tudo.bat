@echo off
setlocal EnableExtensions
set "ROBO_DIR=%~dp0.."
set "ENV_FILE=%~1"
if not defined ENV_FILE set "ENV_FILE=.env-btc"
cd /d "%ROBO_DIR%"

echo ============================================
echo Genesis - inicializacao do sistema
echo Pasta: %ROBO_DIR%
echo ============================================

for %%F in (iniciar_robo.bat iniciar_api_painel.bat iniciar_painel.bat) do (
    if not exist "%ROBO_DIR%\%%F" (
        echo [ERRO] Arquivo nao encontrado: %ROBO_DIR%\%%F
        pause
        exit /b 1
    )
)
if not exist "%ROBO_DIR%\%ENV_FILE%" (
    echo [ERRO] Perfil de exchange nao encontrado: %ROBO_DIR%\%ENV_FILE%
    pause
    exit /b 1
)

echo Iniciando API do painel...
start "Genesis API do Painel" cmd /k call iniciar_api_painel.bat

echo Iniciando painel web...
start "Genesis Painel" cmd /k call iniciar_painel.bat

echo Iniciando Robo Fluxo com %ENV_FILE%...
start "Genesis Robo Fluxo" cmd /k call iniciar_robo.bat %ENV_FILE%

echo.
echo Servicos iniciados em janelas separadas:
echo   API:    http://localhost:8010
echo   Painel: http://localhost:8080
echo   Robo:   perfil %ENV_FILE%
echo.
echo ATENCAO: confirme o modo da Bybit antes de operar.
pause
exit /b 0
