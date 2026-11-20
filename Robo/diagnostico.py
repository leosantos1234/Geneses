import os
import re

# Padrões suspeitos para buscar
padroes = [
    r'sys\.stdout\.close\(',
    r'sys\.stderr\.close\(',
    r'logging\.shutdown\(',
    r'close\(\)',  # Pode pegar arquivos também, mas é pra caçar
    r'logging\.root\.handlers',
    r'StreamHandler\(',
    r'sys\.stdout\s*=', 
    r'sys\.stderr\s*=', 
]

print("Buscando padrões perigosos para logging...\n")

for root, dirs, files in os.walk('.'):
    for file in files:
        if file.endswith('.py'):
            path = os.path.join(root, file)
            with open(path, encoding='utf-8') as f:
                code = f.read()
                for padrao in padroes:
                    for match in re.finditer(padrao, code):
                        linha = code[:match.start()].count('\n') + 1
                        print(f'⚠️  Padrão encontrado em: {path}, linha {linha}: {match.group(0)}')
