use crate::ndjson::LineBuffer;
use serde::Serialize;
use std::io::Read;
use std::net::{Shutdown, TcpStream, ToSocketAddrs};
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

        match connect(&target) {
            Some(stream) => {
                let _ = stream.set_nodelay(true);
                let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
                *shared.sock.lock().unwrap() = stream.try_clone().ok();
                emit_status(&app, "connected", &target);
                read_stream(&app, stream);
                *shared.sock.lock().unwrap() = None;
                emit_status(&app, "reconnecting", &target);
            }
            None => {
                emit_status(&app, "reconnecting", &target);
            }
        }

        // Back off before the next attempt (or after a set_tablet-triggered drop).
        std::thread::sleep(Duration::from_secs(2));
    }
}

/// Resolve the target and attempt a TCP connection with a bounded timeout so an
/// unreachable host (e.g. the default IP on first launch) can't block a pending
/// address change for the OS default connect timeout.
fn connect(target: &Target) -> Option<TcpStream> {
    let addrs = (target.host.as_str(), target.port).to_socket_addrs().ok()?;
    for addr in addrs {
        if let Ok(stream) = TcpStream::connect_timeout(&addr, Duration::from_secs(5)) {
            return Some(stream);
        }
    }
    None
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
