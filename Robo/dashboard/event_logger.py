import json
from datetime import datetime

def registrar_evento(etapa, status, detalhes=None):
    evento = {
        "hora": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "etapa": etapa,
        "status": status,
        "detalhes": detalhes or ""
    }
    with open("eventos.json", "a", encoding="utf-8") as f:
        f.write(json.dumps(evento) + "\n")
