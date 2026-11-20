@echo off
setlocal

cd /d "%~dp0ui\lovable_export\genesis-quant-terminal-main"

if not exist node_modules (
  echo [Lovable UI] Instalando dependencias...
  call npm install
  if errorlevel 1 (
    echo [Lovable UI] Falha ao instalar dependencias.
    pause
    exit /b 1
  )
)

echo [Lovable UI] Iniciando frontend em modo dev...
call npm run dev

endlocal
