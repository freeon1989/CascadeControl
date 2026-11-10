import { useEffect, useState } from "react";
import { api } from "../api";
import { SourceError } from "../sourceError";
import { Banner, Page } from "./Overview";

type Settings = {
  version: string;
  bind: string;
  data_dir: string;
  telemetry: boolean;
  demo: boolean;
  dns1: string;
  dns2: string;
  presets: { id: string; title: string; action: string; domains: number; cidrs: number }[];
  hysteria2: { enabled: boolean; reason: string };
};

type Source = {
  id: string;
  title: string;
  url: string;
  kind: string;
  enabled: boolean;
  last_fetch: string;
  last_count: number;
  last_error: string;
};

export function SettingsPage() {
  const [data, setData] = useState<Settings | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [dns, setDns] = useState({ dns1: "", dns2: "" });
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function load() {
    try {
      const settings = await api<Settings>("/api/settings");
      setData(settings);
      setDns({ dns1: settings.dns1, dns2: settings.dns2 });
      setSources(await api<Source[]>("/api/sources"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  if (!data && !error) return <Banner text="Загрузка настроек…" />;

  return (
    <Page title="Настройки" subtitle={`Версия ${data?.version || ""}`}>
      {error && <Banner text={error} />}
      {data && (
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="card p-4 text-sm">
            <h2 className="font-semibold">Панель</h2>
            <p className="mt-2 text-mist">Адрес: {data.bind}. Наружу панель не слушает.</p>
            <p className="text-mist">Данные: {data.data_dir}</p>
            <p className="text-mist">Телеметрия: {data.telemetry ? "включена" : "выключена"}</p>
            <p className="mt-2 text-mist">{data.hysteria2.reason}</p>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <label className="text-xs text-mist">DNS 1
                <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={dns.dns1} onChange={(e) => setDns({ ...dns, dns1: e.target.value })} />
              </label>
              <label className="text-xs text-mist">DNS 2
                <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={dns.dns2} onChange={(e) => setDns({ ...dns, dns2: e.target.value })} />
              </label>
            </div>
            <p className="mt-3 text-xs text-mist">Эти адреса попадают в новые конфиги клиентов. Уже выданный файл на телефоне не меняется, пока его не скачают снова.</p>
            <button className="mt-3 rounded-lg bg-tide px-4 py-2" onClick={() => api("/api/settings/dns", { method: "PUT", body: JSON.stringify(dns) }).then(() => setMessage("DNS сохранён"))}>Сохранить DNS</button>
            {data.demo && (
              <button className="mt-3 block text-sm text-rose" onClick={() => api("/api/settings/clear-demo", { method: "POST" }).then(() => { setMessage("Демо-данные удалены"); void load(); })}>
                Удалить демо-данные
              </button>
            )}
            {message && <p className="mt-2 text-xs text-teal">{message}</p>}
          </section>
          <section className="card p-4">
            <h2 className="font-semibold">Источники списков</h2>
            <div className="mt-3 flex flex-col gap-3">
              {sources.map((source) => (
                <div key={source.id} className="rounded-xl border border-line p-3 text-sm">
                  <div className="font-medium">{source.title}</div>
                  <div className="break-all text-xs text-mist">{source.url}</div>
                  <div className="mt-1 text-xs text-mist">{source.kind} · {source.enabled ? "включён" : "выключен"} · записей {source.last_count}</div>
                  {source.last_error && <SourceError raw={source.last_error} />}
                  <button className="mt-2 text-xs text-teal" onClick={() => api(`/api/sources/${source.id}/refresh`, { method: "POST" }).then(load).catch((err: Error) => setError(err.message))}>
                    Обновить сейчас
                  </button>
                </div>
              ))}
            </div>
          </section>
          <section className="card p-4 lg:col-span-2">
            <h2 className="font-semibold">Пресеты сервисов</h2>
            <div className="mt-3 grid gap-2 md:grid-cols-3">
              {data.presets.map((preset) => (
                <button key={preset.id} className="rounded-xl border border-line px-3 py-3 text-left text-sm" onClick={() => api(`/api/presets/${preset.id}/apply`, { method: "POST" }).then(() => setMessage(`Правило «${preset.title}» добавлено`))}>
                  <div>{preset.title}</div>
                  <div className="text-xs text-mist">{preset.domains} доменов · {preset.cidrs} сетей · {preset.action}</div>
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
    </Page>
  );
}
