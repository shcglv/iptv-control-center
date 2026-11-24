import { useEffect, useState } from "react";
import { me, logout } from "./api";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Channels from "./pages/Channels";
import Quality from "./pages/Quality";
import Discovery from "./pages/Discovery";
import Profiles from "./pages/Profiles";
import Sources from "./pages/Sources";
import Devices from "./pages/Devices";

type Tab = "overview" | "channels" | "quality" | "discovery" | "profiles" | "sources" | "devices";

const NAV: { group: string; items: { id: Tab; label: string; icon: string }[] }[] = [
  {
    group: "Workspace",
    items: [
      { id: "overview", label: "Обзор", icon: "◫" },
      { id: "channels", label: "Каналы", icon: "▤" },
      { id: "quality", label: "Качество", icon: "◔" },
      { id: "discovery", label: "Обнаружение", icon: "✦" },
    ],
  },
  {
    group: "Manage",
    items: [
      { id: "profiles", label: "Плейлисты", icon: "◍" },
      { id: "sources", label: "Источники", icon: "◈" },
      { id: "devices", label: "Устройства", icon: "▣" },
    ],
  },
];

export default function App() {
  const [state, setState] = useState<"loading" | "in" | "out">("loading");

  useEffect(() => {
    me()
      .then(() => setState("in"))
      .catch(() => setState("out"));
  }, []);

  if (state === "loading") return <div className="empty">Загрузка…</div>;
  if (state === "out") return <Login onSuccess={() => setState("in")} />;
  return <Shell onSignedOut={() => setState("out")} />;
}

function Shell({ onSignedOut }: { onSignedOut: () => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const [signingOut, setSigningOut] = useState(false);

  const signOut = async () => {
    setSigningOut(true);
    try {
      await logout();
    } finally {
      onSignedOut();
    }
  };

  const title = NAV.flatMap((group) => group.items).find((item) => item.id === tab)?.label ?? "Обзор";

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true" />
          <div>
            <div className="brand-name">IPTV Control Center</div>
            <div className="brand-sub">Control plane</div>
          </div>
        </div>
        {NAV.map((group) => (
          <nav key={group.group} className="nav-group" aria-label={group.group}>
            <div className="nav-label">{group.group}</div>
            {group.items.map((item) => (
              <button
                key={item.id}
                className="nav-item"
                aria-current={tab === item.id ? "page" : undefined}
                onClick={() => setTab(item.id)}
              >
                <span className="nav-icon" aria-hidden="true">
                  {item.icon}
                </span>
                {item.label}
              </button>
            ))}
          </nav>
        ))}
      </aside>

      <div className="content">
        <header className="topbar">
          <div className="topbar-title">{title}</div>
          <div className="topbar-actions">
            <button className="btn btn-sm" onClick={signOut} disabled={signingOut}>
              Выйти
            </button>
          </div>
        </header>
        <main className="page">
          {tab === "overview" && <Overview />}
          {tab === "channels" && <Channels />}
          {tab === "quality" && <Quality />}
          {tab === "discovery" && <Discovery />}
          {tab === "profiles" && <Profiles />}
          {tab === "sources" && <Sources />}
          {tab === "devices" && <Devices />}
        </main>
      </div>
    </div>
  );
}
