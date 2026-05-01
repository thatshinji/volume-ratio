import { useCallback, useRef, useState } from "react";
import { Routes, Route, NavLink } from "react-router-dom";
import Dashboard from "./components/Dashboard";
import SignalList from "./components/SignalList";
import Watchlist from "./components/Watchlist";
import Settings from "./components/Settings";
import { useWebSocket } from "./hooks/useWebSocket";
import { showNotification, type AlertNotification } from "./lib/notifications";

const navItems = [
  { path: "/", label: "Dashboard" },
  { path: "/signals", label: "Signals" },
  { path: "/watchlist", label: "Watchlist" },
  { path: "/settings", label: "Settings" },
];

export default function App() {
  const [alertCount, setAlertCount] = useState(0);
  const alertBuffer = useRef<AlertNotification[]>([]);

  const handleAlert = useCallback((data: unknown) => {
    const alert = data as AlertNotification;
    if (alert.type === "alert") {
      alertBuffer.current = [alert, ...alertBuffer.current.slice(0, 99)];
      setAlertCount((c) => c + 1);
      showNotification(alert);
    }
  }, []);

  // Connect to alerts WebSocket — the Tauri backend proxies this
  // In dev mode (browser), connect directly to Python API
  const wsUrl = "ws://127.0.0.1:9720/ws/alerts";
  useWebSocket({ url: wsUrl, onMessage: handleAlert, reconnectInterval: 5000 });

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <nav className="w-48 bg-white border-r border-gray-200 flex flex-col">
        <div className="p-4 border-b border-gray-200">
          <h1 className="text-lg font-bold text-gray-800">Volume Ratio</h1>
          <p className="text-xs text-gray-500">Cross-market monitor</p>
        </div>
        <div className="flex-1 p-2 space-y-1">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              className={({ isActive }) =>
                `block px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-blue-50 text-blue-700"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                }`
              }
            >
              <span className="flex items-center justify-between">
                {item.label}
                {item.path === "/signals" && alertCount > 0 && (
                  <span className="bg-red-500 text-white text-xs rounded-full px-1.5 py-0.5 min-w-[20px] text-center">
                    {alertCount > 99 ? "99+" : alertCount}
                  </span>
                )}
              </span>
            </NavLink>
          ))}
        </div>
        <div className="p-3 border-t border-gray-200 text-xs text-gray-400">
          v0.1.0
        </div>
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/signals" element={<SignalList />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
