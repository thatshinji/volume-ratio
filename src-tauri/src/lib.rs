mod python_manager;
mod commands;

use python_manager::PythonManager;
use std::path::PathBuf;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let project_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_path_buf();
    let python_mgr = PythonManager::new(project_dir);

    // Start Python API server
    if let Err(e) = python_mgr.start() {
        eprintln!("[tauri] Failed to start Python API server: {}", e);
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .manage(python_mgr)
        .invoke_handler(tauri::generate_handler![
            commands::proxy_get,
            commands::proxy_post,
            commands::proxy_put,
            commands::proxy_delete,
            commands::python_status,
        ])
        .setup(|app| {
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                // Wait for Python API server to be ready
                let client = reqwest::Client::new();
                let url = "http://127.0.0.1:9720/api/health";
                for i in 0..30 {
                    match client.get(url).timeout(std::time::Duration::from_secs(1)).send().await {
                        Ok(resp) if resp.status().is_success() => {
                            println!("[tauri] Python API server ready after {}ms", i * 500);
                            break;
                        }
                        _ => {}
                    }
                    tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;
                }
                listen_alerts(app_handle).await;
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

async fn listen_alerts(app: tauri::AppHandle) {
    use futures_util::StreamExt;
    use tauri_plugin_notification::NotificationExt;

    let url = "ws://127.0.0.1:9720/ws/alerts";

    loop {
        match tokio_tungstenite::connect_async(url).await {
            Ok((ws_stream, _)) => {
                println!("[tauri] Connected to alerts WebSocket");
                let (_, mut read) = ws_stream.split();

                while let Some(msg) = read.next().await {
                    match msg {
                        Ok(tokio_tungstenite::tungstenite::Message::Text(text)) => {
                            if let Ok(data) = serde_json::from_str::<serde_json::Value>(&text) {
                                if data.get("type").and_then(|t| t.as_str()) == Some("alert") {
                                    let ticker = data.get("ticker").and_then(|v| v.as_str()).unwrap_or("");
                                    let name = data.get("name").and_then(|v| v.as_str()).unwrap_or("");
                                    let ratio = data.get("ratio").and_then(|v| v.as_f64()).unwrap_or(0.0);
                                    let signal = data.get("signal").and_then(|v| v.as_str()).unwrap_or("");
                                    let change = data.get("change_pct").and_then(|v| v.as_f64()).unwrap_or(0.0);
                                    let analysis = data.get("analysis").and_then(|v| v.as_str());

                                    let icon = if ratio > 5.0 { "🔥🔥" } else if ratio > 2.0 { "🔥" } else if ratio < 0.6 { "⚠️" } else { "📊" };
                                    let direction = if change >= 0.0 { "↑" } else { "↓" };
                                    let title = format!("{} {} - {}", icon, ticker, name);
                                    let mut body = format!("{}{:.2}% | 量比 {:.2} | {}", direction, change.abs(), ratio, signal);
                                    if let Some(a) = analysis {
                                        body.push_str(&format!("\nAI: {}", a));
                                    }

                                    let _ = app.notification()
                                        .builder()
                                        .title(&title)
                                        .body(&body)
                                        .show();
                                }
                            }
                        }
                        Ok(_) => {}
                        Err(_) => break,
                    }
                }
                println!("[tauri] Alerts WebSocket disconnected, reconnecting...");
            }
            Err(e) => {
                eprintln!("[tauri] Failed to connect alerts WebSocket: {}", e);
            }
        }
        tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
    }
}
