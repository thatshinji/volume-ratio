import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import type { Signal } from "../lib/types";
import RatioChart from "./RatioChart";

interface RatioDataPoint {
  market_date: string;
  ratio: number;
}

export default function SignalList() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);
  const [chartData, setChartData] = useState<{ time: string; value: number }[]>([]);
  const [chartLoading, setChartLoading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.signals();
      setSignals(data);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleMute = async (ticker: string) => {
    try {
      await api.mute(ticker, "2h");
      fetchData();
    } catch (e) {
      setError(String(e));
    }
  };

  const handleAnalyze = async (ticker: string) => {
    try {
      const result = await api.analyze(ticker);
      setSignals((prev) =>
        prev.map((s) =>
          s.ticker === ticker ? { ...s, llm_analysis: result.analysis } : s
        )
      );
    } catch (e) {
      setError(String(e));
    }
  };

  const toggleChart = async (ticker: string) => {
    if (expandedTicker === ticker) {
      setExpandedTicker(null);
      setChartData([]);
      return;
    }
    setExpandedTicker(ticker);
    setChartLoading(true);
    try {
      const history: RatioDataPoint[] = await api.ratioHistory(ticker);
      const data = history
        .map((h) => ({
          time: h.market_date,
          value: h.ratio,
        }))
        .reverse();
      setChartData(data);
    } catch {
      setChartData([]);
    } finally {
      setChartLoading(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">
          Signals — {new Date().toLocaleDateString("zh-CN")}
        </h2>
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

      <div className="space-y-3">
        {signals.map((s) => (
          <div
            key={s.id}
            className="bg-white rounded-lg border border-gray-200 p-4 hover:border-blue-200 transition-colors"
          >
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-500 font-mono">
                    {new Date(s.timestamp).toLocaleTimeString("zh-CN", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                  <span
                    className={`text-sm font-medium ${
                      s.change_pct >= 0 ? "text-green-600" : "text-red-600"
                    }`}
                  >
                    {s.change_pct >= 0 ? "↑" : "↓"}
                    {Math.abs(s.change_pct).toFixed(2)}%
                  </span>
                </div>
                <div className="mt-1 font-medium text-gray-800">
                  {s.ticker} - {s.name}
                </div>
                <div className="mt-1 flex items-center gap-3 text-sm">
                  <span className="text-gray-600">
                    量比 <span className="font-mono font-medium">{s.ratio.toFixed(2)}</span>
                  </span>
                  <span className="text-gray-400">|</span>
                  <span className="text-gray-600">{s.signal_type}</span>
                  <span className="text-gray-400">|</span>
                  <span className="text-gray-500">{s.source}</span>
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => toggleChart(s.ticker)}
                  className={`px-2 py-1 text-xs border rounded transition-colors ${
                    expandedTicker === s.ticker
                      ? "text-blue-700 bg-blue-50 border-blue-300"
                      : "text-gray-500 border-gray-200 hover:bg-gray-50"
                  }`}
                >
                  Trend
                </button>
                <button
                  onClick={() => handleMute(s.ticker)}
                  className="px-2 py-1 text-xs text-gray-500 border border-gray-200 rounded hover:bg-gray-50"
                >
                  Mute 2h
                </button>
                <button
                  onClick={() => handleAnalyze(s.ticker)}
                  className="px-2 py-1 text-xs text-blue-600 border border-blue-200 rounded hover:bg-blue-50"
                >
                  Analyze
                </button>
              </div>
            </div>
            {s.llm_analysis && (
              <div className="mt-3 p-3 bg-blue-50 rounded-md text-sm text-blue-800">
                {s.llm_analysis}
              </div>
            )}
            {expandedTicker === s.ticker && (
              <div className="mt-3 pt-3 border-t border-gray-100">
                <h4 className="text-xs font-medium text-gray-500 mb-2">7-Day Ratio Trend</h4>
                {chartLoading ? (
                  <div className="text-center text-gray-400 py-4 text-sm">Loading chart...</div>
                ) : chartData.length > 0 ? (
                  <RatioChart data={chartData} height={150} />
                ) : (
                  <div className="text-center text-gray-400 py-4 text-sm">No history data</div>
                )}
              </div>
            )}
          </div>
        ))}
        {signals.length === 0 && !loading && (
          <div className="text-center text-gray-400 py-8">No signals today</div>
        )}
      </div>
    </div>
  );
}
