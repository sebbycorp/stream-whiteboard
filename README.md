# Stream Whiteboard

Live companion for **SebbyCorp Notepad** on reMarkable 2.

Write on the tablet → ink appears in near real time on your **Mac** (and Windows/Linux browser).

```
reMarkable diary (TCP :27182 NDJSON)
        │
        ▼
  desktop/bridge.py  ──WebSocket :27183──►  browser canvas
```

Tablet stream server lives in **[k8s-goose](https://github.com/sebbycorp/k8s-goose)**  
(`remarkable-diary/takeover/stream.c` + hooks in `diary.c`).

This repo is the **desktop app / viewer** product.

---

## Quick start (Mac)

### 1. Enable streaming on the tablet

In `/home/root/diary.conf`:

```
stream=1
```

Restart the diary service. Logs should show:

```
[stream] listening on 0.0.0.0:27182
```

### 2. Run the desktop bridge

```bash
cd desktop
python3 bridge.py --tablet 172.16.10.175 --serve --open
```

USB:

```bash
# terminal 1
ssh -L 27182:127.0.0.1:27182 root@10.11.99.1
# terminal 2
python3 bridge.py --tablet 127.0.0.1 --serve --open
```

Browser opens the viewer → **Connect** to `ws://127.0.0.1:27183`.

Requires **Python 3.9+** (stdlib only — no pip packages).

---

## Desktop app (double-click, no terminal)

A native macOS app lives in `desktop-app/` (Tauri). It talks TCP straight to the
tablet — no `bridge.py`, no browser.

### Build it (one-time)

Prerequisites: Rust (`rustup`), Xcode Command Line Tools (`xcode-select --install`),
and the Tauri CLI:

```bash
cargo install tauri-cli --version "^2.0" --locked
```

Generate the icon and build:

```bash
python3 tools/make_icon.py desktop-app/src-tauri/icon-src.png
cd desktop-app/src-tauri
cargo tauri icon icon-src.png
cargo tauri build
```

The app is written to
`desktop-app/src-tauri/target/release/bundle/macos/Stream Whiteboard.app`.
Drag it to `/Applications`.

### Use it

1. First launch: right-click the app → **Open** → **Open** (one-time Gatekeeper
   step, because the app is unsigned).
2. Enter your tablet's IP and port, click **Apply**. The setting is remembered.
3. Write on the tablet — ink appears live. It auto-reconnects if the link drops.

USB mode: run `ssh -L 27182:127.0.0.1:27182 root@10.11.99.1`, then set the host to
`127.0.0.1` in the app.

### Test without the tablet

```bash
python3 tools/mock_tablet.py   # fake stream on :27182
```

Then set the app's host to `127.0.0.1` and Apply.

The old `bridge.py` + browser flow still works and remains the zero-install fallback.

---

## Layout

```
stream-whiteboard/
├── README.md
├── PROTOCOL.md          # NDJSON contract with the tablet
├── docs/
│   └── ARCHITECTURE.md
└── desktop/
    ├── bridge.py        # TCP tablet → WebSocket + static server
    └── viewer.html      # canvas live view (Mac / Win / Linux browser)
```

---

## Protocol (summary)

Newline-delimited JSON from tablet → desktop. Examples:

```json
{"t":"hello","proto":1,"w":1404,"h":1872,"page":0,"pages":2}
{"t":"down","id":12,"tool":"pen","color":0,"width":1,"x":220,"y":400}
{"t":"move","id":12,"x":225,"y":404}
{"t":"up","id":12}
{"t":"clear"}
```

Full details: [PROTOCOL.md](./PROTOCOL.md).

---

## Roadmap

| Phase | Status |
|-------|--------|
| Browser viewer + Python bridge | ✅ MVP in this repo |
| Installable Mac app (Tauri) | ✅ double-click app in `desktop-app/` |
| Windows packaging | 🔜 |
| PNG resync / AI bitmaps | 🔜 |
| Meeting / OBS mode | 🔜 |

---

## Related

- Tablet app: https://github.com/sebbycorp/k8s-goose (`remarkable-diary/takeover`)
- Design notes: `remarkable-stuff` docs (live-stream design)
