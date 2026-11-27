import { heroineSrc } from "../heroine";

export type FlowNode = {
  id: string;
  name: string;
  role: string;
  country?: string;
  status?: string;
  kind?: string;
  heroine?: string;
  protocols?: string[];
  ports?: { name: string; port: number; proto: string }[];
  label?: string;
  warp?: { enabled: boolean; mode: string; connected: boolean; egress_ip: string; status: string } | null;
};

export type FlowEdge = {
  from: string;
  to: string;
  kind: string;
  active: boolean;
  label: string;
  style?: string;
};

export type FlowData = {
  clients: FlowNode[];
  entry: FlowNode | null;
  standby_entry: FlowNode | null;
  transit: FlowNode | null;
  exits: FlowNode[];
  specials: FlowNode[];
  edges: FlowEdge[];
};

type Box = { id: string; x: number; y: number; w: number; h: number; node: FlowNode };

function tone(kind?: string, status?: string): string {
  if (kind === "direct") return "ring-ok/60";
  if (kind === "warp") return "ring-amber/60";
  if (kind === "down" || status === "offline" || status === "error") return "ring-rose/70";
  if (kind === "standby" || status === "unknown") return "ring-amber/40";
  if (status === "online" || kind === "active") return "ring-teal/60";
  return "ring-line";
}

