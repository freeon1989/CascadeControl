import { FormEvent, useEffect, useState } from "react";
import { api, Chain, ExitGroup, formatHits, Rule, Server } from "../api";
import { SourceError } from "../sourceError";
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

const matchChoices = [
  ["domain", "Конкретный сайт", "example.com"],
  ["suffix", "Сайт и все поддомены", "google.com"],
  ["ip", "Один IP", "1.1.1.1"],
  ["cidr", "Сеть", "10.0.0.0/8"],
  ["geo", "Страна или гео-список", "geosite:category-ru"],
  ["list", "Готовый список", "antifilter-domains"],
  ["preset", "Пресет сервиса", "youtube"],
  ["any", "Всё, что не совпало выше", ""],
] as const;

const presetHelp: Record<string, string> = {
  "banks-gov": "Банки и Госуслуги открываются напрямую, без каскада.",
  youtube: "YouTube и связанные домены идут через каскад.",
  "google-ai": "Gemini и сервисы Google AI идут через каскад.",
  openai: "ChatGPT и сайты OpenAI идут через каскад.",
  telegram: "Telegram, его домены и сети идут через каскад.",
  ads: "Рекламные домены блокируются.",
};

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
type Tab = "rules" | "addresses" | "sources" | "detect";

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
  const [tab, setTab] = useState<Tab>("rules");
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
  const [step, setStep] = useState(1);
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

  function openNew() {
    setEditor({ ...emptyRule });
    setStep(1);
    setTab("rules");
  }

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
      name: editor.name || defaultName(editor),
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

  async function quickSetup() {
    if (!confirm("Создать базовые правила: российские сайты напрямую, всё остальное через каскад, и включить YouTube, ChatGPT, Gemini и Telegram?")) return;
    try {
      const result = await api<{ rules: string[]; presets: string[] }>("/api/routing/quick-setup", { method: "POST" });
      setMessage(result.rules.length ? "Быстрая настройка применена." : "Правила уже были. Пресеты YouTube, ChatGPT, Gemini и Telegram включены.");
      setTab("rules");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "ошибка");
    }
  }

  const exits = servers.filter((server) => server.role === "exit" || server.role === "standby_exit");
  const tabs: [Tab, string][] = [
    ["rules", "Правила"],
    ["addresses", "Списки адресов"],
    ["sources", "Источники"],
    ["detect", "Автоопределение"],
  ];

  return (
    <Page
      title="Маршрутизация"
      subtitle="Сначала решите, что куда идёт. Списки и проверка блокировок лежат на соседних вкладках."
      extra={
        <div className="flex flex-wrap gap-2">
          <button className="rounded-full border border-teal px-4 py-2 text-sm text-teal" onClick={() => void quickSetup()}>Быстрая настройка</button>
          <button className="rounded-full bg-tide px-4 py-2 text-sm font-semibold" onClick={openNew}>Добавить правило</button>
        </div>
      }
    >
      {error && <Banner text={error} />}
      {message && <Banner text={message} />}
      <div className="mb-4 flex flex-wrap gap-2">
        {tabs.map(([id, label]) => (
          <button key={id} className={`rounded-full px-4 py-2 text-sm ${tab === id ? "bg-white/10 text-foam" : "text-mist"}`} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>
      {!rules && <Banner text="Загрузка правил…" />}
      {rules && tab === "rules" && (
        <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
          <section className="card p-4">
            <Help title="Правила" text="Панель смотрит сверху вниз и берёт первое подходящее правило. «Всё остальное» лучше держать внизу." />
            <div className="mb-3 flex justify-end">
              <button className="rounded-lg border border-teal px-3 py-1 text-sm text-teal" onClick={openNew}>Добавить правило</button>
            </div>
            {rules.length === 0 && (
              <p className="mb-3 text-sm text-mist">Правил пока нет. «Быстрая настройка» создаёт российские сайты напрямую и остальной трафик через каскад. Своё правило можно добавить и без серверов.</p>
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
                  <button className="text-xs text-teal" onClick={() => { setEditor(ruleToForm(rule)); setStep(1); }}>изменить</button>
                  <button className="text-xs text-rose" onClick={() => void removeRule(rule)}>удалить</button>
                  <Switch on={rule.enabled} label="переключить правило" onClick={() => void api(`/api/rules/${rule.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !rule.enabled }) }).then(load)} />
                  <span className="w-16 text-right font-mono text-xs text-mist">{formatHits(rule.hits)}</span>
                </div>
              ))}
            </div>
          </section>
          <section className="card p-4">
            <Help title="Балансировка выходов" text="Если правило не указывает конкретный выход, каскад выбирает его по этой стратегии." />
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
          </section>
        </div>
      )}
      {rules && tab === "addresses" && (
        <AddressesTab addresses={addresses} presets={presets} onChange={load} onError={setError} />
      )}
      {rules && tab === "sources" && <SourcesTab sources={sources} onChange={load} onError={setError} />}
      {rules && tab === "detect" && (
        <DetectTab detect={detect} probeDomain={probeDomain} setProbeDomain={setProbeDomain} onChange={load} onError={setError} />
      )}
      {editor && (
        <form className="fixed inset-0 z-40 flex items-end justify-center bg-black/60 p-4 sm:items-center" onSubmit={saveRule}>
          <div className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-line bg-[#0b1524] p-5">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-lg">{editor.id ? "Изменить правило" : "Новое правило"}</h2>
              <button type="button" className="text-mist" onClick={() => setEditor(null)}>закрыть</button>
            </div>
            <p className="mb-4 text-xs text-mist">Шаг {step} из 2. {step === 1 ? "Что направить?" : "Куда отправить?"}</p>
            {step === 1 && (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-2 gap-2">
                  {matchChoices.map(([id, label]) => (
                    <button type="button" key={id} className={`rounded-lg border px-2 py-2 text-left text-sm ${editor.type === id ? "border-teal text-teal" : "border-line text-mist"}`} onClick={() => setEditor({ ...editor, type: id, value: id === "any" ? "" : editor.value })}>
                      {label}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-mist">Пример: {exampleFor(editor.type)}</p>
                {editor.type !== "any" && (
                  <label className="text-xs text-mist">Адрес или условие
                    <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2 text-foam" value={editor.value} onChange={(e) => setEditor({ ...editor, value: e.target.value })} placeholder={exampleFor(editor.type)} required />
                  </label>
                )}
                <label className="text-xs text-mist">Название, можно оставить пустым
                  <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2 text-foam" value={editor.name} onChange={(e) => setEditor({ ...editor, name: e.target.value })} placeholder={defaultName(editor)} />
                </label>
                <button type="button" className="rounded-xl bg-tide py-3" onClick={() => { if (editor.type !== "any" && !editor.value.trim()) { setError("Сначала укажите, что направить."); return; } setError(""); setStep(2); }}>Дальше: куда отправить</button>
              </div>
            )}
            {step === 2 && (
              <div className="flex flex-col gap-3">
                <p className="text-sm text-mist">Направляем: {editor.type === "any" ? "всё остальное" : editor.value}</p>
                {[
                  ["direct", "Напрямую", "Сайт открывается с входа, без каскада. Так обычно идут российские сайты и банки."],
                  ["cascade", "Через каскад", "Трафик идёт вход, транзит, выход. Цепочку можно выбрать позже."],
                  ["block", "Заблокировать", "Соединение обрывается на входе."],
                ].map(([id, title, hint]) => (
                  <button type="button" key={id} className={`rounded-xl border px-3 py-3 text-left ${editor.action === id ? "border-teal" : "border-line"}`} onClick={() => setEditor({ ...editor, action: id })}>
                    <div className="text-sm">{title}</div>
                    <div className="text-xs text-mist">{hint}</div>
                  </button>
                ))}
                <details className="rounded-xl border border-line px-3 py-2 text-sm">
                  <summary className="cursor-pointer text-mist">Указать конкретный каскад, группу или выход</summary>
                  <div className="mt-3 flex flex-col gap-2">
                    <button type="button" className={editor.action === "chain" ? "text-teal" : "text-mist"} onClick={() => setEditor({ ...editor, action: "chain" })}>Конкретный каскад</button>
                    <button type="button" className={editor.action === "group" ? "text-teal" : "text-mist"} onClick={() => setEditor({ ...editor, action: "group" })}>Группа выходов</button>
                    <button type="button" className={editor.action === "exit" ? "text-teal" : "text-mist"} onClick={() => setEditor({ ...editor, action: "exit" })}>Один выход</button>
                    {editor.action === "chain" && (
                      <select className="rounded-lg border border-line bg-ink px-3 py-2" value={editor.chain_id} onChange={(e) => setEditor({ ...editor, chain_id: e.target.value })}>
                        <option value="">выбрать позже</option>
                        {chains.map((chain) => <option key={chain.id} value={chain.id}>{chain.name}</option>)}
                      </select>
                    )}
                    {editor.action === "group" && (
                      <select className="rounded-lg border border-line bg-ink px-3 py-2" value={editor.group_id} onChange={(e) => setEditor({ ...editor, group_id: e.target.value })}>
                        <option value="">выбрать позже</option>
                        {groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}
                      </select>
                    )}
                    {editor.action === "exit" && (
                      <select className="rounded-lg border border-line bg-ink px-3 py-2" value={editor.exit_id} onChange={(e) => setEditor({ ...editor, exit_id: e.target.value })}>
                        <option value="">выбрать позже</option>
                        {exits.map((exit) => <option key={exit.id} value={exit.id}>{exit.name}</option>)}
                      </select>
                    )}
                  </div>
                </details>
                <div className="flex gap-2">
                  <button type="button" className="rounded-xl border border-line px-4 py-3" onClick={() => setStep(1)}>Назад</button>
                  <button className="flex-1 rounded-xl bg-tide py-3" type="submit">Сохранить</button>
                </div>
              </div>
            )}
          </div>
        </form>
      )}
    </Page>
  );
}

function Help({ title, text }: { title: string; text: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mt-1 text-xs text-mist">{text}</p>
    </div>
  );
}

function Switch({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <button type="button" className={`h-6 w-11 shrink-0 rounded-full ${on ? "bg-teal" : "bg-white/10"}`} onClick={onClick} aria-label={label} aria-pressed={on}>
      <span className={`block h-5 w-5 rounded-full bg-white transition ${on ? "translate-x-5" : "translate-x-0.5"}`} />
    </button>
  );
}

function AddressesTab({ addresses, presets, onChange, onError }: { addresses: Address[]; presets: Preset[]; onChange: () => Promise<void>; onError: (value: string) => void }) {
  const [value, setValue] = useState("");
  const [kind, setKind] = useState("domain");

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

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="card p-4">
        <Help title="Списки адресов" text="Здесь адреса из источников, пресетов и ручного ввода. Закреплённый адрес обновление не затирает. Исключённый больше не добавляется." />
        <form className="mb-3 flex flex-wrap gap-2" onSubmit={addAddress}>
          <select className="rounded-lg border border-line bg-ink px-2 py-2 text-sm" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="domain">Домен</option>
            <option value="cidr">Сеть</option>
          </select>
          <input className="min-w-[180px] flex-1 rounded-lg border border-line bg-ink px-3 py-2 text-sm" value={value} onChange={(e) => setValue(e.target.value)} placeholder="example.com или 10.0.0.0/8" required />
          <button className="rounded-lg bg-tide px-3 py-2 text-sm">Добавить адрес</button>
        </form>
        {addresses.length === 0 && <p className="text-sm text-mist">Пока пусто. Включите пресет, обновите источник или добавьте адрес вручную.</p>}
        <div className="flex max-h-[480px] flex-col gap-2 overflow-auto">
          {addresses.slice(0, 40).map((row) => (
            <div key={row.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-xs">
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
      </section>
      <section className="card p-4">
        <Help title="Пресеты сервисов" text="Переключатель сразу добавляет домены сервиса в список. Выключить можно в любой момент, закреплённые адреса останутся." />
        <div className="flex flex-col gap-3">
          {presets.map((preset) => (
            <div key={preset.id} className="flex items-start justify-between gap-3 rounded-xl border border-line px-3 py-3">
              <div>
                <div className="text-sm">{preset.title}</div>
                <div className="text-xs text-mist">{presetHelp[preset.id] || `${preset.domains} доменов, ${preset.cidrs} сетей`}</div>
              </div>
              <Switch on={Boolean(preset.enabled)} label={preset.title} onClick={() => void api(`/api/presets/${preset.id}/enabled`, { method: "POST", body: JSON.stringify({ enabled: !preset.enabled }) }).then(onChange)} />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function SourcesTab({ sources, onChange, onError }: { sources: Source[]; onChange: () => Promise<void>; onError: (value: string) => void }) {
  const [draft, setDraft] = useState({ title: "", url: "", kind: "domain", interval_hours: 24, selector: "" });

  async function addSource(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/api/sources", { method: "POST", body: JSON.stringify({ ...draft, action: "cascade", enabled: true }) });
      setDraft({ title: "", url: "", kind: "domain", interval_hours: 24, selector: "" });
      await onChange();
    } catch (err) {
      onError(err instanceof Error ? err.message : "ошибка");
    }
  }

  return (
    <section className="card p-4">
      <Help title="Источники списков" text="Панель сама скачивает список по расписанию. Имя достаточно понятное, внутренний номер создаётся сам. Тот же список виден в «Настройках»." />
      <form className="grid gap-2 md:grid-cols-2" onSubmit={addSource}>
        <label className="text-xs text-mist">Название
          <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="Например, свой список доменов" required />
        </label>
        <label className="text-xs text-mist">Ссылка
          <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} placeholder="https://example.com/list.txt" required />
        </label>
        <label className="text-xs text-mist">Что в файле
          <select className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
            <option value="domain">Список доменов</option>
            <option value="cidr">Список сетей</option>
            <option value="geosite-dat">Файл geosite.dat</option>
          </select>
        </label>
        <label className="text-xs text-mist">Обновлять каждые, часов
          <input type="number" min={1} className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={draft.interval_hours} onChange={(e) => setDraft({ ...draft, interval_hours: Number(e.target.value) })} />
        </label>
        {draft.kind === "geosite-dat" && (
          <label className="text-xs text-mist md:col-span-2">Категория внутри geosite.dat
            <input className="mt-1 w-full rounded-lg border border-line bg-ink px-3 py-2" value={draft.selector} onChange={(e) => setDraft({ ...draft, selector: e.target.value })} placeholder="RU" />
          </label>
        )}
        <button className="rounded-lg bg-tide py-2 text-sm md:col-span-2">Добавить источник</button>
      </form>
      <div className="mt-4 flex flex-col gap-2">
        {sources.map((source) => (
          <div key={source.id} className="rounded-xl border border-line p-3 text-sm">
            <div className="font-medium">{source.title}</div>
            <div className="break-all text-xs text-mist">{source.url}</div>
            <div className="text-xs text-mist">каждые {source.interval_hours} ч · записей {source.last_count} · {source.last_fetch || "ещё не обновлялся"}</div>
            {source.last_error && <SourceError raw={source.last_error} />}
            <div className="mt-2 flex gap-3 text-xs">
              <button className="text-teal" onClick={() => void api(`/api/sources/${source.id}/refresh`, { method: "POST" }).then(onChange).catch((err: Error) => onError(err.message))}>Обновить сейчас</button>
              <button className="text-rose" onClick={() => { if (confirm(`Удалить источник ${source.title}?`)) void api(`/api/sources/${source.id}`, { method: "DELETE" }).then(onChange); }}>удалить</button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function DetectTab({ detect, probeDomain, setProbeDomain, onChange, onError }: { detect: Detect; probeDomain: string; setProbeDomain: (value: string) => void; onChange: () => Promise<void>; onError: (value: string) => void }) {
  return (
    <section className="card max-w-2xl p-4">
      <Help title="Автоопределение блокировок" text="Вход сам проверяет домен. Если сайт не открывается, его адрес помечается как каскад. Проверка ниже ничего не включает, она только показывает результат." />
      <div className="flex items-center justify-between gap-3 rounded-xl border border-line px-3 py-3">
        <div>
          <div className="text-sm">Помечать недоступные домены как каскад</div>
          <div className="text-xs text-mist">Статус: {detect.status}{detect.last_run ? `, ${detect.last_run}` : ""}</div>
        </div>
        <Switch on={detect.enabled} label="автоопределение" onClick={() => void api("/api/detect", { method: "PUT", body: JSON.stringify({ enabled: !detect.enabled }) }).then(onChange)} />
      </div>
      <form className="mt-4 flex gap-2" onSubmit={(event) => { event.preventDefault(); void api("/api/detect/probe", { method: "POST", body: JSON.stringify({ domain: probeDomain }) }).then(onChange).catch((err: Error) => onError(err.message)); }}>
        <input className="flex-1 rounded-lg border border-line bg-ink px-3 py-2 text-sm" value={probeDomain} onChange={(e) => setProbeDomain(e.target.value)} placeholder="домен для проверки, например youtube.com" />
        <button className="rounded-lg border border-line px-3 text-sm">Проверить</button>
      </form>
    </section>
  );
}

function exampleFor(type: string): string {
  return matchChoices.find((item) => item[0] === type)?.[2] || "example.com";
}

function defaultName(editor: typeof emptyRule): string {
  if (editor.type === "any") return "Всё остальное";
  if (editor.value) return editor.value;
  return "Правило";
}

function labelMatch(match: Record<string, unknown>): string {
  const type = typeof match.type === "string" ? match.type : "";
  const label = matchChoices.find((item) => item[0] === type)?.[1];
  if (label) return `${label}: ${match.value || "всё"}`;
  if (match.any === true) return "всё";
  if (typeof match.preset === "string") return `пресет ${match.preset}`;
  return "условие";
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
