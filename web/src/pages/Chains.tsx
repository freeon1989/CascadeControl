import { FormEvent, useEffect, useState } from "react";
import { api, Chain, ExitGroup, Server } from "../api";
import { saveDownload } from "../saveFile";
import { Banner, Page } from "./Overview";

export function ChainsPage() {
  const [servers, setServers] = useState<Server[]>([]);
  const [chains, setChains] = useState<Chain[]>([]);
  const [groups, setGroups] = useState<ExitGroup[]>([]);
  const [error, setError] = useState("");
  const [log, setLog] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [chainForm, setChainForm] = useState<null | { id: string; name: string; entry_id: string; transit_ids: string[]; exit_id: string; group_id: string; mode: "exit" | "group" }>(null);
  const [groupForm, setGroupForm] = useState<null | { id: string; name: string; members: { server_id: string; weight: number; standby: boolean }[] }>(null);

  async function load() {
    try {
      const [serverRows, chainRows, groupRows] = await Promise.all([
        api<Server[]>("/api/servers"),
        api<Chain[]>("/api/chains"),
        api<ExitGroup[]>("/api/groups"),
      ]);
      setServers(serverRows);
      setChains(chainRows);
      setGroups(groupRows);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const entries = servers.filter((server) => server.role === "entry" || server.role === "standby_entry");
  const transits = servers.filter((server) => server.role === "transit");
  const exits = servers.filter((server) => server.role === "exit" || server.role === "standby_exit");

  async function saveChain(event: FormEvent) {
    event.preventDefault();
    if (!chainForm) return;
    const body = {
      name: chainForm.name,
      entry_id: chainForm.entry_id,
      transit_ids: chainForm.transit_ids,
      exit_id: chainForm.mode === "exit" ? chainForm.exit_id : null,
      group_id: chainForm.mode === "group" ? chainForm.group_id : null,
    };
    try {
      if (chainForm.id) await api(`/api/chains/${chainForm.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/api/chains", { method: "POST", body: JSON.stringify(body) });
      setChainForm(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  async function saveGroup(event: FormEvent) {
    event.preventDefault();
    if (!groupForm) return;
    const body = { name: groupForm.name, members: groupForm.members };
    try {
      if (groupForm.id) await api(`/api/groups/${groupForm.id}`, { method: "PUT", body: JSON.stringify(body) });
      else await api("/api/groups", { method: "POST", body: JSON.stringify(body) });
      setGroupForm(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  async function autowire() {
    setError("");
    try {
      await api("/api/cascade/autowire", { method: "POST" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  async function deployAll() {
    if (!confirm("Развернуть всё? Сначала выходы, потом транзит, затем вход. Серверы из списка не удаляются.")) return;
    setBusy(true);
    setError("");
    setLog(["запуск"]);
    try {
      const job = await api<{ job_id: string }>("/api/cascade/deploy", { method: "POST" });
      for (let attempt = 0; attempt < 200; attempt += 1) {
        const body = await api<{ status: string; lines: { message: string }[] }>(`/api/jobs/${job.job_id}`);
        setLog(body.lines.map((line) => line.message));
        if (body.status !== "running") {
          if (body.status !== "done") setError("Установка остановилась, журнал ниже");
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 400));
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page
      title="Каскад"
      subtitle="Один вход, необязательные транзиты и группа выходов. Демо-узлы можно включать в схему, но по SSH они не ставятся."
      extra={
        <div className="flex flex-wrap gap-2">
          <button className="rounded-full border border-teal px-4 py-2 text-sm text-teal" onClick={() => void autowire()}>Собрать автоматически</button>
          <button className="rounded-full bg-tide px-4 py-2 text-sm font-semibold disabled:opacity-40" disabled={busy} onClick={() => void deployAll()}>Развернуть всё</button>
        </div>
      }
    >
      {error && <Banner text={error} />}
      {chains.length === 0 && (
        <div className="card mb-4 p-4 text-sm">
          <p>Цепочки ещё нет. Если вход и выходы уже добавлены, «Собрать автоматически» создаст группу из всех выходов и одну цепочку.</p>
          <button className="mt-3 rounded-lg bg-tide px-3 py-2 text-sm" onClick={() => void autowire()}>Собрать автоматически</button>
        </div>
      )}
      {log.length > 0 && <DeployLog lines={log} />}
      <div className="grid gap-4 xl:grid-cols-2">
        <section className="card p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">Цепочки</h2>
            <button className="rounded-lg bg-tide px-3 py-1 text-sm" onClick={() => setChainForm({ id: "", name: "", entry_id: entries[0]?.id || "", transit_ids: [], exit_id: exits[0]?.id || "", group_id: groups[0]?.id || "", mode: groups[0] ? "group" : "exit" })}>Новая цепочка</button>
          </div>
          {chains.length === 0 && <p className="text-sm text-mist">Цепочек нет. Нужен вход и хотя бы один выход или группа.</p>}
          <div className="flex flex-col gap-2">
            {chains.map((chain) => (
              <div key={chain.id} className="rounded-xl border border-line px-3 py-2 text-sm">
                <div className="font-medium">{chain.name}</div>
                <div className="text-xs text-mist">
                  {chain.entry?.name || "вход"}{chain.entry?.demo ? " (демо)" : ""} → {chain.transits.map((item) => item?.name).filter(Boolean).join(", ") || "без транзита"} → {chain.exit?.name || (chain.group_id ? "группа" : "выход")}
                </div>
                <div className="mt-1 flex gap-3 text-xs">
                  <button className="text-teal" onClick={() => setChainForm({ id: chain.id, name: chain.name, entry_id: chain.entry_id, transit_ids: chain.transit_ids, exit_id: chain.exit_id || "", group_id: chain.group_id || "", mode: chain.group_id ? "group" : "exit" })}>изменить</button>
                  <button className="text-rose" onClick={() => { if (confirm(`Удалить каскад «${chain.name}»?`)) void api(`/api/chains/${chain.id}`, { method: "DELETE" }).then(load).catch((err: Error) => setError(err.message)); }}>удалить</button>
                </div>
              </div>
            ))}
          </div>
        </section>
        <section className="card p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">Группы выходов</h2>
            <button className="rounded-lg bg-iris px-3 py-1 text-sm" onClick={() => setGroupForm({ id: "", name: "", members: exits.slice(0, 1).map((exit) => ({ server_id: exit.id, weight: 50, standby: exit.role === "standby_exit" })) })}>Новая группа</button>
          </div>
          {groups.length === 0 && <p className="text-sm text-mist">Групп нет. Веса задаются здесь, резервный выход можно отметить.</p>}
          {groups.map((group) => (
            <div key={group.id} className="mb-2 rounded-xl border border-line px-3 py-2 text-sm">
              <div className="font-medium">{group.name}</div>
              <div className="text-xs text-mist">{group.members.map((member) => `${member.name} ${member.weight}${member.standby ? " резерв" : ""}`).join(", ") || "пусто"}</div>
              <div className="mt-1 flex gap-3 text-xs">
                <button className="text-teal" onClick={() => setGroupForm({ id: group.id, name: group.name, members: group.members.map((member) => ({ server_id: member.server_id, weight: member.weight, standby: member.standby })) })}>изменить</button>
                <button className="text-rose" onClick={() => { if (confirm(`Удалить группу «${group.name}»?`)) void api(`/api/groups/${group.id}`, { method: "DELETE" }).then(load).catch((err: Error) => setError(err.message)); }}>удалить</button>
              </div>
            </div>
          ))}
        </section>
      </div>
      {chainForm && (
        <form className="fixed inset-0 z-40 flex justify-end bg-black/50" onSubmit={saveChain}>
          <div className="h-full w-full max-w-md overflow-y-auto bg-[#0b1524] p-5">
            <div className="mb-3 flex justify-between"><h2>Цепочка</h2><button type="button" onClick={() => setChainForm(null)}>закрыть</button></div>
            <label className="text-xs text-mist">Имя<input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={chainForm.name} onChange={(e) => setChainForm({ ...chainForm, name: e.target.value })} required /></label>
            <label className="mt-3 block text-xs text-mist">Вход (ровно один)
              <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={chainForm.entry_id} onChange={(e) => setChainForm({ ...chainForm, entry_id: e.target.value })}>
                {entries.map((server) => <option key={server.id} value={server.id}>{server.name}{server.demo ? " демо" : ""}</option>)}
              </select>
            </label>
            <div className="mt-3 text-xs text-mist">Транзиты, по порядку</div>
            {transits.map((server) => (
              <label key={server.id} className="mt-1 flex items-center gap-2 text-sm">
                <input type="checkbox" checked={chainForm.transit_ids.includes(server.id)} onChange={(e) => setChainForm({ ...chainForm, transit_ids: e.target.checked ? [...chainForm.transit_ids, server.id] : chainForm.transit_ids.filter((id) => id !== server.id) })} />
                {server.name}{server.demo ? " демо" : ""}
              </label>
            ))}
            <label className="mt-3 block text-xs text-mist">Куда
              <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={chainForm.mode} onChange={(e) => setChainForm({ ...chainForm, mode: e.target.value as "exit" | "group" })}>
                <option value="exit">один выход</option>
                <option value="group">группа выходов</option>
              </select>
            </label>
            {chainForm.mode === "exit" ? (
              <select className="mt-2 w-full rounded-lg border border-line bg-ink px-3 py-2" value={chainForm.exit_id} onChange={(e) => setChainForm({ ...chainForm, exit_id: e.target.value })}>
                {exits.map((server) => <option key={server.id} value={server.id}>{server.name}</option>)}
              </select>
            ) : (
              <select className="mt-2 w-full rounded-lg border border-line bg-ink px-3 py-2" value={chainForm.group_id} onChange={(e) => setChainForm({ ...chainForm, group_id: e.target.value })}>
                {groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
              </select>
            )}
            <button className="mt-4 w-full rounded-xl bg-tide py-3" type="submit">Сохранить</button>
          </div>
        </form>
      )}
      {groupForm && (
        <form className="fixed inset-0 z-40 flex justify-end bg-black/50" onSubmit={saveGroup}>
          <div className="h-full w-full max-w-md overflow-y-auto bg-[#0b1524] p-5">
            <div className="mb-3 flex justify-between"><h2>Группа выходов</h2><button type="button" onClick={() => setGroupForm(null)}>закрыть</button></div>
            <label className="text-xs text-mist">Имя<input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={groupForm.name} onChange={(e) => setGroupForm({ ...groupForm, name: e.target.value })} required /></label>
            {exits.map((server) => {
              const member = groupForm.members.find((item) => item.server_id === server.id);
              return (
                <div key={server.id} className="mt-3 rounded-lg border border-line p-2 text-sm">
                  <label className="flex items-center gap-2">
                    <input type="checkbox" checked={Boolean(member)} onChange={(e) => setGroupForm({ ...groupForm, members: e.target.checked ? [...groupForm.members, { server_id: server.id, weight: 50, standby: false }] : groupForm.members.filter((item) => item.server_id !== server.id) })} />
                    {server.name}{server.demo ? " демо" : ""}
                  </label>
                  {member && (
                    <div className="mt-2 flex gap-2 text-xs">
                      <label>вес<input type="number" min={0} max={100} className="ml-1 w-16 rounded border border-line bg-ink px-1" value={member.weight} onChange={(e) => setGroupForm({ ...groupForm, members: groupForm.members.map((item) => item.server_id === server.id ? { ...item, weight: Number(e.target.value) } : item) })} /></label>
                      <label>резерв<input type="checkbox" className="ml-1" checked={member.standby} onChange={(e) => setGroupForm({ ...groupForm, members: groupForm.members.map((item) => item.server_id === server.id ? { ...item, standby: e.target.checked } : item) })} /></label>
                    </div>
                  )}
                </div>
              );
            })}
            <button className="mt-4 w-full rounded-xl bg-iris py-3" type="submit">Сохранить</button>
          </div>
        </form>
      )}
    </Page>
  );
}

function DeployLog({ lines }: { lines: string[] }) {
  const [notice, setNotice] = useState("");
  return (
    <div className="card mb-4 p-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="text-xs text-mist">Журнал развёртывания</span>
        <button
          className="text-xs text-teal"
          onClick={() => {
            void saveDownload("cascade-deploy.txt", lines.join("\n")).then((saved) => {
              if (saved) setNotice(`Сохранено: ${saved}`);
            });
          }}
        >
          Скачать
        </button>
      </div>
      {notice && <div className="mb-2 text-xs text-teal">{notice}</div>}
      <pre className="max-h-64 overflow-auto text-xs text-teal">{lines.join("\n")}</pre>
    </div>
  );
}