export function FlowDiagram({ data, onSelect }: { data: FlowData; onSelect?: (id: string) => void }) {
  const nodes: FlowNode[] = [
    ...data.clients,
    data.entry,
    data.standby_entry,
    data.transit,
    ...data.exits,
    ...data.specials,
  ].filter(Boolean) as FlowNode[];
  const byId = new Map(nodes.map((item) => [item.id, item]));

  const col = { clients: 40, entry: 280, mid: 520, exits: 760, end: 1040 };
  const boxes: Box[] = [];
  const clientH = 64;
  data.clients.forEach((item, index) => {
    boxes.push({ id: item.id, x: col.clients, y: 80 + index * (clientH + 12), w: 180, h: clientH, node: item });
  });
  if (data.entry) {
    const y = Math.max(160, (data.clients.length * 76) / 2 + 40);
    boxes.push({ id: data.entry.id, x: col.entry, y, w: 200, h: 150, node: data.entry });
    if (data.standby_entry) {
      boxes.push({ id: data.standby_entry.id, x: col.entry, y: y + 170, w: 200, h: 110, node: data.standby_entry });
    }
  }
  const ru = data.specials.find((item) => item.id === "direct-ru");
  const inetDirect = data.specials.find((item) => item.id === "internet-direct");
  if (ru) boxes.push({ id: ru.id, x: col.mid, y: 24, w: 200, h: 100, node: ru });
  if (inetDirect) boxes.push({ id: inetDirect.id, x: col.end, y: 28, w: 180, h: 90, node: inetDirect });
  if (data.transit) boxes.push({ id: data.transit.id, x: col.mid, y: 240, w: 200, h: 140, node: data.transit });
  data.exits.forEach((item, index) => {
    boxes.push({ id: item.id, x: col.exits, y: 200 + index * 170, w: 210, h: 150, node: item });
  });
  data.specials
    .filter((item) => item.id.startsWith("warp-") || item.id === "internet")
    .forEach((item, index) => {
      const y = item.id === "internet" ? 240 + Math.max(data.exits.length, 1) * 80 : 200 + index * 170;
      boxes.push({ id: item.id, x: col.end, y, w: 190, h: item.id === "internet" ? 90 : 120, node: item });
    });

  const pos = new Map(boxes.map((box) => [box.id, box]));
  const height = Math.max(420, ...boxes.map((box) => box.y + box.h + 48));
  const width = 1260;

  function port(box: Box, side: "in" | "out", edge?: FlowEdge) {
    if (edge?.kind === "direct" && side === "out" && box.node.role === "entry") {
      return { x: box.x + box.w * 0.7, y: box.y };
    }
    if (edge?.kind === "direct" && side === "in" && box.node.role === "direct") {
      return { x: box.x, y: box.y + box.h * 0.7 };
    }
    return { x: side === "in" ? box.x : box.x + box.w, y: box.y + box.h / 2 };
  }

  function path(edge: FlowEdge): string {
    const a = pos.get(edge.from);
    const b = pos.get(edge.to);
    if (!a || !b) return "";
    const s = port(a, "out", edge);
    const e = port(b, "in", edge);
    const mid = (s.x + e.x) / 2;
    return `M ${s.x} ${s.y} C ${mid} ${s.y}, ${mid} ${e.y}, ${e.x} ${e.y}`;
  }

  function labelPoint(edge: FlowEdge): { x: number; y: number } {
    const a = pos.get(edge.from);
    const b = pos.get(edge.to);
    if (!a || !b) return { x: 0, y: 0 };
    const s = port(a, "out", edge);
    const e = port(b, "in", edge);
    let x = (s.x + e.x) / 2;
    let y = (s.y + e.y) / 2 - 10;
    const hits = (box: Box, px: number, py: number, pad = 10) =>
      px > box.x - pad && px < box.x + box.w + pad && py > box.y - pad && py < box.y + box.h + pad;
    if ([...pos.values()].some((box) => hits(box, x, y))) {
      x = (a.x + a.w + b.x) / 2;
      const above = Math.min(a.y, b.y) - 14;
      y = above >= 14 ? above : Math.max(a.y + a.h, b.y + b.h) + 16;
    }
    if (edge.kind === "direct" && a.node.role === "entry") {
      x = (s.x + e.x) / 2;
      y = Math.min(s.y, e.y) - 12;
      if (y < 14) y = 16;
    }
    return { x, y };
  }

  function click(id: string) {
    const node = byId.get(id);
    if (!node) return;
    if (["client", "clients", "direct", "internet", "warp"].includes(node.role)) return;
    onSelect?.(id);
  }

  return (
    <div className="card overflow-x-auto p-4">
      <div className="mb-3 flex items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Как идёт трафик</h2>
          <p className="text-xs text-mist">Живая схема по текущим узлам, правилам и переключателям. Нажмите узел, чтобы открыть его карточку.</p>
        </div>
        <div className="flex flex-wrap gap-3 text-xs text-mist">
          <span className="inline-flex items-center gap-1"><i className="inline-block h-2 w-6 rounded-full bg-ok" /> напрямую</span>
          <span className="inline-flex items-center gap-1"><i className="inline-block h-2 w-6 rounded-full bg-teal" /> каскад</span>
          <span className="inline-flex items-center gap-1"><i className="inline-block h-2 w-6 rounded-full bg-amber" /> WARP / резерв</span>
          <span className="inline-flex items-center gap-1"><i className="inline-block h-2 w-6 rounded-full bg-rose" /> нет связи</span>
        </div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="min-w-[900px] w-full" role="img" aria-label="Схема движения трафика">
        {data.edges.map((edge, index) => {
          const d = path(edge);
          if (!d) return null;
          const cls = `flow-line ${edge.active ? "active" : "down"} ${edge.style || edge.kind}`;
          const point = labelPoint(edge);
          return (
            <g key={`${edge.from}-${edge.to}-${index}`}>
              <path d={d} className={cls} />
              {edge.label && (
                <text x={point.x} y={point.y} textAnchor="middle" className="flow-label fill-mist">
                  {edge.label}
                </text>
              )}
            </g>
          );
        })}
        {boxes.map((box) => (
          <foreignObject key={box.id} x={box.x} y={box.y} width={box.w} height={box.h}>
            <button
              type="button"
              onClick={() => click(box.id)}
              className={`flex h-full w-full items-stretch overflow-hidden rounded-2xl border border-line bg-card/80 text-left ring-2 ${tone(box.node.kind, box.node.status)}`}
            >
              {box.node.role === "direct" ? (
                <div className="flex w-16 shrink-0 items-center justify-center sm:w-20">
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-ok/20 text-ok" aria-hidden="true">
                    <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <circle cx="12" cy="12" r="8" />
                      <path d="M12 8v8M8 12h8" strokeLinecap="round" />
                    </svg>
                  </span>
                </div>
              ) : box.node.heroine !== "" && box.node.role !== "internet" && box.node.role !== "warp" && box.node.role !== "client" && box.node.role !== "clients" ? (
                <div className="heroine-frame relative w-16 shrink-0 self-stretch sm:w-20">
                  <img className="heroine-cut h-full w-full" alt="" src={box.node.heroine || heroineSrc(box.node.country, box.node.name)} />
                </div>
              ) : null}
              <div className="min-w-0 flex-1 p-2">
                <div className="truncate text-xs font-semibold">{box.node.name}</div>
                <div className="text-[10px] uppercase tracking-wide text-mist">{roleText(box.node)}</div>
                {box.node.ports && box.node.ports.length > 0 && (
                  <div className="mt-1 truncate text-[10px] text-mist">
                    {box.node.ports.map((port) => `${port.name} ${port.port}/${port.proto}`).join(" · ")}
                  </div>
                )}
                {box.node.warp?.enabled && (
                  <div className="mt-1 truncate text-[10px] text-amber">
                    WARP {box.node.warp.mode === "all" ? "весь трафик" : "сайты"}
                    {box.node.warp.egress_ip ? ` · ${box.node.warp.egress_ip}` : ""}
                  </div>
                )}
                {box.node.label && <div className="mt-1 truncate text-[10px] text-mist">{box.node.label}</div>}
              </div>
            </button>
          </foreignObject>
        ))}
      </svg>
    </div>
  );
}

function roleText(node: FlowNode): string {
  const map: Record<string, string> = {
    entry: "Вход",
    standby_entry: "Запасной вход",
    transit: "Транзит",
    exit: "Выход",
    standby_exit: "Резерв",
    client: "Клиент",
    clients: "Клиенты",
    direct: "Напрямую",
    warp: "WARP",
    internet: "Интернет",
  };
  return map[node.role] || node.role;
}
