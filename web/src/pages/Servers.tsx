import { FormEvent, useEffect, useState } from "react";
import { api, Check, flag, roleLabel, Server } from "../api";
import { saveDownload } from "../saveFile";
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
  const [editId, setEditId] = useState<string | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);
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
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, []);

  async function refreshStatus(id: string) {
    setError("");
    setBusy(id + "status");
    try {
      await api(`/api/servers/${id}/status`, { method: "POST" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    } finally {
      setBusy("");
    }
  }

  async function act(id: string, action: string) {
    setError("");
    setBusy(id + action);
    try {
      if (action === "remove") {
        if (!confirm("Удалить узел? Контейнеры amnezia-* останутся.")) return;
        const job = await api<{ job_id: string }>(`/api/servers/${id}`, { method: "DELETE" });
        await waitJob(job.job_id);
      } else {
        if (action === "reconfigure" && !confirm("Перенастроить сервер? Текущие настройки запишутся по SSH заново. Сервер из списка не удалится, контейнеры amnezia-* не трогаются.")) return;
        const job = await api<{ job_id: string }>(`/api/servers/${id}/${action}`, { method: "POST" });
        const result = await waitJob(job.job_id);
        if (!result.ok) setError(result.lines[result.lines.length - 1] || "задача завершилась с ошибкой");
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
          <table className="w-full min-w-[1100px] text-left text-sm">
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
                    {(row.uptime || row.disk) && <div className="text-xs text-mist">{[row.uptime && `аптайм ${row.uptime}`, row.disk && `диск ${row.disk}`].filter(Boolean).join(" · ")}</div>}
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
                    <ProtocolLive live={row.live} />
                  </td>
                  <td className="px-3 py-3">
                    <div>{row.cpu != null ? `${row.cpu}%` : "—"}</div>
                    <Spark series={row.spark || []} color="#2ee6c7" />
                  </td>
                  <td className="whitespace-nowrap px-3 py-3">{row.ram || "—"}</td>
                  <td className="px-3 py-3 text-ok">{row.ping_ms != null ? `${row.ping_ms} мс` : "—"}</td>
                  <td className="px-3 py-3">
                    <button type="button" className="text-left" onClick={() => setReportId(row.id)}>
                      <span className="inline-flex items-center gap-1">
                        <span className={`inline-block h-2 w-2 rounded-full ${row.status === "online" ? "bg-ok" : row.status === "offline" || row.status === "error" ? "bg-rose" : "bg-mist"}`} />
                        <span className={row.status === "online" ? "text-ok" : row.status === "offline" || row.status === "error" ? "text-rose" : "text-mist"}>
                          {row.status === "online" ? "Онлайн" : row.status === "offline" ? "Офлайн" : row.status === "error" ? "Ошибка" : "Не проверен"}
                        </span>
                      </span>
                      {row.checked_at && <div className="text-xs text-mist">проверка {checkedLabel(row.checked_at)}</div>}
                      {row.last_error && <div className="max-w-[220px] text-xs text-rose underline decoration-rose/40">{row.last_error}</div>}
                      {!row.last_error && <div className="text-xs text-mist underline decoration-white/20">отчёт проверки</div>}
                    </button>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex max-w-[280px] flex-wrap gap-1">
                      <Mini disabled={busy !== ""} onClick={() => void refreshStatus(row.id)}>Статус</Mini>
                      <Mini disabled={busy !== ""} onClick={() => setEditId(row.id)}>Изменить</Mini>
                      <Mini disabled={busy !== ""} onClick={() => act(row.id, "precheck")}>Перепроверить</Mini>
                      <Mini disabled={busy !== ""} onClick={() => act(row.id, "reconfigure")}>Перенастроить</Mini>
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
      {editId && rows && (
        <EditServer
          server={rows.find((item) => item.id === editId) || null}
          onClose={() => { setEditId(null); void load(); }}
        />
      )}
      {reportId && rows && (
        <PrecheckReport
          server={rows.find((item) => item.id === reportId) || null}
          busy={busy !== ""}
          onClose={() => setReportId(null)}
          onRecheck={() => act(reportId, "precheck")}
        />
      )}
    </Page>
  );
}

function ProtocolLive({ live }: { live?: Server["live"] }) {
  if (!live) return null;
  const bits: string[] = [];
  if (live.awg) bits.push(`AWG: ${live.awg.handshakes} рукопожатий`);
  if (live.xray === "active") bits.push("Xray: активен");
  else if (live.xray && live.xray !== "missing") bits.push("Xray: нет");
  if (live.agent === "active") bits.push("агент: работает");
  if (live.docker === "active") bits.push("Docker: работает");
  if (!bits.length) return null;
  return <div className="mt-1 text-[11px] text-mist">{bits.join(" · ")}</div>;
}

function checkedLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function Mini({ children, onClick, disabled }: { children: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button className="rounded-md border border-line px-2 py-1 text-xs text-mist hover:text-foam disabled:opacity-40" disabled={disabled} onClick={onClick}>
      {children}
    </button>
  );
}

function EditServer({ server, onClose }: { server: Server | null; onClose: () => void }) {
  const entryRole = server?.role === "entry" || server?.role === "standby_entry";
  const [form, setForm] = useState({
    name: server?.name || "",
    host: server?.host || "",
    ssh_port: server?.ssh_port || 22,
    ssh_user: server?.ssh_user || "root",
    auth_type: server?.auth_type || "password",
    secret: "",
    role: server?.role || "exit",
    country: server?.country || "",
    awg: server?.protocol_ids?.includes("awg") ?? entryRole,
    vless: server?.protocol_ids?.includes("vless") ?? true,
  });
  const [error, setError] = useState("");
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [busy, setBusy] = useState(false);
  const canAwg = form.role === "entry" || form.role === "standby_entry";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!server) return;
    setError("");
    setBusy(true);
    const protocols = [
      ...(form.awg && canAwg ? ["awg"] : []),
      ...(form.vless ? ["vless"] : []),
    ];
    try {
      await api<Server>(`/api/servers/${server.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: form.name,
          host: form.host,
          ssh_port: form.ssh_port,
          ssh_user: form.ssh_user,
          auth_type: form.auth_type,
          secret: form.secret,
          role: form.role,
          country: form.country,
          protocols,
        }),
      });
      const job = await api<{ job_id: string }>(`/api/servers/${server.id}/precheck`, { method: "POST" });
      await waitJob(job.job_id);
      const fresh = (await api<Server[]>("/api/servers")).find((item) => item.id === server.id);
      setChecks(fresh?.precheck?.checks || []);
      if (fresh && !fresh.precheck?.ok && !fresh.demo) setError(fresh.last_error || "Проверка не пройдена");
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50">
      <aside className="h-full w-full max-w-md overflow-y-auto border-l border-line bg-[#0b1524] p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Изменение сервера</h2>
          <button onClick={onClose} className="text-mist">закрыть</button>
        </div>
        <p className="mb-3 text-xs text-mist">Ключи узла, клиенты и статистика сохраняются. Пустой пароль или ключ означает «не менять». После сохранения проверка запускается снова.</p>
        {error && <div className="mb-3 rounded-lg bg-rose/10 px-3 py-2 text-sm text-rose">{error}</div>}
        {!server && <div className="text-sm text-mist">Сервер не найден.</div>}
        {server && (
          <form className="flex flex-col gap-3" onSubmit={submit}>
            <Field label="Имя" value={form.name} onChange={(v) => setForm({ ...form, name: v })} />
            <Field label="IP или хост" value={form.host} onChange={(v) => setForm({ ...form, host: v })} />
            <Field label="SSH порт" value={String(form.ssh_port)} onChange={(v) => setForm({ ...form, ssh_port: Number(v) || 22 })} />
            <Field label="Пользователь" value={form.ssh_user} onChange={(v) => setForm({ ...form, ssh_user: v })} />
            <label className="text-xs text-mist">Доступ
              <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={form.auth_type} onChange={(e) => setForm({ ...form, auth_type: e.target.value })}>
                <option value="password">Пароль</option>
                <option value="key">SSH-ключ</option>
              </select>
            </label>
            <label className="text-xs text-mist">{form.auth_type === "key" ? "Новый приватный ключ" : "Новый пароль"}
              <textarea className="mt-1 h-24 w-full rounded-lg border border-line bg-ink px-3 py-2" placeholder="не менять" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />
            </label>
            <label className="text-xs text-mist">Страна
              <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })} placeholder="DE" />
            </label>
            <div className="grid grid-cols-2 gap-2">
              {roles.map(([id, label]) => (
                <button
                  type="button"
                  key={id}
                  onClick={() => setForm({ ...form, role: id, awg: id === "entry" || id === "standby_entry" ? form.awg : false })}
                  className={`rounded-lg border px-2 py-2 text-sm ${form.role === id ? "border-teal text-teal" : "border-line text-mist"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="rounded-lg border border-line px-3 py-2 text-sm">
              <div className="mb-2 text-xs text-mist">Протоколы</div>
              <label className="mb-1 flex items-center gap-2">
                <input type="checkbox" checked={form.awg && canAwg} disabled={!canAwg} onChange={(e) => setForm({ ...form, awg: e.target.checked })} />
                AWG 3.1 {canAwg ? "" : "(только вход)"}
              </label>
              <label className="mb-1 flex items-center gap-2">
                <input type="checkbox" checked={form.vless} onChange={(e) => setForm({ ...form, vless: e.target.checked })} />
                VLESS Reality
              </label>
              <div className="text-xs text-mist">Hysteria2 в этой версии выключен</div>
            </div>
            <button className="mt-2 rounded-xl bg-gradient-to-r from-tide to-iris py-3 font-semibold disabled:opacity-40" type="submit" disabled={busy}>
              {busy ? "Сохраняю и проверяю…" : "Сохранить и проверить"}
            </button>
          </form>
        )}
        {checks && (
          <div className="mt-4">
            <h3 className="mb-2 text-sm text-mist">Отчёт проверки</h3>
            <CheckList checks={checks} />
          </div>
        )}
      </aside>
    </div>
  );
}

function PrecheckReport({
  server,
  busy,
  onClose,
  onRecheck,
}: {
  server: Server | null;
  busy: boolean;
  onClose: () => void;
  onRecheck: () => void;
}) {
  const checks = server?.precheck?.checks || [];
  const [notice, setNotice] = useState("");
  const report = checks.map((check) => `${check.title}: ${check.level}\n${check.detail}${check.hint ? `\n${check.hint}` : ""}`).join("\n\n");
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-line bg-[#0b1524] p-5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Предпроверка{server ? `: ${server.name}` : ""}</h2>
          <button onClick={onClose} className="text-mist">закрыть</button>
        </div>
        {!server && <div className="text-sm text-mist">Сервер не найден.</div>}
        {server && checks.length === 0 && <div className="mb-3 text-sm text-mist">Отчёта ещё нет. Запустите проверку.</div>}
        {server?.precheck && (
          <div className={`mb-3 text-sm ${server.precheck.ok ? "text-ok" : "text-rose"}`}>
            {server.precheck.ok ? "Блокирующих пунктов нет. Предупреждения установку не останавливают." : "Есть блокирующие пункты. Установка закрыта, пока они не исправлены."}
          </div>
        )}
        <CheckList checks={checks} />
        {notice && <div className="mt-3 text-xs text-teal">{notice}</div>}
        <button
          className="mt-4 w-full rounded-xl border border-line py-3 text-sm disabled:opacity-40"
          disabled={!report}
          onClick={() => {
            void saveDownload(`precheck-${server?.name || "server"}.txt`, report).then((saved) => {
              if (saved) setNotice(`Сохранено: ${saved}`);
            });
          }}
        >
          Скачать отчёт
        </button>
        <button className="mt-2 w-full rounded-xl border border-line py-3 text-sm disabled:opacity-40" disabled={busy || !server} onClick={onRecheck}>
          {busy ? "Проверяю…" : "Повторить проверку"}
        </button>
      </div>
    </div>
  );
}

function CheckList({ checks }: { checks: Check[] }) {
  return (
    <div className="flex flex-col gap-2">
      {checks.map((check) => (
        <div key={check.id} className="rounded-lg bg-white/5 px-3 py-2 text-sm">
          <div className="flex items-start justify-between gap-3">
            <div className="font-medium">{check.title}</div>
            <span className={check.level === "fail" ? "text-rose" : check.level === "warn" ? "text-amber" : "text-ok"}>
              {check.level === "fail" ? "нет" : check.level === "warn" ? "внимание" : "ок"}
            </span>
          </div>
          <div className="text-xs text-mist">{check.detail}</div>
          {check.hint && <div className="mt-1 text-xs text-foam/80">{check.hint}</div>}
        </div>
      ))}
    </div>
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
                    {check.hint && <div className="mt-1 text-xs text-foam/80">{check.hint}</div>}
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
