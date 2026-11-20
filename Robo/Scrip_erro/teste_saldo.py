from compra1 import session

def testar_tipos_de_saldo():
    for tipo in ["UNIFIED", "CONTRACT", "SPOT"]:
        try:
            print(f"🔍 Testando accountType = {tipo}")
            response = session.get_wallet_balance(accountType=tipo)
            if response.get("retCode") == 0:
                for item in response["result"]["list"]:
                    for coin in item["coin"]:
                        if coin["coin"] == "USDT":
                            saldo = float(coin["walletBalance"])
                            print(f"✅ Tipo {tipo} FUNCIONA → Saldo USDT: {saldo}")
                            return
            else:
                print(f"⚠️ Falha: {response.get('retMsg')}")
        except Exception as e:
            print(f"❌ Erro com tipo {tipo}: {e}")

testar_tipos_de_saldo()
