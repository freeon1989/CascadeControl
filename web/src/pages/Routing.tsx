import { FormEvent, useEffect, useState } from "react";
import { api, Chain, ExitGroup, formatHits, Rule, Server } from "../api";
import { Banner, Page } from "./Overview";

const actionStyle: Record<string, string> = {
  direct: "bg-amber/20 text-amber",
  cascade: "bg-teal/15 text-teal",
  chain: "bg-teal/15 text-teal",
  group: "bg-iris/20 text-iris",
  exit: "bg-iris/20 text-iris",
  block: "bg-rose/20 text-rose",
};

const actionText: Record<string, string> = {
  direct: "Напрямую",
  cascade: "Через каскад",
  chain: "Каскад",
  group: "Группа выходов",
  exit: "Выход",
  block: "Блокировать",
};

const matchTypes = [
  ["domain", "Домен"],
  ["suffix", "Суффикс домена"],
  ["ip", "IP"],
  ["cidr", "CIDR"],
  ["list", "Список адресов"],
  ["preset", "Пресет сервиса"],
  ["geo", "Гео"],
  ["any", "Всё остальное"],
];

type Address = {
  id: string;
  kind: string;
  value: string;
  action: string;
  source: string;
  source_date: string;
  pinned: boolean;
  excluded: boolean;
};

type Source = {
  id: string;
  title: string;
  url: string;
  kind: string;
  action: string;
  enabled: boolean;
  interval_hours: number;
  selector: string;
  last_fetch: string;
  last_count: number;
  last_error: string;
};

type Preset = { id: string; title: string; enabled?: boolean; domains: number; cidrs: number };
type Detect = { enabled: boolean; status: string; last_run: string };

const emptyRule = {
  id: "",
  name: "",
  type: "domain",
  value: "",
  action: "cascade",
  exit_id: "",
  chain_id: "",
  group_id: "",
};

