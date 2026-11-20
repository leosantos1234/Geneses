# dashboard/dash_launcher.py

import logging
from multiprocessing import Process, set_start_method

from .dash_server import app

def run_dash_app():
    try:
        app.run_server(debug=False, use_reloader=False)
    except Exception as e:
        logging.error(f"Erro ao iniciar o servidor Dash: {e}")

def iniciar_dashboard():
    try:
        set_start_method("spawn", force=True)  # Para evitar erro no Windows
    except RuntimeError:
        pass  # Já foi definido antes

    dash_process = Process(target=run_dash_app)
    dash_process.start()
    return dash_process
