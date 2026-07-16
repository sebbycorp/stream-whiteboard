use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tauri::Manager;

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
pub struct Persisted {
    pub host: String,
    pub port: u16,
}

fn config_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|d| d.join("tablet.json"))
}

/// Load the saved tablet target, or `None` if nothing valid is stored yet.
pub fn load(app: &tauri::AppHandle) -> Option<Persisted> {
    let path = config_path(app)?;
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

/// Persist the tablet target. Best-effort: failures are ignored.
pub fn save(app: &tauri::AppHandle, host: &str, port: u16) {
    if let Some(path) = config_path(app) {
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let value = Persisted {
            host: host.to_string(),
            port,
        };
        if let Ok(text) = serde_json::to_string(&value) {
            let _ = std::fs::write(path, text);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn persisted_round_trips_through_json() {
        let original = Persisted {
            host: "172.16.10.175".to_string(),
            port: 27182,
        };
        let text = serde_json::to_string(&original).unwrap();
        let back: Persisted = serde_json::from_str(&text).unwrap();
        assert_eq!(original, back);
    }
}
