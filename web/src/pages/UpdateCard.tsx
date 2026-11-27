import { useEffect, useRef, useState } from "react";
import { api, UpdateStatus } from "../api";

export function useUpdateStatus(pollWhileBusy = true) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const timer = useRef<number | null>(null);

  async function refresh() {
    try {
      setStatus(await api<UpdateStatus>("/api/update"));
    } catch {
      /* панель может закрываться на время обновления */
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const busy = status ? ["downloading", "verifying", "installing"].includes(status.job.state) : false;
  useEffect(() => {
    if (!pollWhileBusy || !busy) return;
    timer.current = window.setInterval(() => void refresh(), 800);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [busy, pollWhileBusy]);

  return { status, setStatus, refresh };
}

function when(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ru-RU");
}

const STATE_TEXT: Record<string, string> = {
  downloading: "Скачиваю архив с GitHub…",
  verifying: "Проверяю контрольную сумму и распаковываю…",
  installing: "Устанавливаю. Окно закроется и откроется заново.",
};

export function UpdateCard() {
  const { status, setStatus, refresh } = useUpdateStatus();
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  async function check() {
    setChecking(true);
    setError("");
    try {
      setStatus(await api<UpdateStatus>("/api/update/check", { method: "POST" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    } finally {
      setChecking(false);
    }
  }

  async function install() {
    if (!status?.latest) return;
    if (!confirm(`Установить версию ${status.latest.version}? Архив скачается с GitHub и проверится по SHA-256, затем программа закроется и откроется уже новой. Ваши серверы, клиенты и ключи останутся на месте. Если новая версия не запустится, вернётся прежняя.`)) return;
    setError("");
    try {
      setStatus(await api<UpdateStatus>("/api/update/install", { method: "POST", body: JSON.stringify({ version: status.latest.version, confirm: true }) }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  async function prefs(body: { auto_check?: boolean; skipped_version?: string }) {
    setError("");
    try {
      setStatus(await api<UpdateStatus>("/api/update/settings", { method: "PUT", body: JSON.stringify(body) }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  async function ack() {
    await api("/api/update/ack", { method: "POST" });
    await refresh();
  }

  if (!status) return null;
  const busy = ["downloading", "verifying", "installing"].includes(status.job.state);
  const latest = status.latest;
  return (
    <section className="card p-4 text-sm lg:col-span-2" data-testid="update-card">
      <h2 className="font-semibold">Обновления</h2>
      <p className="mt-2 text-mist">Установлена версия {status.current}. Проверка читает только публичные релизы на GitHub, ничего не ставится без вашего нажатия.</p>
      {status.result && (
        <div className={`mt-3 rounded-lg border px-3 py-2 ${status.result.ok ? "border-ok/40 text-ok" : "border-rose/40 text-rose"}`}>
          {status.result.ok
            ? `Обновлено до версии ${status.result.version}.`
            : `Обновление до ${status.result.wanted || "новой версии"} не удалось${status.result.rolled_back ? `, возвращена версия ${status.result.version}` : ""}. ${status.result.error || ""}`}
          <button className="ml-3 text-xs underline" onClick={() => void ack()}>Понятно</button>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button className="rounded-lg bg-tide px-4 py-2 disabled:opacity-40" disabled={checking || busy} onClick={() => void check()}>
          {checking ? "Проверяю…" : "Проверить обновления"}
        </button>
        <label className="flex items-center gap-2 text-mist">
          <input type="checkbox" checked={status.auto_check} onChange={(e) => void prefs({ auto_check: e.target.checked })} />
          Проверять при запуске
        </label>
        {status.last_check && <span className="text-xs text-mist">Последняя проверка: {when(status.last_check)}</span>}
      </div>
      {(error || status.check_error) && <p className="mt-2 text-xs text-rose">{error || status.check_error}</p>}
      {latest && !status.available && <p className="mt-3 text-ok">У вас последняя версия ({status.current}).</p>}
      {latest && status.available && (
        <div className="mt-3 rounded-xl border border-line p-3">
          <div className="font-medium">
            Доступна версия {latest.version}
            {status.skipped && <span className="ml-2 text-xs text-mist">(пропущена)</span>}
          </div>
          {latest.published_at && <div className="text-xs text-mist">Выпущена {when(latest.published_at)}</div>}
          {latest.notes && <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-xs text-mist">{latest.notes}</pre>}
          {latest.url && <div className="mt-2 break-all text-xs text-mist">Страница релиза: {latest.url}</div>}
          {!latest.has_assets && <p className="mt-2 text-xs text-rose">В этом релизе пока нет файлов для установки из программы.</p>}
          {!status.can_apply && <p className="mt-2 text-xs text-mist">Установка из программы работает в собранной версии для Windows. Здесь скачайте релиз вручную.</p>}
          {busy && (
            <div className="mt-3">
              <div className="text-xs text-teal">{STATE_TEXT[status.job.state]}</div>
              <div className="mt-1 h-2 overflow-hidden rounded bg-white/10">
                <div className="h-full bg-teal" style={{ width: `${Math.round((status.job.state === "downloading" ? status.job.progress : 1) * 100)}%` }} />
              </div>
            </div>
          )}
          {status.job.state === "error" && <p className="mt-2 text-xs text-rose">{status.job.error}</p>}
          <div className="mt-3 flex gap-2">
            <button className="rounded-lg bg-iris px-4 py-2 disabled:opacity-40" disabled={busy || !status.can_apply || !latest.has_assets} onClick={() => void install()}>
              Обновить
            </button>
            {status.skipped ? (
              <button className="rounded-lg border border-line px-3 py-2 text-mist" onClick={() => void prefs({ skipped_version: "" })}>Не пропускать</button>
            ) : (
              <button className="rounded-lg border border-line px-3 py-2 text-mist disabled:opacity-40" disabled={busy} onClick={() => void prefs({ skipped_version: latest.version })}>
                Пропустить эту версию
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
