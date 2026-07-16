# Stream Whiteboard — double-click Mac app (design)

**Date:** 2026-07-16
**Status:** Approved for planning

## Goal

Turn the working MVP (Python bridge + browser viewer) into a **double-click macOS
app**: no terminal, no typing a tablet IP, no Python install. Ink must keep
appearing **live** as the user writes — with no added lag versus today.

Scope is a single user on their own Mac. **No Apple Developer ID / notarization** —
the app runs locally (first launch: right-click → Open once to clear Gatekeeper,
then normal double-click).

## Non-goals (v1)

- Signed/notarized `.dmg` for distribution to other machines.
- Windows / Linux packaging (kept possible by the Tauri choice, not built now).
- PNG full-page resync, multi-device mirroring, session export/save.
- Any change to the tablet-side emit code (lives in `k8s-goose`).

## Chosen approach: Tauri

A tiny Rust shell hosts a WebView running the existing `viewer.html`. Rust connects
to the tablet over TCP directly and pushes each NDJSON line into the WebView via
Tauri's in-process event IPC.

**Why Tauri over the alternatives**

- Reuses `viewer.html`'s canvas renderer verbatim → fast to build, no rendering
  regressions.
- Ships as one `.app`, no Python and no terminal — the double-click requested.
- Drops the localhost WebSocket hop *and* the separate `bridge.py` process.
- Latency is dominated by Wi-Fi + the tablet's ~40 Hz coalescing (both upstream of
  the Mac); the removed hop is sub-millisecond, so no shell choice is meaningfully
  "faster" to the pen. Tauri wins on product + build speed, not latency.
- SwiftUI would force a full canvas rewrite and lock to macOS for an invisible
  latency gain; a Python-bundled `.app` keeps extra hops and fights notarization.

## Architecture

```
reMarkable 2 (TCP :27182, NDJSON)
        │  Wi-Fi / USB
        ▼
┌─────────────────────────────────┐
│  Stream Whiteboard.app (Tauri)  │
│  ┌───────────────────────────┐  │
│  │ Rust core                 │  │
│  │ • TCP client → tablet     │  │
│  │ • NDJSON line parser      │  │
│  │ • auto-reconnect (2s)     │  │
│  │ • emit("stroke", line) ───┼──┼─┐  Tauri IPC event (in-process)
│  └───────────────────────────┘  │ │
│  ┌───────────────────────────┐  │ │
│  │ WebView (viewer.html)     │◄─┼─┘
│  │ • same canvas renderer    │  │
│  │ • listen('stroke')        │  │
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

## Components

### 1. Rust core (`desktop-app/src-tauri/`)

- Connects to `tablet_host:tablet_port` with `TCP_NODELAY` set.
- Reads bytes, splits on `\n` (NDJSON), buffers partial lines across reads.
- Lightly validates each line parses as JSON; skips malformed lines (matches
  `bridge.py`).
- `emit("stroke", <raw json line string>)` per valid line.
- Owns connection state; on drop/unreachable, retries every 2 s and emits a
  `status` event (`connecting` / `connected` / `reconnecting`).
- Reads tablet host/port from app config; exposes a `set_tablet(host, port)`
  command that reconnects.

### 2. Viewer (`viewer.html`, adapted copy in the app)

- Same canvas/drawing logic as today.
- Replace WebSocket `onmessage` with Tauri `listen('stroke', e => handleLine(e.payload))`.
- Add a small status line driven by the `status` event
  (e.g. "Connected · 172.16.10.175" / "Reconnecting…").
- Add a minimal settings input for tablet IP/port that calls `set_tablet`.

### 3. Settings / config

- Tablet IP/port persisted in Tauri app config (survives restarts).
- First-launch default = `172.16.10.175:27182` (from `.env.example`).

## Data flow & liveness

Tablet stroke → Rust TCP read → line parse → `emit("stroke")` → JS `listen` →
canvas draw. Fully in-process; no localhost socket. Each valid line is drawn on
arrival — no batching beyond what the tablet already does.

## Error handling

| Case | Behaviour |
|------|-----------|
| Tablet unreachable / connection drops | Retry every 2 s; status shows "Reconnecting…"; no manual restart. |
| Malformed / non-JSON line | Skipped silently (as in `bridge.py`). |
| USB mode | User runs the SSH tunnel, sets tablet IP to `127.0.0.1` in settings. Documented. |
| Unknown event type `t` | Viewer ignores it (existing forward-compat rule). |

## Packaging & distribution

- Build: `pnpm tauri build` → `Stream Whiteboard.app` (+ `.dmg`, unused for now).
- No signing. Document the one-time right-click → Open step for Gatekeeper.
- `bridge.py` + `viewer.html` remain in the repo as the zero-install browser
  fallback (unchanged).

## Testing

- Rust unit test for the NDJSON line splitter: partial buffer across reads,
  multiple lines in one packet, blank lines, trailing partial line.
- **Mock tablet** script: replays sample NDJSON on `:27182` so the whole app can be
  exercised end-to-end without the physical reMarkable.
- Manual: run the mock tablet, launch the app, confirm live strokes + reconnect
  after killing/restarting the mock.

## Repo layout impact

```
stream-whiteboard/
├── desktop/            # unchanged: bridge.py + viewer.html (browser fallback)
├── desktop-app/        # NEW: Tauri app
│   ├── src/            # viewer.html adapted + status/settings UI
│   └── src-tauri/      # Rust core (TCP, reconnect, emit)
└── tools/
    └── mock_tablet.py  # NEW: NDJSON replay for testing
```

## Open questions

None blocking. Future phases (PNG resync, Windows build, session export) are
tracked in the README roadmap and out of scope here.
