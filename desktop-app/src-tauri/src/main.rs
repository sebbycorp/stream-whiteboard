#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod config;
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
            let shared = Arc::new(tablet::Shared {
                target: Mutex::new(target),
                sock: Mutex::new(None),
            });
            app.manage(shared.clone());

            let thread_handle = handle.clone();
            std::thread::spawn(move || tablet::run_loop(thread_handle, shared));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_tablet, set_tablet])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
