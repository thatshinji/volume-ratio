import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";
import type { SystemStatus } from "../lib/types";

type Tab = "status" | "apikeys" | "params" | "notifications";

export default function Settings() {
  const [tab, setTab] = useState<Tab>("status");
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.status();
      setStatus(data);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const flash = (msg: string) => {
    setSuccess(msg);
    setTimeout(() => setSuccess(null), 3000);
  };

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: "status", label: "Status" },
    { key: "apikeys", label: "API Keys" },
    { key: "params", label: "Parameters" },
    { key: "notifications", label: "Notifications" },
  ];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-800">Settings</h2>
        <button
          onClick={fetchStatus}
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
      {success && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-md text-sm">
          {success}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key
                ? "border-blue-500 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "status" && status && (
        <StatusTab status={status} formatBytes={formatBytes} />
      )}
      {tab === "apikeys" && (
        <APIKeysTab onError={setError} onSuccess={flash} />
      )}
      {tab === "params" && (
        <ParamsTab status={status} onError={setError} onSuccess={flash} />
      )}
      {tab === "notifications" && (
        <NotificationsTab onError={setError} onSuccess={flash} />
      )}
    </div>
  );
}

// ---- Status Tab ----

function StatusTab({
  status,
  formatBytes,
}: {
  status: SystemStatus;
  formatBytes: (b: number) => string;
}) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
      <h3 className="text-base font-medium text-gray-700 border-b border-gray-100 pb-2">
        System Status
      </h3>
      <div className="grid grid-cols-2 gap-4 text-sm">
        <StatusItem
          color={status.websocket.running ? "green" : "red"}
          label="WebSocket"
          value={
            status.websocket.running
              ? `Running${status.websocket.pid ? ` (PID ${status.websocket.pid})` : ""}`
              : "Stopped"
          }
        />
        <StatusItem
          color="green"
          label="Database"
          value={`${status.database.records} records, ${formatBytes(status.database.size_bytes)} / ${formatBytes(status.database.max_bytes)}`}
        />
        <StatusItem
          color="green"
          label="Snapshots"
          value={`${status.snapshots.files} files, ${formatBytes(status.snapshots.size_bytes)} / ${formatBytes(status.snapshots.max_bytes)}`}
        />
        <StatusItem
          color="blue"
          label="LLM Calls"
          value={`${status.llm_calls_today} today`}
        />
      </div>

      <div className="pt-2">
        <h4 className="text-sm font-medium text-gray-600 mb-2">Markets</h4>
        <div className="space-y-1">
          {status.markets.map((m) => (
            <div key={m.market} className="flex items-center gap-2 text-sm">
              <span
                className={`w-2 h-2 rounded-full ${m.is_trading ? "bg-green-400" : "bg-gray-300"}`}
              />
              <span className="text-gray-600 w-8">{m.market}</span>
              <span className="text-gray-800">
                {m.is_trading ? "Trading" : "Closed"}
              </span>
            </div>
          ))}
        </div>
      </div>

      {Object.keys(status.params).length > 0 && (
        <div className="pt-2">
          <h4 className="text-sm font-medium text-gray-600 mb-2">
            Algorithm Parameters
          </h4>
          <div className="grid grid-cols-2 gap-2 text-sm">
            {Object.entries(status.params).map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-gray-500">{k}</span>
                <span className="text-gray-800 font-mono">{String(v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatusItem({
  color,
  label,
  value,
}: {
  color: string;
  label: string;
  value: string;
}) {
  const colorClass =
    color === "green"
      ? "bg-green-400"
      : color === "red"
        ? "bg-red-400"
        : "bg-blue-400";
  return (
    <div className="flex items-center gap-2">
      <span className={`w-2 h-2 rounded-full ${colorClass}`} />
      <span className="text-gray-600">{label}</span>
      <span className="text-gray-800 font-medium">{value}</span>
    </div>
  );
}

// ---- API Keys Tab ----

function APIKeysTab({
  onError,
  onSuccess,
}: {
  onError: (e: string | null) => void;
  onSuccess: (msg: string) => void;
}) {
  const [loading, setLoading] = useState(true);

  // Longbridge
  const [lbKey, setLbKey] = useState("");
  const [lbSecret, setLbSecret] = useState("");
  const [lbToken, setLbToken] = useState("");

  // LLM
  const [llmProvider, setLlmProvider] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [llmBaseUrl, setLlmBaseUrl] = useState("");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [llmMaxTokens, setLlmMaxTokens] = useState("");
  const [llmTemperature, setLlmTemperature] = useState("");

  // LLM profiles
  const [profiles, setProfiles] = useState<
    { name: string; provider: string; model: string; active: boolean }[]
  >([]);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const [cfg, profs] = await Promise.all([
          api.config(),
          api.llmProfiles(),
        ]);
        setProfiles(profs.profiles);

        const llm = (cfg.llm || {}) as Record<string, unknown>;
        setLlmProvider(String(llm.provider || ""));
        setLlmModel(String(llm.model || ""));
        setLlmBaseUrl(String(llm.base_url || ""));
        setLlmMaxTokens(String(llm.max_tokens || ""));
        setLlmTemperature(String(llm.temperature || ""));

        const lb = (cfg.longbridge || {}) as Record<string, unknown>;
        setLbKey(String(lb.app_key || ""));
      } catch (e) {
        onError(String(e));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const saveLongbridge = async () => {
    try {
      const data: Record<string, string> = {};
      if (lbKey) data.app_key = lbKey;
      if (lbSecret) data.app_secret = lbSecret;
      if (lbToken) data.access_token = lbToken;
      await api.updateLongbridge(data);
      onSuccess("Longbridge config saved");
    } catch (e) {
      onError(String(e));
    }
  };

  const saveLLM = async () => {
    try {
      const data: Record<string, unknown> = {};
      if (llmProvider) data.provider = llmProvider;
      if (llmModel) data.model = llmModel;
      if (llmBaseUrl) data.base_url = llmBaseUrl;
      if (llmApiKey) data.api_key = llmApiKey;
      if (llmMaxTokens) data.max_tokens = parseInt(llmMaxTokens);
      if (llmTemperature) data.temperature = parseFloat(llmTemperature);
      await api.updateLLM(data);
      onSuccess("LLM config saved");
    } catch (e) {
      onError(String(e));
    }
  };

  const switchProfile = async (name: string) => {
    try {
      await api.switchLLM(name);
      const profs = await api.llmProfiles();
      setProfiles(profs.profiles);
      onSuccess(`Switched to profile: ${name}`);
    } catch (e) {
      onError(String(e));
    }
  };

  if (loading) {
    return (
      <div className="text-center text-gray-400 py-8">Loading config...</div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Longbridge */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
        <h3 className="text-base font-medium text-gray-700 border-b border-gray-100 pb-2">
          Longbridge API
        </h3>
        <p className="text-xs text-gray-400">
          Get your keys from{" "}
          <a
            href="https://open.longportapp.com"
            target="_blank"
            className="text-blue-500 underline"
          >
            open.longportapp.com
          </a>
        </p>
        <div className="grid grid-cols-1 gap-3">
          <InputField
            label="App Key"
            value={lbKey}
            onChange={setLbKey}
            placeholder="App Key from Longbridge"
          />
          <InputField
            label="App Secret"
            value={lbSecret}
            onChange={setLbSecret}
            placeholder="App Secret"
            type="password"
          />
          <InputField
            label="Access Token"
            value={lbToken}
            onChange={setLbToken}
            placeholder="Access Token"
            type="password"
          />
        </div>
        <button
          onClick={saveLongbridge}
          className="px-4 py-2 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600"
        >
          Save Longbridge Config
        </button>
      </div>

      {/* LLM */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
        <h3 className="text-base font-medium text-gray-700 border-b border-gray-100 pb-2">
          LLM Configuration
        </h3>

        {/* Profile switcher */}
        {profiles.length > 0 && (
          <div>
            <label className="text-sm font-medium text-gray-600 block mb-1">
              Quick Switch
            </label>
            <div className="flex gap-2 flex-wrap">
              {profiles.map((p) => (
                <button
                  key={p.name}
                  onClick={() => switchProfile(p.name)}
                  className={`px-3 py-1.5 text-xs rounded-md border transition-colors ${
                    p.active
                      ? "bg-blue-50 border-blue-300 text-blue-700"
                      : "border-gray-200 text-gray-600 hover:bg-gray-50"
                  }`}
                >
                  {p.name} ({p.model})
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <InputField
            label="Provider"
            value={llmProvider}
            onChange={setLlmProvider}
            placeholder="xiaomi"
          />
          <InputField
            label="Model"
            value={llmModel}
            onChange={setLlmModel}
            placeholder="mimo-v2.5-pro"
          />
          <InputField
            label="Base URL"
            value={llmBaseUrl}
            onChange={setLlmBaseUrl}
            placeholder="https://..."
          />
          <InputField
            label="API Key"
            value={llmApiKey}
            onChange={setLlmApiKey}
            placeholder="sk-..."
            type="password"
          />
          <InputField
            label="Max Tokens"
            value={llmMaxTokens}
            onChange={setLlmMaxTokens}
            placeholder="800"
          />
          <InputField
            label="Temperature"
            value={llmTemperature}
            onChange={setLlmTemperature}
            placeholder="0.3"
          />
        </div>
        <button
          onClick={saveLLM}
          className="px-4 py-2 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600"
        >
          Save LLM Config
        </button>
      </div>
    </div>
  );
}

// ---- Params Tab ----

function ParamsTab({
  status,
  onError,
  onSuccess,
}: {
  status: SystemStatus | null;
  onError: (e: string | null) => void;
  onSuccess: (msg: string) => void;
}) {
  const [params, setParams] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (status?.params) {
      const mapped: Record<string, string> = {};
      for (const [k, v] of Object.entries(status.params)) {
        mapped[k] = String(v);
      }
      setParams(mapped);
    }
  }, [status]);

  const handleChange = (key: string, value: string) => {
    setParams((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  };

  const handleSave = async () => {
    try {
      // Convert string values back to numbers where possible
      const converted: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(params)) {
        if (v === "true") converted[k] = true;
        else if (v === "false") converted[k] = false;
        else if (/^\d+$/.test(v)) converted[k] = parseInt(v);
        else if (/^\d+\.\d+$/.test(v)) converted[k] = parseFloat(v);
        else converted[k] = v;
      }
      await api.updateParams(converted);
      setDirty(false);
      onSuccess("Parameters saved");
    } catch (e) {
      onError(String(e));
    }
  };

  const paramLabels: Record<string, string> = {
    volume_ratio_window: "History window (trading days)",
    intraday_signal_window_minutes: "Intraday signal window (min)",
    intraday_baseline_minutes: "Intraday baseline (min)",
    intraday_baseline_method: "Baseline method (mean/median)",
    intraday_alert_threshold: "Intraday alert threshold",
    alert_threshold: "Alert threshold (high)",
    shrink_threshold: "Shrink threshold (low)",
  };

  const keys = Object.keys(params);

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
      <h3 className="text-base font-medium text-gray-700 border-b border-gray-100 pb-2">
        Algorithm Parameters
      </h3>
      <p className="text-xs text-gray-400">
        Adjust the volume ratio detection algorithm. Changes take effect on the
        next scan.
      </p>
      {keys.length > 0 ? (
        <div className="space-y-3">
          {keys.map((k) => (
            <div key={k} className="flex items-center gap-3">
              <label className="text-sm text-gray-600 w-56 shrink-0">
                {paramLabels[k] || k}
              </label>
              <input
                type="text"
                value={params[k]}
                onChange={(e) => handleChange(k, e.target.value)}
                className="flex-1 px-3 py-1.5 text-sm border border-gray-200 rounded-md focus:outline-none focus:border-blue-400 font-mono"
              />
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-gray-400">No parameters configured</p>
      )}
      <button
        onClick={handleSave}
        disabled={!dirty}
        className="px-4 py-2 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600 disabled:opacity-50"
      >
        Save Parameters
      </button>
    </div>
  );
}

// ---- Notifications Tab ----

function NotificationsTab({
  onError,
  onSuccess,
}: {
  onError: (e: string | null) => void;
  onSuccess: (msg: string) => void;
}) {
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);

  const [feishuAppId, setFeishuAppId] = useState("");
  const [feishuAppSecret, setFeishuAppSecret] = useState("");
  const [feishuChatId, setFeishuChatId] = useState("");
  const [feishuWebhook, setFeishuWebhook] = useState("");

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const cfg = await api.config();
        setConfig(cfg);
        const feishu = (cfg.feishu || {}) as Record<string, unknown>;
        setFeishuAppId(String(feishu.app_id || ""));
        setFeishuChatId(String(feishu.chat_id || ""));
        setFeishuWebhook(String(feishu.webhook_url || ""));
      } catch (e) {
        onError(String(e));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const saveFeishu = async () => {
    try {
      await api.updateFeishu({
        app_id: feishuAppId,
        app_secret: feishuAppSecret || undefined,
        chat_id: feishuChatId,
        webhook_url: feishuWebhook,
      });
      onSuccess("Feishu config saved");
    } catch (e) {
      onError(String(e));
    }
  };

  const feishuConfigured = Boolean(
    ((config.feishu || {}) as Record<string, unknown>).app_id
  );

  if (loading) {
    return (
      <div className="text-center text-gray-400 py-8">Loading config...</div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Desktop notifications info */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-3">
        <h3 className="text-base font-medium text-gray-700 border-b border-gray-100 pb-2">
          Desktop Notifications
        </h3>
        <div className="flex items-center gap-2 text-sm">
          <span className="w-2 h-2 rounded-full bg-green-400" />
          <span className="text-gray-600">
            macOS native notifications are always active. Alerts appear in
            Notification Center.
          </span>
        </div>
      </div>

      {/* Feishu */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-medium text-gray-700">
            Feishu (Optional)
          </h3>
          <span
            className={`text-xs px-2 py-0.5 rounded ${
              feishuConfigured
                ? "bg-green-100 text-green-700"
                : "bg-gray-100 text-gray-500"
            }`}
          >
            {feishuConfigured ? "Configured" : "Not configured"}
          </span>
        </div>
        <p className="text-xs text-gray-400">
          Dual-channel alerts: desktop + Feishu. Leave empty to use desktop
          notifications only.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <InputField
            label="App ID"
            value={feishuAppId}
            onChange={setFeishuAppId}
            placeholder="cli_xxxxx"
          />
          <InputField
            label="App Secret"
            value={feishuAppSecret}
            onChange={setFeishuAppSecret}
            placeholder="Leave empty to keep current"
            type="password"
          />
          <InputField
            label="Chat ID"
            value={feishuChatId}
            onChange={setFeishuChatId}
            placeholder="oc_xxxxx"
          />
          <InputField
            label="Webhook URL"
            value={feishuWebhook}
            onChange={setFeishuWebhook}
            placeholder="https://open.feishu.cn/..."
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={saveFeishu}
            className="px-4 py-2 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600"
          >
            Save Feishu Config
          </button>
          {feishuConfigured && (
            <button
              onClick={async () => {
                try {
                  await api.updateFeishu({
                    app_id: "",
                    app_secret: "",
                    chat_id: "",
                    webhook_url: "",
                  });
                  setFeishuAppId("");
                  setFeishuAppSecret("");
                  setFeishuChatId("");
                  setFeishuWebhook("");
                  onSuccess("Feishu config cleared");
                } catch (e) {
                  onError(String(e));
                }
              }}
              className="px-4 py-2 text-sm text-red-600 border border-red-200 rounded-md hover:bg-red-50"
            >
              Clear Feishu
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- Shared Input ----

function InputField({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <div>
      <label className="text-xs font-medium text-gray-500 block mb-1">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-1.5 text-sm border border-gray-200 rounded-md focus:outline-none focus:border-blue-400"
      />
    </div>
  );
}
