use crate::ndjson::LineBuffer;
use serde::Serialize;
use std::io::Read;
use std::net::{Shutdown, TcpStream};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

#[derive(Clone)]
pub struct Target {
    pub host: String,
    pub port: u16,
}

/// Shared, mutable connection state managed by Tauri and read by the reader thread.
pub struct Shared {
    pub target: Mutex<Target>,
    pub sock: Mutex<Option<TcpStream>>,
}

#[derive(Clone, Serialize)]
struct StatusEvent {
    state: String, // "connecting" | "connected" | "reconnecting"
    host: String,
    port: u16,
}

fn emit_status(app: &AppHandle, state: &str, target: &Target) {
    let _ = app.emit(
        "status",
        StatusEvent {
            state: state.to_string(),
            host: target.host.clone(),
            port: target.port,
        },
    );
}

/// Reconnecting read loop. Runs forever on a dedicated thread.
pub fn run_loop(app: AppHandle, shared: Arc<Shared>) {
    loop {
        let target = shared.target.lock().unwrap().clone();
        emit_status(&app, "connecting", &target);

        match TcpStream::connect((target.host.as_str(), target.port)) {
            Ok(stream) => {
                let _ = stream.set_nodelay(true);
                let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
                *shared.sock.lock().unwrap() = stream.try_clone().ok();
                emit_status(&app, "connected", &target);
                read_stream(&app, stream);
                *shared.sock.lock().unwrap() = None;
                emit_status(&app, "reconnecting", &target);
            }
            Err(_) => {
                emit_status(&app, "reconnecting", &target);
            }
        }

        // Back off before the next attempt (or after a set_tablet-triggered drop).
        std::thread::sleep(Duration::from_secs(2));
    }
}

fn read_stream(app: &AppHandle, mut stream: TcpStream) {
    let mut lb = LineBuffer::new();
    let mut buf = [0u8; 4096];
    loop {
        match stream.read(&mut buf) {
            Ok(0) => break, // EOF / peer closed
            Ok(n) => {
                for line in lb.push(&buf[..n]) {
                    // Only forward lines that parse as JSON (matches bridge.py behaviour).
                    if serde_json::from_str::<serde_json::Value>(&line).is_ok() {
                        let _ = app.emit("stroke", line);
                    }
                }
            }
            Err(e)
                if e.kind() == std::io::ErrorKind::WouldBlock
                    || e.kind() == std::io::ErrorKind::TimedOut =>
            {
                continue; // read timeout — keep the connection open
            }
            Err(_) => break,
        }
    }
    let _ = stream.shutdown(Shutdown::Both);
}
