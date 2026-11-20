@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
set "ENV_FILE=proximos_Ativos\.env-avax"
if not exist "%ENV_FILE%" (
    echo [ERRO] Perfil AVAX nao encontrado: %CD%\%ENV_FILE%
    pause
    exit /b 1
)
call "%CD%\iniciar_robo.bat" "%ENV_FILE%"
exit /b %ERRORLEVEL%
