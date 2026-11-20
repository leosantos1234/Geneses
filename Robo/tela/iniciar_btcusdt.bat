@echo off
REM Ativar ambiente do robô e rodar o Trading_ai.py BTC SEM abrir janela
cd /d C:\Users\emgepron\Documents\robo\venv_tf\Scripts
call activate.bat
cd /d C:\Users\emgepron\Documents\robo
pythonw trading_bot_geneses.py .env-btc

REM Subir IA BTC headless (NÃO abre janela)
cd /d C:\Users\emgepron\Documents\Agente_IA
call agente-ia-env\Scripts\activate.bat
REM A linha abaixo ainda abre janela! Para rodar TOTALMENTE oculto, o painel deve chamar uvicorn com CREATE_NO_WINDOW (ver abaixo)
start cmd /k "uvicorn api_agente_ia:app --host 0.0.0.0 --port 8000"

