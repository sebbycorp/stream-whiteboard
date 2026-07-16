# Architecture

## Components

```
┌─────────────────────────┐
│  reMarkable 2           │
│  diary (takeover)       │
│  stream.c TCP :27182    │
└───────────┬─────────────┘
            │ NDJSON strokes
            ▼
┌─────────────────────────┐
│  bridge.py (this repo)  │
│  • TCP client to tablet │
│  • WebSocket :27183     │
│  • optional HTTP static │
└───────────┬─────────────┘
            │ WS frames
            ▼
┌─────────────────────────┐
│  viewer.html            │
│  canvas 2D paper        │
│  Mac / Win / Linux      │
└─────────────────────────┘
```

## Why bridge + browser (MVP)

- Zero install beyond Python  
- Same path for Mac and Windows  
- Tablet stays a simple TCP server (no WebSocket lib on-device)

## Later: native Mac app

Replace or wrap the browser with:

- **Tauri** — ship `viewer.html` + local WS, one binary  
- **SwiftUI** — native canvas, direct TCP to tablet (no bridge)

Protocol stays the same; only the client shell changes.

## Tablet ownership

Stream **emit** code remains in **k8s-goose** so device deploy stays one pipeline.  
This repo owns **viewers and packaging**.
