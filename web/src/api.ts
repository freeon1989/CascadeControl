export class ApiError extends Error {
  constructor(message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      /* текст ошибки уже в statusText */
    }
    throw new ApiError(typeof detail === "string" ? detail : "запрос не выполнен");
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export type Server = {
  id: string;
  name: string;
  host: string;
  ssh_port: number;
  ssh_user: string;
  auth_type: string;
  role: string;
  country: string;
  status: string;
  demo: boolean;
  awg_port: number | null;
  reality_port: number | null;
  link_protocol: string;
  protocols: string[];
  hysteria2: { enabled: boolean; reason: string };
  cpu: number | null;
  ram: string | null;
  ping_ms: number | null;
  share: number | null;
  speed_mbps: number | null;
  spark: number[];
  config_dirty: boolean;
  last_error: string;
  precheck: { ok: boolean; checks: Check[] } | null;
};

export type Check = { id: string; title: string; ok: boolean; level: string; detail: string };

export type Client = {
  id: string;
  name: string;
  kind: string;
  protocol: string;
  online: boolean;
  total_gb: number;
  total_bytes: number;
  last_seen: string;
  traffic: number[];
  entry: string;
  address: string;
};

export type ExportBundle = {
  conf: string;
  vpn_uri: string;
  vless_uri: string;
  subscription: string;
  subscription_url: string;
  qr_conf: string;
  qr_vpn: string;
  qr_vless: string;
  note: string;
  protocol: string;
};

export type Rule = {
  id: string;
  position: number;
  name: string;
  match: Record<string, unknown>;
  action: string;
  exit_id: string | null;
  chain_id: string | null;
  group_id: string | null;
  enabled: boolean;
  hits: number;
};

export type GroupMember = {
  server_id: string;
  name: string;
  role: string;
  demo: boolean;
  weight: number;
  standby: boolean;
};

export type ExitGroup = { id: string; name: string; members: GroupMember[] };

export type ChainNode = { id: string; name: string; role: string; demo: boolean } | null;

export type Chain = {
  id: string;
  name: string;
  entry_id: string;
  entry: ChainNode;
  transit_ids: string[];
  transits: ChainNode[];
  exit_id: string | null;
  exit: ChainNode;
  group_id: string | null;
};

export type Overview = {
  entry: Server | null;
  standby_entry: Server | null;
  transit: Server | null;
  exits: Server[];
  clients_online: number;
  clients_total: number;
  by_kind: Record<string, { total: number; online: number }>;
  traffic_gb: number;
  ping_ms: number | null;
  nodes_up: number;
  nodes_total: number;
  direct_share: number;
  cascade_share: number;
  speed_mbps: number;
  demo: boolean;
  sparks: { clients: number[]; traffic: number[]; ping: number[]; nodes: number[] };
};

export const roleLabel: Record<string, string> = {
  entry: "Вход",
  standby_entry: "Запасной вход",
  transit: "Транзит",
  exit: "Выход",
  standby_exit: "Резерв",
};

export const flag: Record<string, string> = {
  RU: "🇷🇺",
  TR: "🇹🇷",
  DE: "🇩🇪",
  NL: "🇳🇱",
  FI: "🇫🇮",
};

export function formatHits(value: number): string {
  return new Intl.NumberFormat("ru-RU").format(value);
}
