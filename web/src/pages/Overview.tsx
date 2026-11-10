import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, flag, Overview, roleLabel } from "../api";

export function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stop = false;
    async function load() {
      try {
        const next = await api<Overview>("/api/overview");
        if (!stop) setData(next);
      } catch (err) {
        if (!stop) setError(err instanceof Error ? err.message : "ошибка");
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, []);

  if (!data && error) return <Banner text={error} />;
  if (!data) return <Banner text="Загрузка обзора…" />;
  if (!data.entry) {
    return (
      <Page title="Топология каскада" subtitle="Клиенты подключаются только к одному входу">
        <Empty
          title="Каскад ещё не собран"
          text="Добавьте вход, хотя бы один выход и при желании транзит. Панель сама выпустит ключи и свяжет узлы."
          action={<Link className="btn" to="/servers">Добавить сервер</Link>}
        />
      </Page>
    );
  }

  const kinds = [
    ["router", "Роутер"],
    ["phone", "Телефоны"],
    ["pc", "ПК"],
  ] as const;

  return (
    <Page title="Топология каскада" subtitle={data.demo ? "Демонстрационные адреса, ключи локальные" : "Живая схема"}>
      <div className="grid gap-4 xl:grid-cols-[220px_280px_1fr]">
        <div className="flex flex-col gap-3">
          {kinds.map(([kind, title]) => {
            const row = data.by_kind[kind] || { total: 0, online: 0 };
            return (
              <div key={kind} className="card flex items-center justify-between px-4 py-3">
                <div>
                  <div className="text-sm">{title}</div>
                  <div className="text-xs text-mist">{row.total} устр.</div>
                </div>
                <span className="text-xs text-teal">{row.online} онлайн</span>
              </div>
            );
          })}
        </div>
        <div className="flex flex-col gap-3">
          <div className="card p-4 shadow-glow">
            <div className="text-xs uppercase tracking-wider text-teal">Вход</div>
            <div className="mt-1 flex items-center justify-between">
              <div className="text-lg font-semibold">{data.entry.name}</div>
              <Status online={data.entry.status === "online"} />
            </div>
            <div className="mt-1 text-sm text-mist">{flag[data.entry.country] || ""} {data.entry.host}</div>
            <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <Metric label="CPU" value={data.entry.cpu != null ? `${data.entry.cpu}%` : "—"} />
              <Metric label="Скорость" value={data.speed_mbps ? `${data.speed_mbps} Мбит/с` : "—"} />
            </div>
          </div>
          <div className="card border-dashed px-4 py-3 text-sm text-mist">
            {data.standby_entry
              ? `Запасной вход: ${data.standby_entry.name}`
              : "Запасной вход (холодный) не назначен"}
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="card border-amber/40 p-4">
            <div className="text-xs text-amber">Напрямую</div>
            <div className="mt-2 text-3xl font-semibold">{data.direct_share}%</div>
            <div className="mt-1 text-sm text-mist">Интернет с адреса входа: банки, госуслуги, geosite:category-ru</div>
          </div>
          <div className="card border-teal/40 p-4">
            <div className="text-xs text-teal">Каскад {data.transit ? `через ${data.transit.name}` : ""}</div>
            <div className="mt-2 text-3xl font-semibold">{data.cascade_share}%</div>
            <div className="mt-3 flex flex-col gap-2">
              {data.exits.map((exit) => (
                <div key={exit.id} className="flex items-center justify-between rounded-xl bg-black/20 px-3 py-2">
                  <div>
                    <div className="text-sm">{flag[exit.country] || ""} {exit.name}</div>
                    <div className="text-xs text-mist">{roleLabel[exit.role]} · {exit.ping_ms != null ? `${exit.ping_ms} мс` : "нет пинга"}</div>
                  </div>
                  <div className="text-right">
                    <div className="font-mono text-sm">{exit.share ?? 0}%</div>
                    <Status online={exit.status === "online"} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <Stat title="Клиенты онлайн" value={String(data.clients_online)} hint={`из ${data.clients_total}`} series={data.sparks.clients} color="#2ee6c7" />
        <Stat title="Трафик всего" value={`${data.traffic_gb} ГБ`} hint="сумма клиентов" series={data.sparks.traffic} color="#8b6cff" />
        <Stat title="Пинг до выхода" value={data.ping_ms != null ? `${data.ping_ms} мс` : "—"} hint="среднее по выходам" series={data.sparks.ping} color="#2ee6c7" />
        <Stat title="Узлы" value={`${data.nodes_up}/${data.nodes_total}`} hint={data.nodes_up === data.nodes_total ? "все активны" : "есть офлайн"} series={data.sparks.nodes} color="#8b6cff" />
      </div>
    </Page>
  );
}

export function Page({ title, subtitle, children, extra }: { title: string; subtitle?: string; children: ReactNode; extra?: ReactNode }) {
  return (
    <main className="px-4 py-6 md:px-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold md:text-3xl">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-mist">{subtitle}</p>}
        </div>
        {extra}
      </div>
      {children}
    </main>
  );
}

export function Banner({ text }: { text: string }) {
  return <div className="m-6 rounded-xl border border-line bg-card px-4 py-3 text-sm">{text}</div>;
}

export function Empty({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return (
    <div className="card p-8">
      <h2 className="text-xl font-semibold">{title}</h2>
      <p className="mt-2 max-w-xl text-sm text-mist">{text}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

function Status({ online }: { online: boolean }) {
  return <span className={online ? "text-xs text-ok" : "text-xs text-rose"}>{online ? "онлайн" : "офлайн"}</span>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-mist">{label}</div>
      <div className="font-medium">{value}</div>
    </div>
  );
}

function Stat({ title, value, hint, series, color }: { title: string; value: string; hint: string; series: number[]; color: string }) {
  return (
    <div className="card p-4">
      <div className="text-sm text-mist">{title}</div>
      <div className="mt-2 flex items-end justify-between gap-2">
        <div>
          <div className="text-2xl font-semibold">{value}</div>
          <div className="text-xs text-mist">{hint}</div>
        </div>
        <Spark series={series} color={color} />
      </div>
    </div>
  );
}

export function Spark({ series, color }: { series: number[]; color: string }) {
  if (!series?.length) return null;
  const max = Math.max(...series, 1);
  const w = 92;
  const h = 36;
  const d = series
    .map((value, index) => {
      const x = (index / Math.max(series.length - 1, 1)) * w;
      const y = h - (value / max) * (h - 6) - 3;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} aria-hidden>
      <path d={d} fill="none" stroke={color} strokeWidth="2" />
    </svg>
  );
}
