@echo off
setlocal EnableExtensions

chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%~dp0.."
set "ENV_FILE=%PROJECT_DIR%\.env-btc"

cd /d "%PROJECT_DIR%"

set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="
set "http_proxy="
set "https_proxy="
set "all_proxy="

if exist "%ENV_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /R /B /C:"BYBIT_API_KEY=" /C:"BYBIT_API_SECRET=" "%ENV_FILE%"`) do (
        set "%%A=%%B"
    )
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PYTHON_CMD="
if exist ".\venv_tf\Scripts\python.exe" set "PYTHON_CMD=".\venv_tf\Scripts\python.exe""
if not defined PYTHON_CMD if exist ".\.venv_backtest\Scripts\python.exe" set "PYTHON_CMD=".\.venv_backtest\Scripts\python.exe""
if not defined PYTHON_CMD where py >nul 2>&1 && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD where python >nul 2>&1 && set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    echo [ERRO] Python nao encontrado.
    echo Instale o Python ou ative um ambiente virtual do projeto.
    pause
    exit /b 1
)

echo ============================================
echo AUDITORIA BACKTEST - BIG RUNNER
echo Projeto: %PROJECT_DIR%
echo Script : research\big_backtest_runner.py
echo Cmd    : %PYTHON_CMD%
echo ============================================

call %PYTHON_CMD% research\big_backtest_runner.py
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo.
    echo [OK] Auditoria concluida com sucesso.
) else (
    echo.
    echo [FALHA] A auditoria terminou com erro. Codigo: %EXIT_CODE%
)

echo.
pause
exit /b %EXIT_CODE%
