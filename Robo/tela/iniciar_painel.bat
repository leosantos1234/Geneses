@echo off
REM Ativar o ambiente virtual e iniciar o painel Streamlit automaticamente

cd /d C:\Users\emgepron\Documents\robo\venv_tf\Scripts
call activate.bat
cd /d C:\Users\emgepron\Documents\robo
start "" streamlit run Dashboard_3.py
