import { FormEvent, useEffect, useState } from "react";
import { api, Check, flag, roleLabel, Server } from "../api";
import { Banner, Page, Spark } from "./Overview";

const roles = [
  ["entry", "Вход"],
  ["standby_entry", "Запасной вход"],
  ["transit", "Транзит"],
  ["exit", "Выход"],
  ["standby_exit", "Резервный выход"],
] as const;

export function ServersPage() {
  const [rows, setRows] = useState<Server[] | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");

  async function load() {
    try {
      setRows(await api<Server[]>("/api/servers"));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function act(id: string, action: string) {
    setBusy(id + action);
    try {
      if (action === "remove") {
        if (!confirm("Удалить узел? Контейнеры amnezia-* останутся.")) return;
        const job = await api<{ job_id: string }>(`/api/servers/${id}`, { method: "DELETE" });
        await waitJob(job.job_id);
      } else {
        const job = await api<{ job_id: string }>(`/api/servers/${id}/${action}`, { method: "POST" });
        await waitJob(job.job_id);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    } finally {
      setBusy("");
    }
  }

  return (
    <Page
      title="Серверы"
      subtitle="Управление серверами каскада VPN"
      extra={<button className="rounded-full bg-iris px-4 py-2 text-sm font-semibold" onClick={() => setOpen(true)}>+ Добавить сервер</button>}
    >
      {error && <Banner text={error} />}
      {!rows && <Banner text="Загрузка серверов…" />}
      {rows && rows.length === 0 && <Banner text="Серверов нет. Добавьте первый — панель проверит KVM, систему и свободные порты." />}
      {rows && rows.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[920px] text-left text-sm">
            <thead className="text-xs uppercase text-mist">
              <tr>
                {["Имя", "IP", "Роль", "Страна", "Протоколы", "CPU", "RAM", "Пинг", "Статус", ""].map((h) => (
                  <th key={h} className="px-3 py-3 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-t border-line/70">
                  <td className="px-3 py-3">
                    <div className="font-medium">{row.name}</div>
                    <div className="text-xs text-mist">{roleLabel[row.role]}{row.demo ? " · демо" : ""}{row.config_dirty ? " · не отправлено" : ""}</div>
                  </td>
                  <td className="px-3 py-3 font-mono text-xs">{row.host}</td>
                  <td className="px-3 py-3"><span className="rounded-full bg-white/5 px-2 py-1 text-xs">{roleLabel[row.role]}</span></td>
                  <td className="px-3 py-3">{flag[row.country] || "—"} {row.country}</td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap gap-1">
                      {row.protocols.map((item) => (
                        <span key={item} className="rounded-md bg-iris/20 px-2 py-0.5 text-xs text-iris">{item}</span>
                      ))}
                      <span className="rounded-md bg-white/5 px-2 py-0.5 text-xs text-mist">Hysteria2 выкл</span>
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <div>{row.cpu != null ? `${row.cpu}%` : "—"}</div>
                    <Spark series={row.spark || []} color="#2ee6c7" />
                  </td>
                  <td className="whitespace-nowrap px-3 py-3">{row.ram || "—"}</td>
                  <td className="px-3 py-3 text-ok">{row.ping_ms != null ? `${row.ping_ms} мс` : "—"}</td>
                  <td className="px-3 py-3">
                    <span className={row.status === "online" ? "text-ok" : row.status === "error" ? "text-rose" : "text-mist"}>
                      {row.status === "online" ? "Онлайн" : row.status === "offline" ? "Офлайн" : row.status === "error" ? "Ошибка" : "Не проверен"}
                    </span>
                    {row.last_error && <div className="max-w-[180px] text-xs text-rose">{row.last_error}</div>}
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex flex-nowrap gap-1">
                      <Mini disabled={busy !== ""} onClick={() => act(row.id, "update")}>Обновить</Mini>
                      <Mini disabled={busy !== ""} onClick={() => act(row.id, "reinstall")}>Переустановить</Mini>
                      <Mini disabled={busy !== ""} onClick={() => act(row.id, "remove")}>Удалить</Mini>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && <AddServer onClose={() => { setOpen(false); void load(); }} />}
    </Page>
  );
}

function Mini({ children, onClick, disabled }: { children: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button className="rounded-md border border-line px-2 py-1 text-xs text-mist hover:text-foam disabled:opacity-40" disabled={disabled} onClick={onClick}>
      {children}
    </button>
  );
}

function AddServer({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(1);
  const [server, setServer] = useState<Server | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({
    name: "",
    host: "",
    ssh_port: 22,
    ssh_user: "root",
    auth_type: "password",
    secret: "",
    role: "exit",
    country: "",
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const created = await api<Server>("/api/servers", { method: "POST", body: JSON.stringify(form) });
      setServer(created);
      setStep(2);
      const job = await api<{ job_id: string }>(`/api/servers/${created.id}/precheck`, { method: "POST" });
      const lines = await waitJob(job.job_id, setLog);
      const fresh = (await api<Server[]>("/api/servers")).find((item) => item.id === created.id) || created;
      setServer(fresh);
      if (!lines.ok && !fresh.demo) setError("Проверка не пройдена, установка закрыта");
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  async function install() {
    if (!server) return;
    setStep(3);
    setError("");
    try {
      const job = await api<{ job_id: string }>(`/api/servers/${server.id}/install`, { method: "POST" });
      const result = await waitJob(job.job_id, setLog);
      if (!result.ok) setError("Установка завершилась с ошибкой, журнал ниже");
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  const checks: Check[] = server?.precheck?.checks || [];

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50">
      <aside className="h-full w-full max-w-md overflow-y-auto border-l border-line bg-[#0b1524] p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Добавление сервера</h2>
          <button onClick={onClose} className="text-mist">закрыть</button>
        </div>
        <Steps step={step} />
        {error && <div className="mb-3 rounded-lg bg-rose/10 px-3 py-2 text-sm text-rose">{error}</div>}
        {step === 1 && (
          <form className="flex flex-col gap-3" onSubmit={submit}>
            <Field label="Имя" value={form.name} onChange={(v) => setForm({ ...form, name: v })} />
            <Field label="IP адрес" value={form.host} onChange={(v) => setForm({ ...form, host: v })} />
            <Field label="SSH порт" value={String(form.ssh_port)} onChange={(v) => setForm({ ...form, ssh_port: Number(v) || 22 })} />
            <Field label="Пользователь" value={form.ssh_user} onChange={(v) => setForm({ ...form, ssh_user: v })} />
            <label className="text-xs text-mist">Доступ
              <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={form.auth_type} onChange={(e) => setForm({ ...form, auth_type: e.target.value })}>
                <option value="password">Пароль</option>
                <option value="key">SSH-ключ</option>
              </select>
            </label>
            <label className="text-xs text-mist">{form.auth_type === "key" ? "Приватный ключ" : "Пароль"}
              <textarea className="mt-1 h-24 w-full rounded-lg border border-line bg-ink px-3 py-2" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />
            </label>
            <label className="text-xs text-mist">Страна
              <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })} placeholder="DE" />
            </label>
            <div className="grid grid-cols-2 gap-2">
              {roles.map(([id, label]) => (
                <button type="button" key={id} onClick={() => setForm({ ...form, role: id })} className={`rounded-lg border px-2 py-2 text-sm ${form.role === id ? "border-teal text-teal" : "border-line text-mist"}`}>
                  {label}
                </button>
              ))}
            </div>
            <button className="mt-2 rounded-xl bg-gradient-to-r from-tide to-iris py-3 font-semibold" type="submit">Проверить</button>
          </form>
        )}
        {step === 2 && (
          <div>
            <h3 className="mb-3 text-sm text-mist">Результаты проверки</h3>
            <div className="flex flex-col gap-2">
              {checks.length === 0 && <div className="text-sm text-mist">Жду отчёт с сервера…</div>}
              {checks.map((check) => (
                <div key={check.id} className="flex items-start justify-between gap-3 rounded-lg bg-white/5 px-3 py-2 text-sm">
                  <div>
                    <div>{check.title}</div>
                    <div className="text-xs text-mist">{check.detail}</div>
                  </div>
                  <span className={check.level === "fail" ? "text-rose" : check.level === "warn" ? "text-amber" : "text-ok"}>
                    {check.level === "fail" ? "нет" : check.level === "warn" ? "внимание" : "ок"}
                  </span>
                </div>
              ))}
            </div>
            <button className="mt-4 w-full rounded-xl bg-gradient-to-r from-tide to-iris py-3 font-semibold disabled:opacity-40" disabled={!server?.precheck?.ok && !server?.demo} onClick={() => void install()}>
              Развернуть
            </button>
          </div>
        )}
        {step === 3 && (
          <div>
            <h3 className="mb-2 text-sm">Журнал установки</h3>
            <pre className="max-h-[420px] overflow-auto rounded-xl bg-black/40 p-3 text-xs text-teal">{log.join("\n") || "…"}</pre>
          </div>
        )}
      </aside>
    </div>
  );
}

function Steps({ step }: { step: number }) {
  const names = ["Подключение", "Проверка", "Установка"];
  return (
    <div className="mb-4 flex gap-2 text-xs">
      {names.map((name, index) => (
        <div key={name} className={step === index + 1 ? "text-teal" : "text-mist"}>
          {index + 1}. {name}
        </div>
      ))}
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="text-xs text-mist">{label}
      <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2 text-foam" value={value} onChange={(e) => onChange(e.target.value)} required />
    </label>
  );
}

async function waitJob(jobId: string, onLog?: (lines: string[]) => void): Promise<{ ok: boolean; lines: string[] }> {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const job = await api<{ status: string; lines: { message: string }[] }>(`/api/jobs/${jobId}`);
    const lines = job.lines.map((line) => line.message);
    onLog?.(lines);
    if (job.status !== "running") return { ok: job.status === "done", lines };
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return { ok: false, lines: ["таймаут ожидания журнала"] };
}
