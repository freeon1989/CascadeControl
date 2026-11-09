import { NavLink, Route, Routes } from "react-router-dom";
import { useState } from "react";
import { OverviewPage } from "./pages/Overview";
import { ServersPage } from "./pages/Servers";
import { RoutingPage } from "./pages/Routing";
import { ClientsPage } from "./pages/Clients";
import { JournalPage } from "./pages/Journal";
import { SettingsPage } from "./pages/Settings";

const links = [
  ["/", "Обзор"],
  ["/servers", "Серверы"],
  ["/routing", "Маршрутизация"],
  ["/clients", "Клиенты"],
  ["/journal", "Журнал"],
  ["/settings", "Настройки"],
];

export function App() {
  const [open, setOpen] = useState(false);
  return (
    <div className="shell">
      <aside className={open ? "side open" : "side"}>
        <div className="mb-8 flex items-center gap-3 px-2">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-teal/15 text-teal shadow-glow">
            <Shield />
          </div>
          <div>
            <div className="text-sm font-semibold">Cascade Control</div>
            <div className="text-xs text-mist">VPN Cascade Panel</div>
          </div>
        </div>
        <nav className="flex flex-col gap-1">
          {links.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setOpen(false)}
              className={({ isActive }) => `nav-btn ${isActive ? "active" : ""}`}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto card p-3">
          <div className="flex items-center gap-2 text-sm text-ok">
            <span className="h-2 w-2 rounded-full bg-ok" /> Система
          </div>
          <div className="mt-1 text-xs text-mist">Панель только на 127.0.0.1</div>
        </div>
      </aside>
      <div>
        <div className="flex items-center gap-3 px-4 pt-4 lg:hidden">
          <button className="menu-toggle rounded-lg border border-line px-3 py-2" onClick={() => setOpen((v) => !v)}>
            Меню
          </button>
          <span className="text-sm text-mist">Cascade Control</span>
        </div>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/servers" element={<ServersPage />} />
          <Route path="/routing" element={<RoutingPage />} />
          <Route path="/clients" element={<ClientsPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </div>
    </div>
  );
}

function Shield() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
      <path d="M12 3 5 6v6c0 4.2 2.8 7.4 7 9 4.2-1.6 7-4.8 7-9V6l-7-3Z" stroke="currentColor" strokeWidth="1.6" />
      <path d="M9 12.2 11 14l4-4.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
