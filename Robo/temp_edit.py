with open('monitoramento.py', 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace('''# � MARCAR TRADE COMO ENCERRADO IMEDIATAMENTE
                trade_state["encerrado"] = True
                
                # 🛡️ Verificar se o trade está positivo — se sim, NÃO sair''', '''# 🛡️ Verificar se o trade está positivo — se sim, NÃO sair''')
with open('monitoramento.py', 'w', encoding='utf-8') as f:
    f.write(content)