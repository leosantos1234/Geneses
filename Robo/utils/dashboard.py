import React, { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { BarChart, Bar, XAxis, YAxis, Tooltip } from "recharts";

export default function DashboardTrading() {
  const [modo, setModo] = useState("moderado");
  const [decisao, setDecisao] = useState("Aguardando IA...");
  const [lucros, setLucros] = useState([]);
  const [operacoesAbertas, setOperacoesAbertas] = useState(0);
  const [totalTrades, setTotalTrades] = useState(0);
  const [sl, setSl] = useState(1.5); // Stop-Loss default 1.5%
  const [tp, setTp] = useState(3.0); // Take-Profit default 3.0%

  useEffect(() => {
    fetch("http://localhost:8000/status")
      .then((res) => res.json())
      .then((data) => {
        setDecisao(data.decisao || "Aguardando IA...");
        setOperacoesAbertas(data.abertas);
        setTotalTrades(data.total);
        setLucros(data.historicoLucros || []);
      });
  }, []);

  const alternarModo = () => {
    const novoModo = modo === "moderado" ? "agressivo" : modo === "agressivo" ? "conservador" : "moderado";
    setModo(novoModo);
    fetch("http://localhost:8000/mudar_modo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modo: novoModo }),
    });
  };

  const encerrarTodas = () => {
    fetch("http://localhost:8000/encerrar_todas", { method: "POST" });
  };

  const aplicarAjustes = () => {
    fetch("http://localhost:8000/ajustar_sl_tp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stop_loss: sl, take_profit: tp })
    });
  };

  return (
    <div className="p-6 grid gap-4 grid-cols-1 md:grid-cols-2">
      <Card>
        <CardContent>
          <h2 className="text-xl font-bold">Modo de Operação</h2>
          <p>Atual: <strong>{modo.toUpperCase()}</strong></p>
          <Button className="mt-2" onClick={alternarModo}>Alternar Modo</Button>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <h2 className="text-xl font-bold">Decisão Atual da IA</h2>
          <p>{decisao}</p>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <h2 className="text-xl font-bold">Resumo de Operações</h2>
          <p>Operações Abertas: {operacoesAbertas}</p>
          <p>Total de Trades: {totalTrades}</p>
          <Button className="mt-2" onClick={encerrarTodas}>Encerrar Todas as Ordens</Button>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <h2 className="text-xl font-bold">Histórico de Lucros</h2>
          <BarChart width={320} height={200} data={lucros}>
            <XAxis dataKey="data" />
            <YAxis />
            <Tooltip />
            <Bar dataKey="lucro" fill="#4ade80" />
          </BarChart>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <h2 className="text-xl font-bold">Ajustes Dinâmicos (SL / TP)</h2>
          <p>Stop-Loss (%): {sl.toFixed(2)}%</p>
          <Slider min={0.5} max={10} step={0.1} value={[sl]} onValueChange={([val]) => setSl(val)} />

          <p className="mt-4">Take-Profit (%): {tp.toFixed(2)}%</p>
          <Slider min={1} max={20} step={0.1} value={[tp]} onValueChange={([val]) => setTp(val)} />

          <Button className="mt-4" onClick={aplicarAjustes}>Aplicar Ajustes</Button>
        </CardContent>
      </Card>
    </div>
  );
}
