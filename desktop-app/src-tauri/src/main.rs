#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod config;
mod export;
mod ndjson;
mod tablet;

use serde::Serialize;
use std::net::Shutdown;
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Manager, State};

#[derive(Serialize)]
struct TargetDto {
    host: String,
    port: u16,
}

#[tauri::command]
fn get_tablet(shared: State<Arc<tablet::Shared>>) -> TargetDto {
    let t = shared.target.lock().unwrap().clone();
    TargetDto {
        host: t.host,
        port: t.port,
    }
}

/// Current link snapshot. The UI calls this after registering listeners so a
/// connection that came up during WebView startup is not shown as "starting…".
#[tauri::command]
fn get_status(shared: State<Arc<tablet::Shared>>) -> tablet::LinkState {
    shared.snapshot.lock().unwrap().clone()
}

#[tauri::command]
fn set_tablet(app: AppHandle, host: String, port: u16, shared: State<Arc<tablet::Shared>>) {
    config::save(&app, &host, port);
    {
        let mut t = shared.target.lock().unwrap();
        t.host = host;
        t.port = port;
    }
    // Drop the current connection so the reader loop reconnects to the new target.
    if let Some(sock) = shared.sock.lock().unwrap().as_ref() {
        let _ = sock.shutdown(Shutdown::Both);
    }
}

/// Desktop → tablet control: clear | undo | rotate (landscape toggle).
/// Tablet applies the action and rebroadcasts so the app stays in sync via
/// the normal clear/undo/hello events.
#[tauri::command]
fn tablet_cmd(cmd: String, shared: State<Arc<tablet::Shared>>) -> Result<(), String> {
    tablet::send_cmd(&shared, &cmd)
}

/// Save a base64-encoded PNG from the viewer into ~/Pictures/StreamWhiteboard.
/// Returns the absolute path written.
#[tauri::command]
fn save_png(png_base64: String, label: Option<String>) -> Result<String, String> {
    export::save_png_base64(&png_base64, label.as_deref())
}

#[tauri::command]
fn get_export_dir() -> Result<String, String> {
    export::export_dir().map(|p| p.to_string_lossy().into_owned())
}

#[tauri::command]
fn open_export_dir() -> Result<String, String> {
    export::reveal_export_dir()
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let handle = app.handle().clone();
            let target = match config::load(&handle) {
                Some(p) => tablet::Target {
                    host: p.host,
                    port: p.port,
                },
                None => tablet::Target {
                    host: "172.16.10.175".to_string(),
                    port: 27182,
                },
            };
            let snapshot = tablet::LinkState::connecting(&target);
            let shared = Arc::new(tablet::Shared {
                target: Mutex::new(target),
                sock: Mutex::new(None),
                snapshot: Mutex::new(snapshot),
            });
            app.manage(shared.clone());

            let thread_handle = handle.clone();
            std::thread::spawn(move || tablet::run_loop(thread_handle, shared));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_tablet,
            set_tablet,
            get_status,
            tablet_cmd,
            save_png,
            get_export_dir,
            open_export_dir
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