export function RoutingPage() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [servers, setServers] = useState<Server[]>([]);
  const [chains, setChains] = useState<Chain[]>([]);
  const [groups, setGroups] = useState<ExitGroup[]>([]);
  const [balance, setBalance] = useState({ strategy: "least_ping", auto_failover: true, interval_sec: 30, weights: {} as Record<string, number> });
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [detect, setDetect] = useState<Detect>({ enabled: false, status: "выключено", last_run: "" });
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [editor, setEditor] = useState<typeof emptyRule | null>(null);
  const [probeDomain, setProbeDomain] = useState("");

  async function load() {
    try {
      const [ruleRows, serverRows, balanceRow, addressRows, chainRows, groupRows, sourceRows, settings] = await Promise.all([
        api<Rule[]>("/api/rules"),
        api<Server[]>("/api/servers"),
        api<typeof balance>("/api/balance"),
        api<Address[]>("/api/addresses"),
        api<Chain[]>("/api/chains"),
        api<ExitGroup[]>("/api/groups"),
        api<Source[]>("/api/sources"),
        api<{ presets: Preset[]; detect: Detect }>("/api/settings"),
      ]);
      setRules(ruleRows);
      setServers(serverRows);
      setBalance(balanceRow);
      setAddresses(addressRows);
      setChains(chainRows);
      setGroups(groupRows);
      setSources(sourceRows);
      setPresets(settings.presets);
      setDetect(settings.detect);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function move(rule: Rule, direction: -1 | 1) {
    if (!rules) return;
    const ids = rules.map((item) => item.id);
    const index = ids.indexOf(rule.id);
    const next = index + direction;
    if (next < 0 || next >= ids.length) return;
    [ids[index], ids[next]] = [ids[next], ids[index]];
    setRules(await api<Rule[]>("/api/rules/order", { method: "PUT", body: JSON.stringify({ ids }) }));
  }

  async function removeRule(rule: Rule) {
    if (!confirm(`Удалить правило «${rule.name}»?`)) return;
    await api(`/api/rules/${rule.id}`, { method: "DELETE" });
    await load();
  }

  async function saveRule(event: FormEvent) {
    event.preventDefault();
    if (!editor) return;
    const body = {
      name: editor.name || "Правило",
      match: { type: editor.type, value: editor.type === "any" ? "" : editor.value },
      action: editor.action,
      exit_id: editor.action === "exit" ? editor.exit_id || null : null,
      chain_id: editor.action === "chain" ? editor.chain_id || null : null,
      group_id: editor.action === "group" ? editor.group_id || null : null,
      enabled: true,
    };
    try {
      if (editor.id) {
        await api(`/api/rules/${editor.id}`, { method: "PATCH", body: JSON.stringify(body) });
      } else {
        await api("/api/rules", { method: "POST", body: JSON.stringify(body) });
      }
      setEditor(null);
      setMessage(editor.action === "cascade" && !editor.chain_id ? "Правило сохранено. Каскад можно выбрать позже на странице «Каскад»." : "Правило сохранено.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  const exits = servers.filter((server) => server.role === "exit" || server.role === "standby_exit");

  return (
    <Page
      title="Правила маршрутизации на входе"
      subtitle="Правила применяются по порядку. Первое совпадение определяет маршрут."
      extra={<button className="rounded-full bg-tide px-4 py-2 text-sm font-semibold" onClick={() => setEditor({ ...emptyRule })}>Добавить правило</button>}
    >
      {error && <Banner text={error} />}
      {message && <Banner text={message} />}
      {!rules && <Banner text="Загрузка правил…" />}
      {rules && (
        <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
          <div className="card p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-sm text-mist">Правила маршрутизации</div>
              <button className="rounded-lg border border-teal px-3 py-1 text-sm text-teal" onClick={() => setEditor({ ...emptyRule })}>Добавить правило</button>
            </div>
            {rules.length === 0 && (
              <p className="mb-3 text-sm text-mist">Правил пока нет. Кнопка «Добавить правило» создаёт маршрут и без серверов: действие «Через каскад» можно привязать к цепочке позже.</p>
            )}
            <div className="flex flex-col gap-2">
              {rules.map((rule, index) => (
                <div key={rule.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-black/20 px-3 py-3">
                  <span className="w-5 text-sm text-mist">{index + 1}</span>
                  <span className="min-w-[140px] flex-1 text-sm">{rule.name}</span>
                  <span className="text-xs text-mist">{labelMatch(rule.match)}</span>
                  <span className={`rounded-full px-3 py-1 text-xs ${actionStyle[rule.action] || ""}`}>{actionText[rule.action] || rule.action}</span>
                  <button className="text-xs text-mist" onClick={() => void move(rule, -1)}>вверх</button>
                  <button className="text-xs text-mist" onClick={() => void move(rule, 1)}>вниз</button>
                  <button className="text-xs text-teal" onClick={() => setEditor(ruleToForm(rule))}>изменить</button>
                  <button className="text-xs text-rose" onClick={() => void removeRule(rule)}>удалить</button>
                  <button className={`h-6 w-11 rounded-full ${rule.enabled ? "bg-teal" : "bg-white/10"}`} onClick={() => void api(`/api/rules/${rule.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !rule.enabled }) }).then(load)} aria-label="переключить правило">
                    <span className={`block h-5 w-5 rounded-full bg-white transition ${rule.enabled ? "translate-x-5" : "translate-x-0.5"}`} />
                  </button>
                  <span className="w-16 text-right font-mono text-xs text-mist">{formatHits(rule.hits)}</span>
                </div>
              ))}
            </div>
            <AddressBlock
              addresses={addresses}
              sources={sources}
              presets={presets}
              detect={detect}
              probeDomain={probeDomain}
              setProbeDomain={setProbeDomain}
              onChange={load}
              onError={setError}
            />
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
                <label key={exit.id} className="text-xs text-mist">{exit.name} {exit.role === "standby_exit" ? "резерв" : ""}
                  <input type="range" min={0} max={100} value={balance.weights[exit.id] ?? (exit.role === "standby_exit" ? 0 : 50)} onChange={(e) => setBalance({ ...balance, weights: { ...balance.weights, [exit.id]: Number(e.target.value) } })} className="mt-1 w-full" />
                </label>
              ))}
              {exits.length === 0 && <div className="text-xs text-mist">Выходы появятся после добавления серверов. Правило «через каскад» можно создать и раньше.</div>}
            </div>
            <label className="mt-4 flex items-center justify-between text-sm">
              Автопереключение при падении
              <input type="checkbox" checked={balance.auto_failover} onChange={(e) => setBalance({ ...balance, auto_failover: e.target.checked })} />
            </label>
            <label className="mt-3 block text-xs text-mist">Интервал проверки, с
              <input type="number" min={5} className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={balance.interval_sec} onChange={(e) => setBalance({ ...balance, interval_sec: Number(e.target.value) })} />
            </label>
            <button className="mt-4 w-full rounded-xl bg-tide py-3 font-semibold" onClick={() => void api("/api/balance", { method: "PUT", body: JSON.stringify(balance) }).then(() => setMessage("Балансировка сохранена."))}>Применить</button>
          </div>
        </div>
      )}
      {editor && (
        <form className="fixed inset-0 z-40 flex justify-end bg-black/50" onSubmit={saveRule}>
          <div className="h-full w-full max-w-md overflow-y-auto bg-[#0b1524] p-5">
            <div className="mb-4 flex justify-between"><h2 className="text-lg">{editor.id ? "Изменить правило" : "Новое правило"}</h2><button type="button" onClick={() => setEditor(null)}>закрыть</button></div>
            <label className="text-xs text-mist">Название
              <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={editor.name} onChange={(e) => setEditor({ ...editor, name: e.target.value })} required />
            </label>
            <label className="mt-3 block text-xs text-mist">Тип совпадения
              <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={editor.type} onChange={(e) => setEditor({ ...editor, type: e.target.value })}>
                {matchTypes.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
            </label>
            {editor.type !== "any" && (
              <label className="mt-3 block text-xs text-mist">Значение
                <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={editor.value} onChange={(e) => setEditor({ ...editor, value: e.target.value })} placeholder={placeholder(editor.type)} required />
              </label>
            )}
            <label className="mt-3 block text-xs text-mist">Действие
              <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={editor.action} onChange={(e) => setEditor({ ...editor, action: e.target.value })}>
                <option value="direct">Напрямую</option>
                <option value="cascade">Через каскад (цепочку можно выбрать позже)</option>
                <option value="chain">Конкретный каскад</option>
                <option value="group">Группа выходов</option>
                <option value="exit">Один выход</option>
                <option value="block">Блокировать</option>
              </select>
            </label>
            {editor.action === "chain" && (
              <label className="mt-3 block text-xs text-mist">Каскад
                <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={editor.chain_id} onChange={(e) => setEditor({ ...editor, chain_id: e.target.value })}>
                  <option value="">выбрать позже</option>
                  {chains.map((chain) => <option key={chain.id} value={chain.id}>{chain.name}</option>)}
                </select>
              </label>
            )}
            {editor.action === "group" && (
              <label className="mt-3 block text-xs text-mist">Группа
                <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={editor.group_id} onChange={(e) => setEditor({ ...editor, group_id: e.target.value })}>
                  <option value="">выбрать позже</option>
                  {groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
                </select>
              </label>
            )}
            {editor.action === "exit" && (
              <label className="mt-3 block text-xs text-mist">Выход
                <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={editor.exit_id} onChange={(e) => setEditor({ ...editor, exit_id: e.target.value })}>
                  <option value="">выбрать позже</option>
                  {exits.map((exit) => <option key={exit.id} value={exit.id}>{exit.name}</option>)}
                </select>
              </label>
            )}
            <button className="mt-4 w-full rounded-xl bg-tide py-3" type="submit">Сохранить</button>
          </div>
        </form>
      )}
    </Page>
  );
}

function labelMatch(match: Record<string, unknown>): string {
  if (typeof match.type === "string") return `${match.type}: ${match.value || "всё"}`;
  if (match.any === true) return "всё";
  if (typeof match.preset === "string") return `пресет ${match.preset}`;
  return "условие";
}

function placeholder(type: string): string {
  if (type === "geo") return "geosite:ru или geoip:ru";
  if (type === "cidr") return "10.0.0.0/8";
  if (type === "list") return "идентификатор источника";
  if (type === "preset") return "youtube";
  return "example.com";
}

function ruleToForm(rule: Rule): typeof emptyRule {
  const match = rule.match;
  return {
    id: rule.id,
    name: rule.name,
    type: typeof match.type === "string" ? match.type : match.any ? "any" : "domain",
    value: typeof match.value === "string" ? match.value : "",
    action: rule.action,
    exit_id: rule.exit_id || "",
    chain_id: rule.chain_id || "",
    group_id: rule.group_id || "",
  };
}

function AddressBlock({
  addresses, sources, presets, detect, probeDomain, setProbeDomain, onChange, onError,
}: {
  addresses: Address[];
  sources: Source[];
  presets: Preset[];
  detect: Detect;
  probeDomain: string;
  setProbeDomain: (value: string) => void;
  onChange: () => Promise<void>;
  onError: (value: string) => void;
}) {
  const [value, setValue] = useState("");
  const [kind, setKind] = useState("domain");
  const [draft, setDraft] = useState({ id: "", title: "", url: "", kind: "domain", interval_hours: 24, selector: "" });

  async function addAddress(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/api/addresses", { method: "POST", body: JSON.stringify({ kind, value, action: "cascade" }) });
      setValue("");
      await onChange();
    } catch (err) {
      onError(err instanceof Error ? err.message : "ошибка");
    }
  }

  async function addSource(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/api/sources", { method: "POST", body: JSON.stringify({ ...draft, action: "cascade", enabled: true }) });
      setDraft({ id: "", title: "", url: "", kind: "domain", interval_hours: 24, selector: "" });
      await onChange();
    } catch (err) {
      onError(err instanceof Error ? err.message : "ошибка");
    }
  }

  return (
    <div className="mt-6 border-t border-line pt-4">
      <div className="mb-2 text-sm">Автодобавленные адреса</div>
      <form className="mb-3 flex flex-wrap gap-2" onSubmit={addAddress}>
        <select className="rounded-lg border border-line bg-ink px-2 py-1 text-sm" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="domain">Домен</option>
          <option value="cidr">CIDR</option>
        </select>
        <input className="min-w-[180px] flex-1 rounded-lg border border-line bg-ink px-2 py-1 text-sm" value={value} onChange={(e) => setValue(e.target.value)} placeholder="домен или сеть" required />
        <button className="rounded-lg bg-tide px-3 py-1 text-sm">Добавить адрес</button>
      </form>
      {addresses.length === 0 && <p className="mb-3 text-xs text-mist">Пока пусто. Включите пресет, обновите источник или добавьте адрес вручную.</p>}
      <div className="flex flex-col gap-1">
        {addresses.slice(0, 12).map((row) => (
          <div key={row.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className={row.excluded ? "text-mist line-through" : ""}>{row.value}</span>
            <span className="text-mist">{row.source} · {row.source_date.slice(0, 10)}</span>
            <span className="flex gap-2">
              <button className="text-teal" onClick={() => void api(`/api/addresses/${row.id}/pin`, { method: "POST" }).then(onChange)}>{row.pinned ? "открепить" : "закрепить"}</button>
              <button className="text-amber" onClick={() => void api(`/api/addresses/${row.id}/exclude`, { method: "POST" }).then(onChange)}>{row.excluded ? "вернуть" : "исключить"}</button>
              <button className="text-rose" onClick={() => { if (confirm(`Удалить ${row.value}?`)) void api(`/api/addresses/${row.id}`, { method: "DELETE" }).then(onChange); }}>удалить</button>
            </span>
          </div>
        ))}
      </div>
      <div className="mt-4 text-sm">Автоопределение блокировок</div>
      <label className="mt-1 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={detect.enabled} onChange={(e) => void api("/api/detect", { method: "PUT", body: JSON.stringify({ enabled: e.target.checked }) }).then(onChange)} />
        на входе помечать недоступные домены как каскад
      </label>
      <p className="text-xs text-mist">Статус: {detect.status}{detect.last_run ? `, ${detect.last_run}` : ""}</p>
      <form className="mt-2 flex gap-2" onSubmit={(event) => { event.preventDefault(); void api("/api/detect/probe", { method: "POST", body: JSON.stringify({ domain: probeDomain }) }).then(onChange).catch((err: Error) => onError(err.message)); }}>
        <input className="flex-1 rounded-lg border border-line bg-ink px-2 py-1 text-sm" value={probeDomain} onChange={(e) => setProbeDomain(e.target.value)} placeholder="домен для проверки" />
        <button className="rounded-lg border border-line px-2 text-sm">Проверить</button>
      </form>
      <div className="mt-4 text-sm">Пресеты сервисов</div>
      <div className="mt-2 flex flex-wrap gap-2">
        {presets.map((preset) => (
          <button key={preset.id} className={`rounded-full px-3 py-1 text-xs ${preset.enabled ? "bg-teal text-ink" : "border border-line text-mist"}`} onClick={() => void api(`/api/presets/${preset.id}/enabled`, { method: "POST", body: JSON.stringify({ enabled: !preset.enabled }) }).then(onChange)}>
            {preset.title}: {preset.enabled ? "вкл" : "выкл"}
          </button>
        ))}
      </div>
      <div className="mt-4 text-sm">Источники списков</div>
      <form className="mt-2 grid gap-2 text-xs" onSubmit={addSource}>
        <input className="rounded-lg border border-line bg-ink px-2 py-1" placeholder="id" value={draft.id} onChange={(e) => setDraft({ ...draft, id: e.target.value })} required />
        <input className="rounded-lg border border-line bg-ink px-2 py-1" placeholder="название" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} required />
        <input className="rounded-lg border border-line bg-ink px-2 py-1" placeholder="https://..." value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} required />
        <div className="flex gap-2">
          <select className="rounded-lg border border-line bg-ink px-2 py-1" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
            <option value="domain">домены</option>
            <option value="cidr">сети</option>
            <option value="geosite-dat">geosite.dat</option>
          </select>
          <input type="number" min={1} className="w-24 rounded-lg border border-line bg-ink px-2 py-1" value={draft.interval_hours} onChange={(e) => setDraft({ ...draft, interval_hours: Number(e.target.value) })} />
          <span className="self-center text-mist">часов</span>
        </div>
        {draft.kind === "geosite-dat" && <input className="rounded-lg border border-line bg-ink px-2 py-1" placeholder="категория, например RU" value={draft.selector} onChange={(e) => setDraft({ ...draft, selector: e.target.value })} />}
        <button className="rounded-lg border border-teal py-1 text-teal">Добавить источник</button>
      </form>
      <div className="mt-2 flex flex-col gap-2">
        {sources.map((source) => (
          <div key={source.id} className="rounded-lg border border-line p-2 text-xs">
            <div className="font-medium">{source.title}</div>
            <div className="break-all text-mist">{source.url}</div>
            <div className="text-mist">каждые {source.interval_hours} ч · записей {source.last_count} · {source.last_fetch || "ещё не обновлялся"}</div>
            {source.last_error && <div className="text-amber">{source.last_error}</div>}
            <div className="mt-1 flex gap-3">
              <button className="text-teal" onClick={() => void api(`/api/sources/${source.id}/refresh`, { method: "POST" }).then(onChange).catch((err: Error) => onError(err.message))}>Обновить сейчас</button>
              <button className="text-rose" onClick={() => { if (confirm(`Удалить источник ${source.title}?`)) void api(`/api/sources/${source.id}`, { method: "DELETE" }).then(onChange); }}>удалить</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
