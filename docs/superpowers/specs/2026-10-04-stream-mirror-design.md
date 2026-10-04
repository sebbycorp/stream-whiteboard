# Stream mirror mode: design

Date: 2026-10-04 · Status: approved in chat (approach A, tablet section), owner said "do it".

## Goal

For streaming and recording to an audience: the Mac app can show **exactly what is on the tablet panel**, with the same paper, the same ink colours, the side dock and menus, popups and answer cards. Pixels, not a re-drawing. It is an option alongside the existing stroke view, which stays as it is.

## Approach (A: damage-rect mirror)

Every panel refresh in `diary.c` goes through `upd(x,y,w,h,…)`, and off-screen renders skip it (`upd_mute`). So the rectangles passed to `upd()` are exactly what changed on the glass. The tablet records them, merges them, PNG-encodes those regions of the RGB565 framebuffer (existing `capture_png()`), and streams them as `fb` events. The Mac app pastes each piece into a full-panel canvas.

## Tablet (`remarkable-diary/takeover`, repo k8s-goose)

- **`mirror.c/.h`** (pure, host-tested): a damage list of up to 16 rectangles. A rectangle that overlaps or nearly touches an existing one (gap ≤ 32 px) merges into it, and merges cascade. When the list is full, everything collapses into one bounding box. `mirror_take()` snaps each rectangle outward to 8 px, clamps it to the panel, returns the list and resets it. `mirror_full(w,h)` marks the whole panel dirty.
- **`upd()` hook**: after the existing clamp, `mirror_damage(x,y,w,h)` runs only when streaming is on, mirroring is on and at least one viewer is connected.
- **Pump** (main loop, after `stream_poll()`):
  - On a resync request (new viewer or `cmd:resync`), mark the whole panel dirty.
  - At most every 100 ms, and only when no viewer has queued output (`stream_backlog()==0`), take the rectangles, `capture_png` each one, and send `stream_fb()`. A keyframe is flagged `key:1`.
- **`stream.c`**:
  - **Per-viewer send queue** (cap 8 MB, then the viewer is dropped). Partial writes no longer disconnect a viewer, which was fatal for big lines.
  - **Command parsing:** client input is read into a per-viewer line buffer and parsed. `{"t":"cmd","cmd":"resync"}` raises the resync flag; unknown commands are ignored.
  - **New viewer:** a new connection also raises the resync flag.
  - **`hello` additions:** `fbw`, `fbh` (full panel size) and `mirror` (0/1).
- **Settings**: the "Live stream to desktop" row becomes **Off / Ink / Screen**. Ink is strokes only; Screen is strokes plus the mirror. Config key `mirror=` defaults to 1, so existing `stream=1` setups get Screen. Env: `MIRROR=0/1`.

## Protocol additions (proto stays 1; all additive)

```json
{"t":"hello","proto":1,"w":1404,"h":1604,"page":0,"pages":2,"fbw":1404,"fbh":1872,"mirror":1}
{"t":"fb","x":0,"y":0,"w":1404,"h":1872,"key":1,"png":"<base64 RGB PNG>"}
{"t":"cmd","cmd":"resync"}            // desktop → tablet
```

- `fb` coordinates are **panel pixels** (full screen, including the top bar and footer), not canvas units.
- A viewer draws each `fb` at (x, y), unscaled, in arrival order.
- A `key:1` frame covers the whole panel.
- Viewers that don't know `fb` ignore it (forward compatible).

## Mac app (`desktop-app`, repo stream-whiteboard)

- **Mirror canvas:** a `#mirror` canvas sized `fbw×fbh`. Every `fb` event is decoded (base64 → Blob → `createImageBitmap`) and drawn through one serial promise chain, so pieces never land out of order. The canvas is always kept current, so switching modes is instant.
- **Mirror toggle:** a **Mirror** button (key **V**), persisted in localStorage.
  - In Mirror mode the stroke canvases are hidden, the sheet uses the panel's aspect ratio, and Dark, Chroma and Overlay are ignored, because pixels are shown as-is.
  - Save and Copy export the mirror canvas.
- **Resync:** turning Mirror on, (re)connecting, and a **Resync** button all send `cmd:resync`.
- **No mirror support:** if the tablet's `hello` has no `mirror:1`, a toast says to set Live stream to Screen on the tablet.
- **OBS and Meeting presets** work unchanged on top of Mirror.

## Testing

- **Host tests** for `mirror.c`:
  - merge and no-merge by gap
  - cascade merge
  - overflow collapses to one box
  - snapping and clamping
  - full frame
  - empty take
- **Device tests** with a Python NDJSON client against the real tablet:
  - the keyframe arrives (1404×1872 PNG)
  - drawing produces small `fb` pieces
  - `cmd:resync` produces a new keyframe
  - a slow reader is not dropped
  - Ink mode sends no `fb`
- **Build gates:** `./host-test.sh`, `./check.sh` (no new warnings), `cargo test` for the app.
- **Owner check:** the Mac app's Mirror mode matches the tablet on screen.
