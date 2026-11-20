@echo off
setlocal enabledelayedexpansion

rem ====== Configuracao opcional ======
rem Defina manualmente a pasta do frontend:
set "FRONTEND_DIR=%~dp0ui\lovable_export\genesis-quant-terminal-main"

rem ====== Checagens basicas ======
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js nao encontrado. Instale o Node.js antes de continuar.
  pause
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo npm nao encontrado. Verifique sua instalacao do Node.js.
  pause
  exit /b 1
)

echo Iniciando frontend...

echo Pasta do frontend: %FRONTEND_DIR%

cd /d "%FRONTEND_DIR%"
if errorlevel 1 (
  echo Falha ao acessar a pasta do frontend.
  pause
  exit /b 1
)

if not exist node_modules (
  echo Dependencias nao encontradas. Instalando...
  npm install
  if errorlevel 1 (
    echo Falha ao instalar dependencias.
    pause
    exit /b 1
  )
)

echo Subindo o frontend (npm run dev)...
start "Vite Dev Server" cmd /k "npm run dev"

rem Aguarda alguns segundos antes de abrir o navegador.
rem O Vite esta configurado na porta 8080 (vite.config.ts).
timeout /t 3 >nul
start "" "http://localhost:8080"

endlocal
