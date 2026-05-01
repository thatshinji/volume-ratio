import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import type { ScanResult, MarketSummary } from "../lib/types";

const RATIO_EMOJI = (ratio: number) => {
  if (ratio > 5) return "🔥🔥";
  if (ratio > 2) return "🔥";
  if (ratio < 0.6) return "⚠️";
  return "✅";
};

const RATIO_LABEL = (ratio: number) => {
  if (ratio <= 0) return "数据不足";
  if (ratio < 0.6) return "缩量异常";
  if (ratio < 0.8) return "缩量";
  if (ratio <= 1.2) return "正常";
  if (ratio <= 2.0) return "放量";
  if (ratio <= 5.0) return "显著放量";
  return "巨量";
};

const CHANGE_ARROW = (pct: number) => (pct >= 0 ? "↑" : "↓");

function groupByMarket(results: ScanResult[]): Map<string, ScanResult[]> {
  const groups = new Map<string, ScanResult[]>();
  for (const r of results) {
    let market = "Other";
    if (r.ticker.endsWith(".US")) market = "US";
    else if (r.ticker.endsWith(".HK")) market = "HK";
    else if (r.ticker.endsWith(".SH") || r.ticker.endsWith(".SZ")) market = "CN";
    if (!groups.has(market)) groups.set(market, []);
    groups.get(market)!.push(r);
  }
  return groups;
}

function marketSummaries(results: ScanResult[]): MarketSummary[] {
  const groups = groupByMarket(results);
  return Array.from(groups.entries()).map(([market, items]) => ({
    market,
    count: items.length,
    trading: true, // will be updated from status
    signal_count: items.filter((i) => i.ratio > 2 || i.ratio < 0.6).length,
  }));
}

export default function Dashboard() {
  const [results, setResults] = useState<ScanResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("All");
  const [sortKey, setSortKey] = useState<"ratio" | "change_pct" | "ticker">("ratio");
  const [brief, setBrief] = useState<{ text: string; analysis?: string } | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.scan();
      setResults(data);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const generateBrief = useCallback(async () => {
    try {
      setBriefLoading(true);
      const data = await api.brief();
      setBrief({ text: data.brief, analysis: data.llm_analysis });
    } catch (e) {
      setError(String(e));
    } finally {
      setBriefLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30_000); // refresh every 30s
    return () => clearInterval(interval);
  }, [fetchData]);

  const groups = groupByMarket(results);
  const summaries = marketSummaries(results);

  const filtered = (
    filter === "All"
      ? results
      : groups.get(filter) || []
  ).sort((a, b) => {
    if (sortKey === "ratio") return b.ratio - a.ratio;
    if (sortKey === "change_pct") return b.change_pct - a.change_pct;
    return a.ticker.localeCompare(b.ticker);
  });

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">Dashboard</h2>
        <button
          onClick={fetchData}
          disabled={loading}
          className="px-3 py-1.5 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600 disabled:opacity-50"
        >
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-md text-sm">
          {error}
        </div>
      )}

      {/* Market summary cards */}
      <div className="grid grid-cols-3 gap-4">
        {["US", "HK", "CN"].map((m) => {
          const s = summaries.find((x) => x.market === m);
          return (
            <div
              key={m}
              className="bg-white rounded-lg border border-gray-200 p-4 cursor-pointer hover:border-blue-300 transition-colors"
              onClick={() => setFilter(filter === m ? "All" : m)}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-gray-500">{m} Market</span>
                <span
                  className={`w-2 h-2 rounded-full ${
                    s ? "bg-green-400" : "bg-gray-300"
                  }`}
                />
              </div>
              <div className="text-2xl font-bold text-gray-800">{s?.count || 0}</div>
              <div className="text-xs text-gray-500">
                {s?.signal_count || 0} signals
              </div>
            </div>
          );
        })}
      </div>

      {/* AI Brief */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-gray-700">AI Brief</h3>
          <button
            onClick={generateBrief}
            disabled={briefLoading}
            className="px-3 py-1 text-xs bg-indigo-500 text-white rounded-md hover:bg-indigo-600 disabled:opacity-50"
          >
            {briefLoading ? "Generating..." : "Generate AI Analysis"}
          </button>
        </div>
        {brief ? (
          <div className="space-y-2">
            {brief.analysis && (
              <p className="text-sm text-gray-700 leading-relaxed">{brief.analysis}</p>
            )}
          </div>
        ) : (
          <p className="text-xs text-gray-400">Click the button to generate an AI-powered market brief.</p>
        )}
      </div>

      {/* Filter and sort */}
      <div className="flex items-center gap-3">
        <div className="flex gap-1">
          {["All", "US", "HK", "CN"].map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1 text-sm rounded-md transition-colors ${
                filter === f
                  ? "bg-blue-100 text-blue-700 font-medium"
                  : "text-gray-500 hover:bg-gray-100"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <select
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value as typeof sortKey)}
          className="ml-auto text-sm border border-gray-200 rounded-md px-2 py-1 bg-white"
        >
          <option value="ratio">Sort by Ratio</option>
          <option value="change_pct">Sort by Change%</option>
          <option value="ticker">Sort by Ticker</option>
        </select>
      </div>

      {/* Results table */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Ticker</th>
              <th className="text-right px-4 py-3 font-medium text-gray-500">Price</th>
              <th className="text-right px-4 py-3 font-medium text-gray-500">Change</th>
              <th className="text-right px-4 py-3 font-medium text-gray-500">5D Ratio</th>
              <th className="text-right px-4 py-3 font-medium text-gray-500">Intraday</th>
              <th className="text-right px-4 py-3 font-medium text-gray-500">Samples</th>
              <th className="text-left px-4 py-3 font-medium text-gray-500">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {filtered.map((r) => (
              <tr key={r.ticker} className="hover:bg-gray-50 transition-colors">
                <td className="px-4 py-3">
                  <div className="font-medium text-gray-800">{r.ticker}</div>
                  <div className="text-xs text-gray-500">{r.name}</div>
                </td>
                <td className="px-4 py-3 text-right font-mono text-gray-700">
                  {r.price > 0 ? `$${r.price.toFixed(2)}` : "-"}
                </td>
                <td
                  className={`px-4 py-3 text-right font-mono ${
                    r.change_pct >= 0 ? "text-green-600" : "text-red-600"
                  }`}
                >
                  {r.change_pct !== 0
                    ? `${CHANGE_ARROW(r.change_pct)}${Math.abs(r.change_pct).toFixed(2)}%`
                    : "-"}
                </td>
                <td className="px-4 py-3 text-right font-mono font-medium">
                  {r.ratio > 0 ? r.ratio.toFixed(2) : "-"}
                </td>
                <td className="px-4 py-3 text-right font-mono text-gray-500">
                  {r.ratio_intraday && r.ratio_intraday > 0
                    ? r.ratio_intraday.toFixed(2)
                    : "-"}
                </td>
                <td className="px-4 py-3 text-right text-gray-500">
                  {r.historical_sample_days}/5
                </td>
                <td className="px-4 py-3">
                  <span className="inline-flex items-center gap-1">
                    {RATIO_EMOJI(r.ratio)}
                    <span className="text-xs text-gray-500">
                      {RATIO_LABEL(r.ratio)}
                    </span>
                  </span>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && !loading && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-gray-400">
                  No data available
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
