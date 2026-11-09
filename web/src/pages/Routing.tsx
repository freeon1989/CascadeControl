import { useEffect, useState } from "react";
import { api, formatHits, Rule, Server } from "../api";
import { Banner, Page } from "./Overview";

const actionStyle: Record<string, string> = {
  direct: "bg-amber/20 text-amber",
  cascade: "bg-teal/15 text-teal",
  exit: "bg-iris/20 text-iris",
  block: "bg-rose/20 text-rose",
};

const actionText: Record<string, string> = {
  direct: "Напрямую",
  cascade: "Каскад",
  exit: "Выход",
  block: "Блокировать",
};

export function RoutingPage() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [servers, setServers] = useState<Server[]>([]);
  const [balance, setBalance] = useState({ strategy: "least_ping", auto_failover: true, interval_sec: 30, weights: {} as Record<string, number> });
  const [addresses, setAddresses] = useState<{ id: string; value: string; source: string; source_date: string; pinned: boolean; excluded: boolean; action: string }[]>([]);
  const [error, setError] = useState("");
  const [drag, setDrag] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  async function load() {
    try {
      const [ruleRows, serverRows, balanceRow, addressRows] = await Promise.all([
        api<Rule[]>("/api/rules"),
        api<Server[]>("/api/servers"),
        api<typeof balance>("/api/balance"),
        api<typeof addresses>("/api/addresses"),
      ]);
      setRules(ruleRows);
      setServers(serverRows);
      setBalance(balanceRow);
      setAddresses(addressRows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function dropOn(target: string) {
    if (!rules || !drag || drag === target) return;
    const ids = rules.map((rule) => rule.id);
    const from = ids.indexOf(drag);
    const to = ids.indexOf(target);
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    setRules(await api<Rule[]>("/api/rules/order", { method: "PUT", body: JSON.stringify({ ids }) }));
    setDrag(null);
  }

  async function toggle(rule: Rule) {
    await api(`/api/rules/${rule.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !rule.enabled }) });
    await load();
  }

  async function saveBalance() {
    await api("/api/balance", { method: "PUT", body: JSON.stringify(balance) });
    setMessage("Балансировка сохранена. На вход она попадёт при следующей установке или обновлении.");
  }

  const exits = servers.filter((server) => server.role === "exit" || server.role === "standby_exit");

  return (
    <Page title="Правила маршрутизации на входе" subtitle="Правила применяются по порядку. Первое совпадение определяет маршрут.">
      {error && <Banner text={error} />}
      {!rules && <Banner text="Загрузка правил…" />}
      {rules && (
        <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
          <div className="card p-4">
            <div className="mb-3 text-sm text-mist">Правила маршрутизации</div>
            <div className="flex flex-col gap-2">
              {rules.map((rule, index) => (
                <div
                  key={rule.id}
                  draggable
                  onDragStart={() => setDrag(rule.id)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={() => void dropOn(rule.id)}
                  className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-black/20 px-3 py-3"
                >
                  <span className="cursor-grab text-mist">≡</span>
                  <span className="w-5 text-sm text-mist">{index + 1}</span>
                  <span className="min-w-[180px] flex-1 text-sm">{rule.name}</span>
                  <span className="text-mist">→</span>
                  <span className={`rounded-full px-3 py-1 text-xs ${actionStyle[rule.action] || ""}`}>
                    {actionText[rule.action] || rule.action}
                    {rule.exit_id ? ` ${exits.find((item) => item.id === rule.exit_id)?.name || ""}` : ""}
                  </span>
                  <button className={`h-6 w-11 rounded-full ${rule.enabled ? "bg-teal" : "bg-white/10"}`} onClick={() => void toggle(rule)} aria-label="переключить правило">
                    <span className={`block h-5 w-5 rounded-full bg-white transition ${rule.enabled ? "translate-x-5" : "translate-x-0.5"}`} />
                  </button>
                  <span className="w-16 text-right font-mono text-xs text-mist">{formatHits(rule.hits)}</span>
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs text-mist">Перетаскивайте правила. Выключенное правило пропускается, счётчик нажатий хранится в базе.</p>
            <div className="mt-4">
              <div className="mb-2 text-sm">Автодобавленные адреса</div>
              {addresses.length === 0 && <div className="text-xs text-mist">Пока пусто. Источники и детектор появятся здесь с датой и источником.</div>}
              <div className="flex flex-col gap-1">
                {addresses.slice(0, 8).map((row) => (
                  <div key={row.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className={row.excluded ? "text-mist line-through" : ""}>{row.value}</span>
                    <span className="text-mist">{row.source} · {row.source_date.slice(0, 10)}</span>
                    <span className="flex gap-2">
                      <button className="text-teal" onClick={() => api(`/api/addresses/${row.id}/pin`, { method: "POST" }).then(load)}>{row.pinned ? "открепить" : "закрепить"}</button>
                      <button className="text-rose" onClick={() => api(`/api/addresses/${row.id}/exclude`, { method: "POST" }).then(load)}>{row.excluded ? "вернуть" : "исключить"}</button>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="card p-4">
            <h2 className="text-sm font-semibold">Балансировка выходов</h2>
            <label className="mt-3 block text-xs text-mist">Стратегия
              <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={balance.strategy} onChange={(e) => setBalance({ ...balance, strategy: e.target.value })}>
                <option value="least_ping">По наименьшему пингу</option>
                <option value="weighted">По весам</option>
                <option value="round_robin">По кругу</option>
              </select>
            </label>
            <div className="mt-4 flex flex-col gap-3">
              {exits.map((exit) => (
                <label key={exit.id} className="text-xs text-mist">{exit.name} {exit.role === "standby_exit" ? "· резерв" : ""}
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={balance.weights[exit.id] ?? (exit.role === "standby_exit" ? 0 : 50)}
                    onChange={(e) => setBalance({ ...balance, weights: { ...balance.weights, [exit.id]: Number(e.target.value) } })}
                    className="mt-1 w-full"
                  />
                  <span className="font-mono text-foam">{balance.weights[exit.id] ?? (exit.role === "standby_exit" ? 0 : 50)}</span>
                </label>
              ))}
              {exits.length === 0 && <div className="text-xs text-mist">Выходы появятся после добавления серверов.</div>}
            </div>
            <label className="mt-4 flex items-center justify-between text-sm">
              Автопереключение при падении
              <input type="checkbox" checked={balance.auto_failover} onChange={(e) => setBalance({ ...balance, auto_failover: e.target.checked })} />
            </label>
            <label className="mt-3 block text-xs text-mist">Интервал проверки, с
              <input type="number" min={5} className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={balance.interval_sec} onChange={(e) => setBalance({ ...balance, interval_sec: Number(e.target.value) })} />
            </label>
            <button className="mt-4 w-full rounded-xl bg-tide py-3 font-semibold" onClick={() => void saveBalance()}>Применить</button>
            {message && <p className="mt-2 text-xs text-mist">{message}</p>}
          </div>
        </div>
      )}
    </Page>
  );
}
