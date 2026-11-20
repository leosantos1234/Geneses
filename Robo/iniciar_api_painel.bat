@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHON_EXE=%CD%\.venv_local\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [ERRO] Ambiente Python nao encontrado: %PYTHON_EXE%
    pause
    exit /b 1
)
if not exist "%CD%\api_ui.py" (
    echo [ERRO] API nao encontrada: %CD%\api_ui.py
    pause
    exit /b 1
)
set "PYTHONPATH=%CD%"
echo Iniciando API do painel em http://localhost:8010
echo Para parar: Ctrl+C
"%PYTHON_EXE%" -m uvicorn api_ui:app --host 0.0.0.0 --port 8010
set "EXIT_CODE=%ERRORLEVEL%"
echo API encerrada com codigo %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
