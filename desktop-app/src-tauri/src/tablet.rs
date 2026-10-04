use crate::ndjson::LineBuffer;
use serde::Serialize;
use std::io::{Read, Write};
use std::net::{Shutdown, TcpStream, ToSocketAddrs};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

#[derive(Clone)]
pub struct Target {
    pub host: String,
    pub port: u16,
}

/// Queryable snapshot of the tablet link. The reader loop updates this so the
/// WebView can ask "are we already connected?" after it missed the first events.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LinkState {
    pub state: String, // "connecting" | "connected" | "reconnecting"
    pub host: String,
    pub port: u16,
    pub error: Option<String>,
    pub last_hello: Option<String>,
}

impl LinkState {
    pub fn connecting(target: &Target) -> Self {
        Self {
            state: "connecting".into(),
            host: target.host.clone(),
            port: target.port,
            error: None,
            last_hello: None,
        }
    }

    pub fn set_connecting(&mut self, target: &Target) {
        self.state = "connecting".into();
        self.host = target.host.clone();
        self.port = target.port;
    }

    pub fn set_connected(&mut self, target: &Target) {
        self.state = "connected".into();
        self.host = target.host.clone();
        self.port = target.port;
        self.error = None;
    }

    pub fn set_reconnecting(&mut self, target: &Target, error: Option<String>) {
        self.state = "reconnecting".into();
        self.host = target.host.clone();
        self.port = target.port;
        if error.is_some() {
            self.error = error;
        }
    }

    pub fn note_line(&mut self, line: &str) {
        if let Some(hello) = hello_from_line(line) {
            self.last_hello = Some(hello);
        }
    }
}

/// If `line` is a valid hello event, return it; otherwise None.
pub fn hello_from_line(line: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    match v.get("t").and_then(|t| t.as_str()) {
        Some("hello") => Some(line.to_string()),
        _ => None,
    }
}

/// Shared, mutable connection state managed by Tauri and read by the reader thread.
pub struct Shared {
    pub target: Mutex<Target>,
    /// Write half / clone used to send desktop→tablet control commands.
    pub sock: Mutex<Option<TcpStream>>,
    pub snapshot: Mutex<LinkState>,
}

/// Send a control command to the tablet (`clear`, `undo`, `rotate`, `resync`).
/// Returns Ok(()) if written, Err if not connected or write failed.
pub fn send_cmd(shared: &Shared, cmd: &str) -> Result<(), String> {
    let safe: String = cmd
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
        .take(32)
        .collect();
    if safe.is_empty() {
        return Err("empty command".into());
    }
    let line = format!("{{\"t\":\"cmd\",\"cmd\":\"{safe}\"}}\n");
    let mut guard = shared.sock.lock().unwrap();
    let sock = guard
        .as_mut()
        .ok_or_else(|| "not connected to tablet".to_string())?;
    sock.write_all(line.as_bytes())
        .map_err(|e| format!("write cmd: {e}"))?;
    let _ = sock.flush();
    Ok(())
}

#[derive(Clone, Serialize)]
struct StatusEvent {
    state: String, // "connecting" | "connected" | "reconnecting"
    host: String,
    port: u16,
    error: Option<String>,
}

fn publish_status(app: &AppHandle, shared: &Shared, update: impl FnOnce(&mut LinkState)) {
    let snap = {
        let mut s = shared.snapshot.lock().unwrap();
        update(&mut s);
        s.clone()
    };
    let _ = app.emit(
        "status",
        StatusEvent {
            state: snap.state,
            host: snap.host,
            port: snap.port,
            error: snap.error,
        },
    );
}

/// Reconnecting read loop. Runs forever on a dedicated thread.
pub fn run_loop(app: AppHandle, shared: Arc<Shared>) {
    loop {
        let target = shared.target.lock().unwrap().clone();
        publish_status(&app, &shared, |s| s.set_connecting(&target));

        match connect(&target) {
            Ok(stream) => {
                let _ = stream.set_nodelay(true);
                let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
                *shared.sock.lock().unwrap() = stream.try_clone().ok();
                publish_status(&app, &shared, |s| s.set_connected(&target));
                let drop_reason = read_stream(&app, &shared, stream);
                *shared.sock.lock().unwrap() = None;
                publish_status(&app, &shared, |s| {
                    s.set_reconnecting(&target, Some(drop_reason))
                });
            }
            Err(err) => {
                publish_status(&app, &shared, |s| s.set_reconnecting(&target, Some(err)));
            }
        }

        // Back off before the next attempt (or after a set_tablet-triggered drop).
        std::thread::sleep(Duration::from_secs(2));
    }
}

