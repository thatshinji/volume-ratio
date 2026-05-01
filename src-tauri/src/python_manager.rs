use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::path::PathBuf;

const API_PORT: u16 = 9720;

pub struct PythonManager {
    child: Mutex<Option<Child>>,
    project_dir: PathBuf,
}

impl PythonManager {
    pub fn new(project_dir: PathBuf) -> Self {
        Self {
            child: Mutex::new(None),
            project_dir,
        }
    }

    fn python_path(&self) -> PathBuf {
        self.project_dir.join(".venv").join("bin").join("python3")
    }

    fn api_server_path(&self) -> PathBuf {
        self.project_dir.join("scripts").join("api_server.py")
    }

    pub fn start(&self) -> Result<(), String> {
        let mut child_lock = self.child.lock().map_err(|e| e.to_string())?;

        if let Some(ref mut c) = *child_lock {
            // Check if still alive
            match c.try_wait() {
                Ok(Some(_)) => { *child_lock = None; }
                Ok(None) => return Ok(()), // already running
                Err(_) => { *child_lock = None; }
            }
        }

        let python = self.python_path();
        let api_server = self.api_server_path();

        if !python.exists() {
            return Err(format!("Python not found: {}", python.display()));
        }
        if !api_server.exists() {
            return Err(format!("API server not found: {}", api_server.display()));
        }

        let child = Command::new(&python)
            .arg(&api_server)
            .arg("--port")
            .arg(API_PORT.to_string())
            .current_dir(&self.project_dir)
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| format!("Failed to start Python API server: {}", e))?;

        let pid = child.id();
        *child_lock = Some(child);

        println!("[tauri] Python API server started (PID {})", pid);
        Ok(())
    }

    pub fn stop(&self) -> Result<(), String> {
        let mut child_lock = self.child.lock().map_err(|e| e.to_string())?;
        if let Some(ref mut child) = *child_lock {
            child.kill().map_err(|e| format!("Failed to kill Python process: {}", e))?;
            let _ = child.wait();
            *child_lock = None;
        }
        Ok(())
    }

    pub fn is_running(&self) -> bool {
        let child_lock = self.child.lock();
        match child_lock {
            Ok(mut c) => {
                if let Some(ref mut child) = *c {
                    match child.try_wait() {
                        Ok(Some(_)) => { *c = None; false }
                        Ok(None) => true,
                        Err(_) => { *c = None; false }
                    }
                } else {
                    false
                }
            }
            Err(_) => false,
        }
    }
}

impl Drop for PythonManager {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}
