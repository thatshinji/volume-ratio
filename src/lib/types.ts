export interface ScanResult {
  ticker: string;
  name: string;
  price: number;
  change_pct: number;
  ratio: number;
  ratio_intraday: number | null;
  historical_sample_days: number;
  signal: string;
  signal_detail: string;
  data_quality: string;
}

export interface Signal {
  id: number;
  ticker: string;
  name: string;
  timestamp: string;
  signal_type: string;
  ratio: number;
  price: number;
  change_pct: number;
  source: string;
  llm_analysis: string | null;
}

export interface SystemStatus {
  websocket: { running: boolean; pid: number | null };
  database: { records: number; size_bytes: number; max_bytes: number };
  snapshots: { files: number; size_bytes: number; max_bytes: number };
  llm_calls_today: number;
  markets: MarketStatus[];
  params: Record<string, unknown>;
}

export interface MarketStatus {
  market: string;
  is_trading: boolean;
  latest_snapshot: string | null;
  latest_age_seconds: number | null;
}

export interface MarketSummary {
  market: string;
  count: number;
  trading: boolean;
  signal_count: number;
}

export interface WatchlistItem {
  ticker: string;
  name: string;
  market: string;
}
