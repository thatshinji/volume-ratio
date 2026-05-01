import { sendNotification } from "@tauri-apps/plugin-notification";

export interface AlertNotification {
  type: string;
  ticker: string;
  name: string;
  signal: string;
  ratio: number;
  change_pct: number;
  price: number;
  source: string;
  analysis?: string;
}

export function showNotification(alert: AlertNotification) {
  const direction = alert.change_pct >= 0 ? "↑" : "↓";
  const title = `${alert.ratio > 2 ? "🔥" : alert.ratio < 0.6 ? "⚠️" : "📊"} ${alert.ticker} - ${alert.name}`;
  const body = [
    `${direction}${Math.abs(alert.change_pct).toFixed(2)}% | 量比 ${alert.ratio.toFixed(2)}`,
    alert.signal,
    alert.analysis ? `AI: ${alert.analysis}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  try {
    sendNotification({ title, body });
  } catch {
    // Notification API not available (e.g., in browser dev mode)
    console.log("[notification]", title, body);
  }
}
