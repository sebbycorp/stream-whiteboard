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
| Installable Mac app (Tauri / SwiftUI) | 🔜 |
| Windows packaging | 🔜 |
| PNG resync / AI bitmaps | 🔜 |
| Meeting / OBS mode | 🔜 |

---

## Related

- Tablet app: https://github.com/sebbycorp/k8s-goose (`remarkable-diary/takeover`)
- Design notes: `remarkable-stuff` docs (live-stream design)
