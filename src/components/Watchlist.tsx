import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

interface WatchlistData {
  us: string[];
  hk: string[];
  cn: string[];
}

export default function Watchlist() {
  const [watchlist, setWatchlist] = useState<WatchlistData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addInput, setAddInput] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.watchlist();
      setWatchlist(data);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleAdd = async () => {
    if (!addInput.trim()) return;
    try {
      await api.addTicker(addInput.trim());
      setAddInput("");
      fetchData();
    } catch (e) {
      setError(String(e));
    }
  };

  const handleRemove = async (ticker: string) => {
    try {
      await api.removeTicker(ticker);
      fetchData();
    } catch (e) {
      setError(String(e));
    }
  };

  const handleSync = async () => {
    try {
      setSyncing(true);
      setSyncResult(null);
      const result = await api.syncWatchlist();
      const parts = [];
      if (result.added.length) parts.push(`Added: ${result.added.join(", ")}`);
      if (result.removed.length) parts.push(`Removed: ${result.removed.join(", ")}`);
      if (!parts.length) parts.push("No changes");
      setSyncResult(parts.join(" | "));
      fetchData();
    } catch (e) {
      setError(String(e));
    } finally {
      setSyncing(false);
    }
  };

  const markets = watchlist
    ? [
        { key: "us" as const, label: "US Market" },
        { key: "hk" as const, label: "HK Market" },
        { key: "cn" as const, label: "CN Market" },
      ].filter((m) => watchlist[m.key].length > 0)
    : [];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">Watchlist</h2>
        <div className="flex gap-2">
          <button
            onClick={handleSync}
            disabled={syncing}
            className="px-3 py-1.5 text-sm bg-indigo-500 text-white rounded-md hover:bg-indigo-600 disabled:opacity-50"
          >
            {syncing ? "Syncing..." : "Sync from Longbridge"}
          </button>
          <button
            onClick={fetchData}
            disabled={loading}
            className="px-3 py-1.5 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600 disabled:opacity-50"
          >
            {loading ? "Loading..." : "Refresh"}
          </button>
        </div>
      </div>

      {syncResult && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-md text-sm">
          {syncResult}
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-md text-sm">
          {error}
        </div>
      )}

      {/* Add ticker */}
      <div className="flex gap-2">
        <input
          type="text"
          value={addInput}
          onChange={(e) => setAddInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="CLF.US-Cleveland Cliffs"
          className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-md focus:outline-none focus:border-blue-400"
        />
        <button
          onClick={handleAdd}
          className="px-4 py-2 text-sm bg-green-500 text-white rounded-md hover:bg-green-600"
        >
          + Add
        </button>
      </div>

      {/* Market groups */}
      {markets.map((m) => (
        <div key={m.key} className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="px-4 py-3 bg-gray-50 border-b border-gray-200">
            <h3 className="text-sm font-medium text-gray-700">{m.label}</h3>
          </div>
          <ul className="divide-y divide-gray-100">
            {watchlist![m.key].map((raw) => {
              const dashIdx = raw.indexOf("-");
              const ticker = dashIdx > 0 ? raw.substring(0, dashIdx) : raw;
              const name = dashIdx > 0 ? raw.substring(dashIdx + 1) : ticker;
              return (
                <li
                  key={ticker}
                  className="flex items-center justify-between px-4 py-3 hover:bg-gray-50"
                >
                  <div>
                    <span className="font-medium text-gray-800 text-sm">{ticker}</span>
                    <span className="text-xs text-gray-500 ml-2">{name}</span>
                  </div>
                  <button
                    onClick={() => handleRemove(ticker)}
                    className="px-2 py-1 text-xs text-red-500 border border-red-200 rounded hover:bg-red-50"
                  >
                    Remove
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}

      {watchlist && markets.length === 0 && (
        <div className="text-center text-gray-400 py-8">
          No tickers in watchlist. Add one above.
        </div>
      )}
    </div>
  );
}
