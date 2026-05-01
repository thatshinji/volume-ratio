import { invoke } from "@tauri-apps/api/core";

async function apiGet<T>(path: string): Promise<T> {
  const text = await invoke<string>("proxy_get", { path });
  return JSON.parse(text);
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const text = await invoke<string>("proxy_post", {
    path,
    body: body ? JSON.stringify(body) : null,
  });
  return JSON.parse(text);
}

async function apiDelete<T>(path: string): Promise<T> {
  const text = await invoke<string>("proxy_delete", { path });
  return JSON.parse(text);
}

async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  const text = await invoke<string>("proxy_put", {
    path,
    body: body ? JSON.stringify(body) : null,
  });
  return JSON.parse(text);
}

export const api = {
  health: () => apiGet<{ status: string }>("/api/health"),
  scan: () => apiPost<import("./types").ScanResult[]>("/api/scan"),
  scanTicker: (ticker: string) =>
    apiGet<import("./types").ScanResult>(`/api/scan/${encodeURIComponent(ticker)}`),
  signals: () => apiGet<import("./types").Signal[]>("/api/signals"),
  signalHistory: (ticker: string) =>
    apiGet<import("./types").Signal[]>(`/api/signals/history/${encodeURIComponent(ticker)}`),
  status: () => apiGet<import("./types").SystemStatus>("/api/status"),
  watchlist: () =>
    apiGet<{ us: string[]; hk: string[]; cn: string[] }>("/api/watchlist"),
  addTicker: (raw: string) =>
    apiPost<{ ok: boolean; message: string }>("/api/watchlist", { raw }),
  removeTicker: (ticker: string) =>
    apiDelete<{ ok: boolean; message: string }>(
      `/api/watchlist/${encodeURIComponent(ticker)}`
    ),
  mute: (ticker: string, duration: string) =>
    apiPost<{ ok: boolean; message: string }>("/api/mute", { ticker, duration }),
  brief: () => apiPost<{ brief: string; llm_analysis?: string }>("/api/brief"),
  analyze: (ticker: string) =>
    apiPost<{ analysis: string }>("/api/analyze", { ticker }),
  config: () => apiGet<Record<string, unknown>>("/api/config"),
  updateParams: (params: Record<string, unknown>) =>
    apiPut<{ ok: boolean; params: Record<string, unknown> }>("/api/config/params", { params }),
  updateLLM: (data: { provider?: string; model?: string; base_url?: string; api_key?: string; max_tokens?: number; temperature?: number }) =>
    apiPut<{ ok: boolean }>("/api/config/llm", data),
  switchLLM: (profile: string) =>
    apiPut<{ ok: boolean; profile: string }>("/api/config/llm/switch", { profile }),
  llmProfiles: () =>
    apiGet<{ profiles: { name: string; provider: string; model: string; base_url: string; active: boolean }[]; active_provider: string }>("/api/config/llm/profiles"),
  updateLongbridge: (data: { app_key?: string; app_secret?: string; access_token?: string }) =>
    apiPut<{ ok: boolean }>("/api/config/longbridge", data),
  updateFeishu: (data: { app_id?: string; app_secret?: string; chat_id?: string; webhook_url?: string }) =>
    apiPut<{ ok: boolean }>("/api/config/feishu", data),
  ratioHistory: (ticker: string) =>
    apiGet<{ ticker: string; market_date: string; ratio: number; ratio_intraday: number; price: number; change_pct: number }[]>(
      `/api/signals/ratios/${encodeURIComponent(ticker)}`
    ),
  syncWatchlist: () =>
    apiPost<{ added: string[]; removed: string[]; positions: string[]; watchlist_groups: string[] }>(
      "/api/watchlist/sync"
    ),
};
