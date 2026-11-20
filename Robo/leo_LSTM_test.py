from keras.models import load_model
import pickle
import numpy as np

model = load_model("lstm_btc_4features.h5", compile=False)

with open("scaler_btc.pkl", "rb") as f:
    scaler = pickle.load(f)

# cria um exemplo qualquer para testar
dummy = np.random.rand(1,10,4)
dummy_scaled = scaler.transform(dummy.reshape(-1,4)).reshape(1,10,4)

pred = model.predict(dummy_scaled)
print(pred)
