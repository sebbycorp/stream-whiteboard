//! PNG export helpers — write board captures under Pictures/StreamWhiteboard.

use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

/// Decode standard base64 (no padding quirks beyond what we emit from canvas).
fn decode_base64(input: &str) -> Result<Vec<u8>, String> {
    const TABLE: &[u8] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::with_capacity(input.len() * 3 / 4);
    let mut buf: u32 = 0;
    let mut bits: i8 = -8;
    for &c in input.as_bytes() {
        if c == b'=' {
            break;
        }
        if c.is_ascii_whitespace() {
            continue;
        }
        let val = TABLE
            .iter()
            .position(|&x| x == c)
            .ok_or_else(|| format!("invalid base64 byte: {c}"))? as u32;
        buf = (buf << 6) | val;
        bits += 6;
        if bits >= 0 {
            out.push((buf >> bits) as u8);
            bits -= 8;
        }
    }
    Ok(out)
}

fn pictures_dir() -> Option<PathBuf> {
    // Prefer the real user Pictures folder (macOS / Linux / Windows-ish).
    if let Ok(home) = std::env::var("HOME") {
        let p = PathBuf::from(home).join("Pictures");
        if p.is_dir() || fs::create_dir_all(&p).is_ok() {
            return Some(p);
        }
    }
    dirs_fallback()
}

fn dirs_fallback() -> Option<PathBuf> {
    // Last resort: current user's home or temp.
    let base = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    Some(base.join("Pictures"))
}

/// `~/Pictures/StreamWhiteboard` (created on demand).
pub fn export_dir() -> Result<PathBuf, String> {
    let base = pictures_dir().ok_or_else(|| "could not resolve Pictures directory".to_string())?;
    let dir = base.join("StreamWhiteboard");
    fs::create_dir_all(&dir).map_err(|e| format!("create export dir: {e}"))?;
    Ok(dir)
}

fn timestamp_slug() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Local-ish sortable name without chrono dep: epoch + process-local.
    format!("{secs}")
}

fn sanitize_label(label: &str) -> String {
    let mut s: String = label
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect();
    while s.contains("--") {
        s = s.replace("--", "-");
    }
    let s = s.trim_matches('-').to_string();
    if s.is_empty() {
        "board".into()
    } else {
        s.chars().take(48).collect()
    }
}

/// Write PNG bytes (base64 from canvas) to the export folder. Returns absolute path.
pub fn save_png_base64(png_base64: &str, label: Option<&str>) -> Result<String, String> {
    let bytes = decode_base64(png_base64.trim())?;
    if bytes.len() < 8 || &bytes[0..8] != b"\x89PNG\r\n\x1a\n" {
        return Err("payload is not a PNG".into());
    }
    let dir = export_dir()?;
    let tag = sanitize_label(label.unwrap_or("board"));
    let name = format!("{tag}-{}.png", timestamp_slug());
    let path = dir.join(name);
    fs::write(&path, bytes).map_err(|e| format!("write png: {e}"))?;
    Ok(path.to_string_lossy().into_owned())
}

/// Reveal the export folder in Finder (macOS) or open it on other platforms.
pub fn reveal_export_dir() -> Result<String, String> {
    let dir = export_dir()?;
    let path = dir.to_string_lossy().into_owned();
    #[cfg(target_os = "macos")]
    {
        let status = Command::new("open")
            .arg(&path)
            .status()
            .map_err(|e| format!("open Finder: {e}"))?;
        if !status.success() {
            return Err("open failed".into());
        }
    }
    #[cfg(target_os = "linux")]
    {
        let _ = Command::new("xdg-open").arg(&path).status();
    }
    #[cfg(target_os = "windows")]
    {
        let _ = Command::new("explorer").arg(&path).status();
    }
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_strips_junk() {
        assert_eq!(sanitize_label("hello world!"), "hello-world");
        assert_eq!(sanitize_label("///"), "board");
        assert_eq!(sanitize_label("auto-clear"), "auto-clear");
    }

    #[test]
    fn decode_simple_base64() {
        // "hi" in base64
        let v = decode_base64("aGk=").unwrap();
        assert_eq!(v, b"hi");
    }

    #[test]
    fn rejects_non_png() {
        let b64 = "aGk="; // "hi"
        let err = save_png_base64(b64, Some("x")).unwrap_err();
        assert!(err.contains("PNG"), "{err}");
    }

    #[test]
    fn writes_minimal_png() {
        // 1×1 transparent PNG
        let b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
        let path = save_png_base64(b64, Some("test-unit")).unwrap();
        assert!(std::path::Path::new(&path).is_file(), "{path}");
        let meta = fs::metadata(&path).unwrap();
        assert!(meta.len() > 20);
        let _ = fs::remove_file(&path);
    }
}
