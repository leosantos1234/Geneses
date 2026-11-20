@echo off
setlocal
echo Encerrando somente as janelas do Genesis...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$targets = Get-Process -Name cmd -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'Genesis*' }; foreach ($p in $targets) { taskkill /PID $p.Id /T /F | Out-Null }"
REM Encerra todos os processos python.exe e uvicorn.exe

REM Mata todos os scripts Python (robôs e LSTM)
REM Processos filhos sao encerrados junto com cada janela Genesis.

REM Mata todos os scripts pythonw.exe (headless, pode ser útil se algo rodou com pythonw)
REM Nenhum processo Python global e encerrado.

REM Mata todas as instâncias do FastAPI (uvicorn)
REM Nenhum processo Uvicorn global e encerrado.

echo Todos os serviços foram encerrados.
echo Janelas e processos do Genesis encerrados.
timeout /t 2 /nobreak >nul
endlocal
