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

> **Note:** overlay mode needs `macOSPrivateApi` (Tauri) / the `macos-private-api`
> Cargo feature. It uses private Apple APIs, which is fine for this unsigned,
> self-distributed build but rules out Mac App Store submission.

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

### Streaming / export (Mac app)

| Shortcut / control | What it does |
|--------------------|--------------|
| **⌘S** / **Save** | Export the current board as PNG → `~/Pictures/StreamWhiteboard/` |
| **⌘C** / **Copy** | Copy the board image to the clipboard (paste into Keynote/Slack) |
| **Folder** | Open the export folder in Finder |
| **O** / **OBS** | Stream preset: fixed **810×1080** window, always-on-top, chrome hidden, edge-to-edge paper (no gray frame) for Window Capture |
| **G** / **Chroma** | Green paper (`#00B140`) for OBS chroma key |
| **D** / **Dark** | Black board with light ink (inverted palette). Remembered across launches |
| **T** / **Overlay** | Genuinely transparent window — ink floats over slides/desktop. No keying needed |
| **I** / **Click-thru** | Let mouse clicks pass through to the app underneath (overlay mode only) |
| **M** / **Meeting** | Hide chrome only (window size still free) |
| **Clear** / tablet clear / page flip | Auto-saves a PNG first so boards aren't lost |
| **⌘Z / Undo** | Undo last stroke on **tablet + app** (synced) |
| **⌘⌫ / Clear** | Erase-all on **tablet + app** (synced) |
| **R / Rotate** | Rotate the **view** 90° (app-side only, remembered; PNG exports follow). Use it when you physically turn the tablet and write along its long edge |

**Sync model — read this before trusting the buttons.** Sync is currently
**tablet → app only**. The tablet's rail Undo/Clear do stream to the app
(`diary.c` calls `stream_undo()` / `stream_clear()`).

The reverse direction is **not implemented on the device**: `stream.c`'s
`stream_poll()` reads desktop bytes into a buffer named `junk` purely to detect
disconnects and never parses them. So the app's **Clear and Undo do not affect
the tablet** (Clear only appears to work because the viewer wipes itself first).

**Rotate does not exist on the tablet at all** — `diary.c` has zero
`rotate`/`landscape` code and its canvas is welded to the fixed portrait
framebuffer (`canvas_w()` is just `vinfo.xres`). There is also no rotate button
in the tablet's tool palette. That's why **R rotates the app's view instead**.

Making app→tablet Clear/Undo real needs an NDJSON command parser in k8s-goose's
`stream.c` (it already links `cJSON.c`) plus a rebuild and device redeploy.

Esc exits click-through, then overlay, then OBS or meeting mode — in that order, so you can always get your clicks back. In OBS/meeting mode, move the cursor to the top-left corner to peek the menu.

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
| Meeting / OBS preset + PNG export | ✅ Mac app (Save/Copy, OBS size, chroma) |
| Sync clear / undo both ways | ⚠️ tablet→app only; device discards app→tablet cmds |
| Tablet-side rotate / landscape | ❌ not implemented on device; app rotates its own view |
| Windows packaging | 🔜 |
| PNG resync / AI bitmaps | 🔜 |
| In-app USB SSH tunnel | 🔜 |

---

## Related

- Tablet app: https://github.com/sebbycorp/k8s-goose (`remarkable-diary/takeover`)
- Design notes: `remarkable-stuff` docs (live-stream design)
