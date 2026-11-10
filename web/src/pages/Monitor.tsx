import { FormEvent, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { Banner, Page, Spark } from "./Overview";

type Card = {
  id: string;
  name: string;
  role: string;
  host: string;
  status: string;
  cpu: number | null;
  ram: string;
  disk: string;
  uptime: string;
  ping_ms: number | null;
  clients_online: number;
  dns_status: string;
  checked_at: string;
};

type Detail = {
  id: string;
  name: string;
  role: string;
  status: string;
  cpu: number | null;
  ram: string;
  disk: string;
  uptime: string;
  ping_ms: number | null;
  disk_free: number | null;
  containers: { name: string; type: string; up: boolean; status: string }[];
  protocols: { id: string; type: string; up: boolean; detail: string }[];
  series: { cpu: (number | null)[]; mem: (number | null)[]; net_out: number[]; net_in: number[]; clients: (number | null)[] };
  top_clients: { id: string; name: string; online: boolean; address: string; total_bytes: number; rx_bytes: number; tx_bytes: number; last_seen: string; traffic: number[] }[];
  dns: {
    status: string;
    note: string;
    mode: string;
    drift: boolean;
    wanted: boolean;
    queries: number;
    top: { site: string; n: number }[];
    per_client: { client: string; total: number; top: { site: string; n: number }[] }[];
    retention_hours: number;
    cap_mb: number;
  };
};

const dnsLabel: Record<string, string> = {
  ok: "есть запросы",
  empty: "журнал пуст",
  not_enabled: "не включён",
  no_container: "нет DNS",
  unreadable: "не прочитан",
};

function fmtBytes(value: number | null | undefined): string {
  if (value == null) return "неизвестно";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let n = value;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${i ? n.toFixed(n < 10 ? 1 : 0) : Math.round(n)} ${units[i]}`;
}

export function MonitorPage() {
  const [cards, setCards] = useState<Card[] | null>(null);
  const [summary, setSummary] = useState<string[]>([]);
  const [current, setCurrent] = useState<string>("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [hours, setHours] = useState(24);
  const [cap, setCap] = useState(50);
  const [busy, setBusy] = useState(false);
  const [dns, setDns] = useState({ dns1: "", dns2: "" });
  const [message, setMessage] = useState("");

  async function loadList() {
    const board = await api<{ servers: Card[]; summary: string[] }>("/api/monitor");
    setCards(board.servers);
    setSummary(board.summary);
    setCurrent((prev) => prev || board.servers[0]?.id || "");
  }

  async function loadDetail(id: string) {
    if (!id) return;
    const next = await api<Detail>(`/api/monitor/${id}?hours=24`);
    setDetail(next);
    setHours(next.dns.retention_hours);
    setCap(next.dns.cap_mb);
  }

  useEffect(() => {
    let stop = false;
    async function pull() {
      try {
        await loadList();
        const settings = await api<{ dns1: string; dns2: string }>("/api/settings");
        if (!stop) setDns({ dns1: settings.dns1, dns2: settings.dns2 });
      } catch (err) {
        if (!stop) setError(err instanceof Error ? err.message : "ошибка");
      }
    }
    void pull();
    const timer = window.setInterval(() => void pull(), 15000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!current) return;
    let stop = false;
    async function pull() {
      try {
        const next = await api<Detail>(`/api/monitor/${current}?hours=24`);
        if (stop) return;
        setDetail(next);
      } catch (err) {
        if (!stop) setError(err instanceof Error ? err.message : "ошибка");
      }
    }
    void pull();
    const timer = window.setInterval(() => void pull(), 15000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [current]);

  async function toggle(enable: boolean) {
    if (!detail) return;
    const verb = enable ? "Включить" : "Выключить";
    const where = detail.dns.mode === "xray" ? "журнал Xray на входе" : "журнал Unbound в amnezia-dns";
    const text = enable
      ? `${verb} ${where}? Будут сохраняться домены клиентов ${hours} ч, не больше ${cap} МБ. Контейнер DNS или Xray кратко перезапустится.`
      : `${verb} ${where}? Новые запросы писаться не будут.`;
    if (!window.confirm(text)) return;
    setBusy(true);
    setMessage("");
    try {
      await api(`/api/servers/${detail.id}/dns-log`, {
        method: "POST",
        body: JSON.stringify({ enable, confirm: true, retention_hours: hours, cap_mb: cap }),
      });
      setMessage(enable ? "Включение записано в журнал. Обновится после следующего опроса." : "Выключение записано в журнал.");
      await loadDetail(detail.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function saveDns(event: FormEvent) {
    event.preventDefault();
    await api("/api/settings/dns", { method: "PUT", body: JSON.stringify(dns) });
    setMessage("DNS для новых конфигов сохранён. Уже выданные файлы не меняются.");
  }

  const numbers = (series: (number | null)[] | undefined) => (series || []).map((item) => item || 0);

  return (
    <Page title="Мониторинг" subtitle="Нагрузка, контейнеры, трафик и сайты по журналу DNS">
      {error && <Banner text={error} />}
      {!cards && <Banner text="Загрузка мониторинга…" />}
      {cards && cards.length === 0 && <Banner text="Серверов нет. Добавьте вход или выход, затем нажмите Статус." />}
      {summary.length > 0 && (
        <section className="card mb-4 p-4 text-sm">
          <h2 className="font-semibold">Сводка</h2>
          <ul className="mt-2 space-y-1 text-mist">
            {summary.map((line) => <li key={line}>{line}</li>)}
          </ul>
        </section>
      )}
      <div className="mb-4 flex flex-wrap gap-2">
        {cards?.map((card) => (
          <button key={card.id} className={`rounded-xl border px-3 py-2 text-left text-sm ${card.id === current ? "border-teal" : "border-line"}`} onClick={() => setCurrent(card.id)}>
            <div className="font-medium">{card.name}</div>
            <div className={card.status === "online" ? "text-xs text-ok" : "text-xs text-rose"}>{card.status === "online" ? "на связи" : "нет связи"}</div>
          </button>
        ))}
      </div>
      {detail && (
        <div className="grid gap-4">
          <section className="grid gap-3 md:grid-cols-4">
            <Tile title="CPU" value={detail.cpu != null ? `${detail.cpu}%` : "—"} extra={<Spark series={numbers(detail.series.cpu)} color="#2ee6c7" />} />
            <Tile title="Память" value={detail.ram || "—"} extra={<Spark series={numbers(detail.series.mem)} color="#8b6cff" />} />
            <Tile title="Диск" value={detail.disk || "—"} extra={<span className="text-xs text-mist">свободно {fmtBytes(detail.disk_free)}</span>} />
            <Tile title="Аптайм" value={detail.uptime || "—"} extra={<span className="text-xs text-mist">{detail.ping_ms != null ? `${detail.ping_ms} мс` : "нет пинга"}</span>} />
          </section>
          <section className="card p-4">
            <h2 className="font-semibold">Контейнеры и протоколы</h2>
            <div className="mt-3 flex flex-wrap gap-2">
              {detail.containers.length === 0 && detail.protocols.length === 0 && <span className="text-sm text-mist">Контейнеры ещё не видны. Нажмите Статус на сервере.</span>}
              {detail.protocols.map((item) => (
                <span key={item.id} className={`rounded-full border px-3 py-1 text-xs ${item.up ? "border-ok/40 text-ok" : "border-rose/40 text-rose"}`}>
                  {item.type}: {item.id} {item.up ? "работает" : "остановлен"}
                </span>
              ))}
            </div>
          </section>
          <section className="card p-4">
            <h2 className="font-semibold">Трафик интерфейса за сутки</h2>
            <p className="mt-1 text-xs text-mist">Исходящий и входящий трафик сервера между опросами. Трафик клиентов AWG ниже, он копится отдельно и не сбрасывается при перезапуске интерфейса.</p>
            <div className="mt-3"><Bars out={detail.series.net_out} inn={detail.series.net_in} /></div>
          </section>
          <section className="card p-4">
            <h2 className="font-semibold">Клиенты по трафику</h2>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-mist"><tr><th>Имя</th><th>Адрес</th><th>Статус</th><th>Трафик</th><th></th></tr></thead>
                <tbody>
                  {detail.top_clients.map((client) => (
                    <tr key={client.id} className="border-t border-line">
                      <td className="py-2">{client.name}</td>
                      <td className="text-mist">{client.address || "—"}</td>
                      <td className={client.online ? "text-ok" : "text-mist"}>{client.online ? "онлайн" : "офлайн"}</td>
                      <td>{fmtBytes(client.total_bytes)}</td>
                      <td><Spark series={client.traffic.length ? client.traffic : [0]} color="#2ee6c7" /></td>
                    </tr>
                  ))}
                  {detail.top_clients.length === 0 && <tr><td className="py-3 text-mist" colSpan={5}>Клиентов на этом узле нет.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-semibold">Сайты (по DNS-запросам)</h2>
              <span className="text-xs text-mist">{dnsLabel[detail.dns.status] || detail.dns.status} · {detail.dns.queries} за сутки</span>
            </div>
            {detail.dns.note && <p className="mt-2 text-sm text-mist">{detail.dns.note}</p>}
            {detail.dns.drift && <p className="mt-2 text-sm text-rose">Запись снова выключена. Контейнер могли пересоздать. Нажмите «Применить снова».</p>}
            <div className="mt-3 grid gap-4 lg:grid-cols-2">
              <div>
                <div className="text-xs text-mist">По серверу</div>
                <BarsList rows={detail.dns.top} />
              </div>
              <div>
                <div className="text-xs text-mist">По клиентам</div>
                {detail.dns.per_client.length === 0 && <p className="mt-2 text-sm text-mist">Пока нет строк, которые удалось сопоставить.</p>}
                {detail.dns.per_client.map((row) => (
                  <div key={row.client} className="mt-2">
                    <div className="text-sm">{row.client}</div>
                    <BarsList rows={row.top} />
                  </div>
                ))}
              </div>
            </div>
            <div className="mt-4 flex flex-wrap items-end gap-3 text-sm">
              <label className="text-xs text-mist">Хранить, часы
                <input className="mt-1 block w-24 rounded-lg border border-line bg-ink px-3 py-2" type="number" min={1} max={168} value={hours} onChange={(e) => setHours(Number(e.target.value))} />
              </label>
              <label className="text-xs text-mist">Потолок, МБ
                <input className="mt-1 block w-24 rounded-lg border border-line bg-ink px-3 py-2" type="number" min={5} max={500} value={cap} onChange={(e) => setCap(Number(e.target.value))} />
              </label>
              <button className="rounded-lg bg-tide px-4 py-2" disabled={busy} onClick={() => void toggle(true)}>{detail.dns.drift ? "Применить снова" : "Включить"}</button>
              <button className="rounded-lg border border-rose/50 px-4 py-2 text-rose" disabled={busy} onClick={() => void toggle(false)}>Выключить</button>
            </div>
            <p className="mt-2 text-xs text-mist">Перед включением свободно {fmtBytes(detail.disk_free)}. Ротация каждые 15 минут, архивы старше срока и сверх потолка удаляются. Кнопки пишут строки в журнал.</p>
          </section>
          <form className="card p-4" onSubmit={(event) => void saveDns(event)}>
            <h2 className="font-semibold">Какой DNS получают клиенты</h2>
            <p className="mt-1 text-sm text-mist">Новые конфиги AWG берут эти адреса. Сайты на входе каскада читаются из журнала Xray по имени TLS, даже если DNS остаётся публичным.</p>
            <div className="mt-3 grid max-w-lg grid-cols-2 gap-2">
              <input className="rounded-lg border border-line bg-ink px-3 py-2" value={dns.dns1} onChange={(e) => setDns({ ...dns, dns1: e.target.value })} placeholder="DNS 1" />
              <input className="rounded-lg border border-line bg-ink px-3 py-2" value={dns.dns2} onChange={(e) => setDns({ ...dns, dns2: e.target.value })} placeholder="DNS 2" />
            </div>
            <button className="mt-3 rounded-lg border border-line px-4 py-2 text-sm">Сохранить DNS</button>
          </form>
          {message && <p className="text-sm text-teal">{message}</p>}
        </div>
      )}
    </Page>
  );
}

function Tile({ title, value, extra }: { title: string; value: string; extra?: ReactNode }) {
  return (
    <div className="card p-4">
      <div className="text-xs text-mist">{title}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
      <div className="mt-2">{extra}</div>
    </div>
  );
}

function Bars({ out, inn }: { out: number[]; inn: number[] }) {
  const max = Math.max(1, ...out, ...inn);
  return (
    <div className="flex h-24 items-end gap-1">
      {out.map((value, index) => (
        <div key={index} className="flex h-full flex-1 items-end gap-px">
          <div className="w-1/2 rounded-sm bg-teal" style={{ height: `${(value / max) * 100}%` }} />
          <div className="w-1/2 rounded-sm bg-[#8b6cff]" style={{ height: `${((inn[index] || 0) / max) * 100}%` }} />
        </div>
      ))}
    </div>
  );
}

function BarsList({ rows }: { rows: { site: string; n: number }[] }) {
  if (!rows.length) return <p className="mt-2 text-sm text-mist">Нет данных за сутки.</p>;
  const max = Math.max(...rows.map((row) => row.n), 1);
  return (
    <div className="mt-2 space-y-1">
      {rows.map((row) => (
        <div key={row.site} className="grid grid-cols-[1fr_80px] items-center gap-2 text-sm">
          <div className="truncate">{row.site}</div>
          <div className="h-2 rounded bg-white/10">
            <div className="h-2 rounded bg-teal" style={{ width: `${(row.n / max) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}
