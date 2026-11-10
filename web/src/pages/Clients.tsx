import { FormEvent, useEffect, useState } from "react";
import { api, Client, ExportBundle, Server } from "../api";
import { Banner, Page } from "./Overview";

const kinds = [
  ["router", "Роутер"],
  ["phone", "Телефон"],
  ["pc", "ПК"],
  ["laptop", "Ноутбук"],
];

export function ClientsPage() {
  const [rows, setRows] = useState<Client[] | null>(null);
  const [current, setCurrent] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [sync, setSync] = useState<string>("");

  async function load() {
    try {
      setRows(await api<Client[]>("/api/clients"));
      const servers = await api<Server[]>("/api/servers");
      const entry = servers.find((item) => item.role === "entry");
      setSync(entry?.live?.awg_sync?.message || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <Page
      title="Клиенты"
      subtitle="Все устройства ходят только на вход"
      extra={<button className="rounded-full bg-tide px-4 py-2 text-sm font-semibold" onClick={() => setCreating(true)}>+ Новый клиент</button>}
    >
      {error && <Banner text={error} />}
      {sync && <Banner text={`Синхронизация входа: ${sync}`} />}
      {!rows && <Banner text="Загрузка клиентов…" />}
      {rows && rows.length === 0 && <Banner text="Клиентов нет. Создайте первый — панель выпустит ключи AWG 3.1 или VLESS." />}
      <div className="grid gap-3 md:grid-cols-2">
        {rows?.map((client) => (
          <div key={client.id} className="card cursor-pointer p-4 text-left" onClick={() => setCurrent(client.id)}>
            <div className="flex items-center justify-between">
              <div className="font-semibold">{client.name}</div>
              <span className={client.online ? "text-xs text-ok" : "text-xs text-mist"}>{client.online ? "Онлайн" : "Офлайн"}</span>
            </div>
            <div className="mt-3 text-sm text-mist">Протокол: {client.protocol === "awg" ? "AWG 3.1" : "VLESS Reality"}</div>
            <div className="text-sm text-mist">Трафик: {client.total_gb} ГБ</div>
            <div className="mt-3 flex gap-3 text-xs">
              <span className="text-teal">открыть</span>
              <span className="text-rose" onClick={(event) => { event.stopPropagation(); if (confirm(`Удалить клиента «${client.name}»?`)) void api(`/api/clients/${client.id}`, { method: "DELETE" }).then(load); }}>удалить</span>
            </div>
          </div>
        ))}
      </div>
      {creating && <CreateClient onClose={() => { setCreating(false); void load(); }} />}
      {current && <ClientDrawer id={current} onClose={() => { setCurrent(null); void load(); }} />}
    </Page>
  );
}

function CreateClient({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("pc");
  const [protocol, setProtocol] = useState("awg");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/api/clients", { method: "POST", body: JSON.stringify({ name, kind, protocol }) });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50">
      <form className="h-full w-full max-w-md bg-[#0b1524] p-5" onSubmit={submit}>
        <div className="mb-4 flex justify-between"><h2 className="text-lg">Новый клиент</h2><button type="button" onClick={onClose}>закрыть</button></div>
        {error && <div className="mb-2 text-sm text-rose">{error}</div>}
        <label className="text-xs text-mist">Имя
          <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="mt-3 block text-xs text-mist">Тип
          <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={kind} onChange={(e) => setKind(e.target.value)}>
            {kinds.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
        <label className="mt-3 block text-xs text-mist">Протокол
          <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={protocol} onChange={(e) => setProtocol(e.target.value)}>
            <option value="awg">AmneziaWG 3.1</option>
            <option value="vless">VLESS Reality</option>
          </select>
        </label>
        <button className="mt-4 w-full rounded-xl bg-tide py-3">Создать</button>
      </form>
    </div>
  );
}

function ClientDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const [data, setData] = useState<(Client & { export: ExportBundle }) | null>(null);
  const [tab, setTab] = useState<"conf" | "vpn" | "sub">("conf");
  const [copied, setCopied] = useState("");

  async function load() {
    const detail = await api<Client & { export: ExportBundle }>(`/api/clients/${id}`);
    setData(detail);
    setTab(detail.protocol === "vless" ? "sub" : "conf");
  }

  useEffect(() => {
    let stop = false;
    let first = true;
    async function pull() {
      const detail = await api<Client & { export: ExportBundle }>(`/api/clients/${id}`);
      if (stop) return;
      setData(detail);
      if (first) {
        first = false;
        setTab(detail.protocol === "vless" ? "sub" : "conf");
      }
    }
    void pull();
    const timer = window.setInterval(() => void pull(), 15000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [id]);

  if (!data) return null;
  const exp = data.export;
  const text = tab === "conf" ? exp.conf : tab === "vpn" ? exp.vpn_uri : exp.vless_uri || exp.subscription;
  const qr = tab === "conf" ? exp.qr_conf : tab === "vpn" ? exp.qr_vpn : exp.qr_vless;

  async function copy() {
    await navigator.clipboard.writeText(text);
    setCopied("Скопировано");
  }

  function download() {
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = tab === "conf" ? `${data!.name}.conf` : `${data!.name}.txt`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50">
      <aside className="h-full w-full max-w-md overflow-y-auto bg-[#0b1524] p-5">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-xl font-semibold">{data.name}</h2>
            <div className={data.online ? "text-sm text-ok" : "text-sm text-mist"}>{data.online ? "Онлайн" : "Офлайн"}</div>
          </div>
          <button onClick={onClose}>закрыть</button>
        </div>
        {qr && <img alt="QR" src={qr} className="mx-auto mt-4 h-48 w-48 rounded-xl bg-white p-2" />}
        <div className="mt-4 grid grid-cols-3 gap-1 rounded-xl bg-white/5 p-1 text-xs">
          <Tab active={tab === "conf"} onClick={() => setTab("conf")}>AmneziaWG .conf</Tab>
          <Tab active={tab === "vpn"} onClick={() => setTab("vpn")}>Amnezia vpn://</Tab>
          <Tab active={tab === "sub"} onClick={() => setTab("sub")}>Подписка</Tab>
        </div>
        {exp.note && tab === "conf" && data.protocol !== "awg" && <p className="mt-2 text-xs text-mist">{exp.note}</p>}
        {!text && (
          <p className="mt-3 text-sm text-mist">
            {tab === "sub"
              ? "Подписка нужна клиентам VLESS. Для AmneziaWG сохраните файл .conf или ссылку vpn://."
              : "Для этой вкладки у клиента нет данных."}
          </p>
        )}
        <textarea readOnly className="mt-3 h-40 w-full rounded-xl border border-line bg-black/30 p-3 font-mono text-[11px]" value={text} />
        {tab === "sub" && exp.subscription_url && <div className="mt-2 break-all text-xs text-mist">{exp.subscription_url}</div>}
        <div className="mt-3 flex gap-2">
          <button className="flex-1 rounded-lg border border-line py-2 text-sm" onClick={download}>Скачать</button>
          <button className="flex-1 rounded-lg border border-line py-2 text-sm" onClick={() => void copy()}>Копировать</button>
        </div>
        {copied && <div className="mt-1 text-xs text-teal">{copied}</div>}
        <button
          className="mt-4 w-full rounded-xl border border-rose/50 py-3 text-rose"
          onClick={() => {
            if (!confirm("Перевыпустить ключи? Старый конфиг перестанет подключаться.")) return;
            void api(`/api/clients/${id}/reissue`, { method: "POST" }).then(load);
          }}
        >
          Перевыпустить ключи
        </button>
        <dl className="mt-4 space-y-2 text-sm">
          <Row k="Вход" v={data.entry || "—"} />
          <Row k="Последнее подключение" v={data.last_seen || "—"} />
          <Row k="Трафик" v={`${data.total_gb} ГБ`} />
        </dl>
        <Chart series={data.traffic} />
      </aside>
    </div>
  );
}

function Tab({ active, children, onClick }: { active: boolean; children: string; onClick: () => void }) {
  return <button className={`rounded-lg py-2 ${active ? "bg-tide text-ink" : "text-mist"}`} onClick={onClick}>{children}</button>;
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="flex justify-between gap-3"><dt className="text-mist">{k}</dt><dd>{v}</dd></div>;
}

function Chart({ series }: { series: number[] }) {
  if (!series?.length) return null;
  const max = Math.max(...series, 1);
  const w = 320;
  const h = 90;
  const d = series.map((value, index) => {
    const x = (index / Math.max(series.length - 1, 1)) * w;
    const y = h - (value / max) * (h - 8) - 4;
    return `${x},${y}`;
  }).join(" ");
  return (
    <div className="mt-4">
      <div className="mb-1 text-xs text-mist">Трафик за последние точки</div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full">
        <polyline fill="none" stroke="#2ee6c7" strokeWidth="2" points={d} />
      </svg>
    </div>
  );
}
