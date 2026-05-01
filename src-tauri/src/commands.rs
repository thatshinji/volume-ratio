use tauri::State;
use crate::python_manager::PythonManager;

#[tauri::command]
pub async fn proxy_get(path: String, state: State<'_, PythonManager>) -> Result<String, String> {
    if !state.is_running() {
        return Err("Python API server is not running".to_string());
    }
    let url = format!("http://127.0.0.1:9720{}", path);
    let resp = reqwest::get(&url).await.map_err(|e| e.to_string())?;
    resp.text().await.map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn proxy_post(path: String, body: Option<String>, state: State<'_, PythonManager>) -> Result<String, String> {
    if !state.is_running() {
        return Err("Python API server is not running".to_string());
    }
    let url = format!("http://127.0.0.1:9720{}", path);
    let client = reqwest::Client::new();
    let mut req = client.post(&url);
    if let Some(b) = body {
        req = req.header("Content-Type", "application/json").body(b);
    }
    let resp = req.send().await.map_err(|e| e.to_string())?;
    resp.text().await.map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn proxy_delete(path: String, state: State<'_, PythonManager>) -> Result<String, String> {
    if !state.is_running() {
        return Err("Python API server is not running".to_string());
    }
    let url = format!("http://127.0.0.1:9720{}", path);
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(|e| e.to_string())?;
    resp.text().await.map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn proxy_put(path: String, body: Option<String>, state: State<'_, PythonManager>) -> Result<String, String> {
    if !state.is_running() {
        return Err("Python API server is not running".to_string());
    }
    let url = format!("http://127.0.0.1:9720{}", path);
    let client = reqwest::Client::new();
    let mut req = client.put(&url);
    if let Some(b) = body {
        req = req.header("Content-Type", "application/json").body(b);
    }
    let resp = req.send().await.map_err(|e| e.to_string())?;
    resp.text().await.map_err(|e| e.to_string())
}

#[tauri::command]
pub fn python_status(state: State<PythonManager>) -> bool {
    state.is_running()
}