/// Resolve the target and attempt a TCP connection with a bounded timeout so an
/// unreachable host (e.g. the default IP on first launch) can't block a pending
/// address change for the OS default connect timeout.
fn connect(target: &Target) -> Result<TcpStream, String> {
    let addrs = (target.host.as_str(), target.port)
        .to_socket_addrs()
        .map_err(|e| format!("resolve {}:{}: {e}", target.host, target.port))?;
    let mut last = format!("no addresses for {}:{}", target.host, target.port);
    for addr in addrs {
        match TcpStream::connect_timeout(&addr, Duration::from_secs(5)) {
            Ok(stream) => return Ok(stream),
            Err(e) => last = format!("{addr}: {e}"),
        }
    }
    Err(last)
}

fn read_stream(app: &AppHandle, shared: &Shared, mut stream: TcpStream) -> String {
    let mut lb = LineBuffer::new();
    // Big reads: mirror keyframes and `ans` bitmaps are single lines of
    // hundreds of KB. The LineBuffer itself has no line-length limit.
    let mut buf = vec![0u8; 64 * 1024];
    let reason = loop {
        match stream.read(&mut buf) {
            Ok(0) => break "tablet closed the connection".into(),
            Ok(n) => {
                for line in lb.push(&buf[..n]) {
                    // Only forward lines that parse as JSON (matches bridge.py behaviour).
                    if serde_json::from_str::<serde_json::Value>(&line).is_ok() {
                        shared.snapshot.lock().unwrap().note_line(&line);
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
            Err(e) => break format!("read: {e}"),
        }
    };
    let _ = stream.shutdown(Shutdown::Both);
    reason
}

#[cfg(test)]
mod tests {
    use super::*;

    fn target() -> Target {
        Target {
            host: "172.16.10.175".into(),
            port: 27182,
        }
    }

    #[test]
    fn hello_line_is_detected() {
        let line = r#"{"t":"hello","proto":1,"w":1404,"h":1872,"page":0,"pages":1}"#;
        assert_eq!(hello_from_line(line).as_deref(), Some(line));
    }

    #[test]
    fn non_hello_line_is_ignored() {
        assert_eq!(hello_from_line(r#"{"t":"down","id":1}"#), None);
        assert_eq!(hello_from_line("not json"), None);
    }

    #[test]
    fn snapshot_connecting_then_connected_clears_error() {
        let t = target();
        let mut s = LinkState::connecting(&t);
        assert_eq!(s.state, "connecting");
        assert_eq!(s.host, "172.16.10.175");
        assert_eq!(s.port, 27182);
        assert!(s.error.is_none());
        assert!(s.last_hello.is_none());

        s.set_reconnecting(&t, Some("timed out".into()));
        assert_eq!(s.state, "reconnecting");
        assert_eq!(s.error.as_deref(), Some("timed out"));

        s.set_connected(&t);
        assert_eq!(s.state, "connected");
        assert!(s.error.is_none());
    }

    #[test]
    fn snapshot_json_uses_camel_case_for_the_webview() {
        let t = target();
        let mut s = LinkState::connecting(&t);
        s.note_line(r#"{"t":"hello","w":1,"h":2}"#);
        let text = serde_json::to_string(&s).unwrap();
        assert!(text.contains("\"lastHello\""), "{text}");
        assert!(!text.contains("last_hello"), "{text}");
    }

    #[test]
    fn snapshot_retains_hello_across_reconnect() {
        let t = target();
        let mut s = LinkState::connecting(&t);
        let line = r#"{"t":"hello","proto":1,"w":1404,"h":1872,"page":0,"pages":1}"#;
        s.note_line(line);
        s.note_line(r#"{"t":"down","id":1}"#);
        assert_eq!(s.last_hello.as_deref(), Some(line));
        s.set_reconnecting(&t, Some("eof".into()));
        assert_eq!(s.last_hello.as_deref(), Some(line));
    }
}
