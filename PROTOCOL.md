# Stream Whiteboard protocol

**Transport:** TCP, tablet listens on port **27182** (default).  
**Framing:** one JSON object per line (NDJSON), UTF-8.  
**Direction:**
- **Tablet → desktop** — strokes and page events (server push)
- **Desktop → tablet** — optional control commands (`clear` / `undo` / `rotate` / `resync` / `mirror_on` / `mirror_off`)

Version field: `"proto": 1` on `hello`.

---

## Events (tablet → desktop)

### `hello` — connection / orientation

```json
{"t":"hello","proto":1,"w":1404,"h":1604,"page":0,"pages":2,"fbw":1404,"fbh":1872,"mirror":1,"cy":0}
```

| Field | Meaning |
|-------|---------|
| `w`, `h` | Writing surface in canvas units — the tablet content band, **not** the full panel. Top bar and footer chrome are stripped. Stroke `x`/`y` use this origin (y=0 is the top of the paper, just below the menu). Portrait RM2 is typically 1404×1604. |
| `page`, `pages` | Current page index (0-based) and page count |
| `fbw`, `fbh` | *(optional, stream mirror)* Full panel size in pixels, including the top bar and footer — the coordinate space of `fb` events. RM2 portrait: 1404×1872 |
| `mirror` | *(optional)* `1` when the tablet can stream `fb` screen pieces (Settings → Live stream to desktop → **Screen**), `0` for Ink. Absent on older builds — treat as 0. A viewer still has to opt in with `cmd:mirror_on` |
| `cy` | *(optional)* Panel y of canvas y=0. Informational only — stroke `x`/`y` also go through the tablet's pan/zoom, so use `px`/`py` on `down`/`move` for panel positions. Currently always 0; treat missing as 0 |

Sent when stream starts and when orientation changes (landscape toggle).

**Client rules for `hello`:**
1. First connect → set canvas size and clear.
2. Same page, only `w`/`h` changed → **resize, keep strokes** (orientation sync).
3. Page index changed → clear (page flip).

### `page`

```json
{"t":"page","page":1,"pages":3}
```

Clear the current page view on the client.

### `clear`

```json
{"t":"clear"}
```

Clear the current page view on the client (tablet rail/more, or echo of desktop cmd).

### `undo`

```json
{"t":"undo"}
```

Remove the last committed stroke on the client. Only emitted when the tablet
actually had a stroke to remove.

### `down` — pen/tool down

```json
{"t":"down","id":12,"tool":"pen","color":0,"width":1,"x":220,"y":400,"px":220,"py":496,"zs":1.0}
```

| Field | Meaning |
|-------|---------|
| `id` | Stroke id (unique until `up`) |
| `tool` | `pen`, `hl`, `erase`, or shape name |
| `color` | Index into tablet ink palette (0=black, …) |
| `width` | 0=S, 1=M, 2=L |
| `x`, `y` | Canvas coordinates |
| `px`, `py` | *(optional, stream mirror)* The same pen position in **panel pixels** (after the tablet's pan/zoom) — the `fb` coordinate space |
| `zs` | *(optional)* Current zoom scale; a panel-space line width is the tool width × `zs` |

### `move`

```json
{"t":"move","id":12,"x":225,"y":404,"px":225,"py":500}
```

`px`/`py` as on `down`. A mirror viewer can draw these as live vector ink over
the mirror, then drop each stroke once an `fb` piece that arrived after its
`up` covers it (that piece holds the real pixels). If `px`/`py` are absent,
skip the overlay for that stroke.

Coalesced (~40 Hz) on the tablet when the network is busy.

### `up`

```json
{"t":"up","id":12}
```

### `shape`

```json
{"t":"shape","tool":"rect","color":0,"width":1,"x0":100,"y0":200,"x1":400,"y1":500}
```

Committed shape from corner to corner in canvas space.

### `ans` — AI answer bitmap

```json
{"t":"ans","cx":0,"cy":0,"w":1404,"h":1604,"png":"iVBORw0KGgo…"}
```

| Field | Meaning |
|-------|---------|
| `cx`, `cy` | Top-left placement in canvas coordinates |
| `w`, `h` | Size to draw at, in canvas units (the bitmap is scaled to fit) |
| `png` | Base64 **RGBA** PNG. The tablet has already made its near-white background transparent (the `0xF7DE` cutoff `ans_blit()` uses), so a viewer just composites it |

Circle-to-ask answers — typed replies, markmaps, flow charts, diagrams — are
*not* strokes. On the device they live in a separate page layer (`Page.ans[]`,
bitmaps captured off the framebuffer), so nothing in `down`/`move`/`up`
describes them and a viewer that only follows ink shows the question and never
the reply. This event carries the rendered pixels instead, which covers every
answer kind through one path and needs no font engine on the client.

This is the only event that is **big** — tens to hundreds of KB on one line.
The tablet queues it and drains it across several poll ticks, so small events
emitted afterwards arrive *after* it, never interleaved inside it.

**Client rules:** draw it *under* the ink (the device blits answers first), keep
at most 6 (the device's `MAX_ANS`), and drop them all on `clear` / `page` /
first `hello` exactly like strokes.

### `eraserect`

```json
{"t":"eraserect","x0":100,"y0":200,"x1":900,"y1":700}
```

Strokes the tablet deleted inside a canvas-space rect. Circle-to-ask erases the
handwriting it just answered, so without this the viewer keeps showing a
question the tablet has already replaced.

Drop every stroke whose **bounding-box centre** lies inside the rect — the same
test `erase_strokes_in_region()` applies on the device, so both sides keep the
same ink.

### `fb` — stream mirror: a piece of the tablet screen

```json
{"t":"fb","x":0,"y":0,"w":1404,"h":1872,"key":1,"png":"iVBORw0KGgo…"}
```

| Field | Meaning |
|-------|---------|
| `x`, `y`, `w`, `h` | Region in **panel pixels** (full screen incl. top bar, dock and footer — `fbw`×`fbh` space), *not* canvas units |
| `key` | `1` = keyframe covering the whole panel; `0` = a damage piece |
| `png` | Base64 **RGB** PNG of exactly that region of the framebuffer |

**Opt-in per viewer:** sent only when the tablet's Live stream is set to
**Screen** (`hello.mirror:1`) *and* the viewer has sent `cmd:mirror_on` on this
connection (`cmd:mirror_off` stops it). A viewer that never opts in gets no
`fb` — opt in again after every reconnect.
The rectangles are what the device actually refreshed on the glass (merged and
snapped outward to 8 px), so pasting every piece reproduces the panel exactly —
paper, ink colours, menus, popups and answer cards. A keyframe answers
`cmd:mirror_on` and `cmd:resync`. Pieces are rate-limited (≤ every 100 ms, and
only while no viewer has queued output). Keyframes are a single line of roughly
100–400 KB; readers must not cap line length.

**Client rules:** draw each piece unscaled at (`x`, `y`) on a `fbw`×`fbh`
canvas, **in arrival order** (decoding is async — serialise it), and never
recolour the pixels. Clients that don't show the mirror ignore `fb`.

---

## Commands (desktop → tablet)

> ✅ **`resync` is implemented on the device** (stream mirror, 2026-10): the
> tablet now reads client lines into a per-viewer buffer and parses them, and
> `{"t":"cmd","cmd":"resync"}` makes it send a fresh `fb` keyframe. The
> per-viewer `mirror_on` / `mirror_off` opt-in is implemented too. Unknown
> commands are ignored.
>
> ⚠️ **`clear` / `undo` / `rotate`: specified, NOT implemented on the device (verified 2026-08-19).**
> `stream.c`'s `stream_poll()` reads client bytes into a buffer named `junk`
> solely to detect disconnects — nothing parses them, and `stream.h` exposes no
> command callback. A desktop `write()` therefore *succeeds* while the tablet
> ignores the line, which is why these buttons looked functional.
>
> `rotate` is further unimplementable as specified: `diary.c` contains no
> orientation code, its canvas is welded to the fixed-portrait framebuffer
> (`canvas_w()` == `vinfo.xres`), and the tool palette has no rotate action.
> The Mac app now rotates its **own view** instead.
>
> Making `clear`/`undo` real needs only an NDJSON parser where `junk` is —
> `page_clear_strokes()` and `stroke_undo()` already exist and `stream.c`
> already links `cJSON.c`. The section below is the intended contract.

One NDJSON line per command. Tablet applies the action (diary screen only) and
**rebroadcasts** the matching event so all viewers stay in sync.

```json
{"t":"cmd","cmd":"clear"}
{"t":"cmd","cmd":"undo"}
{"t":"cmd","cmd":"rotate"}
{"t":"cmd","cmd":"resync"}
{"t":"cmd","cmd":"mirror_on"}
{"t":"cmd","cmd":"mirror_off"}
```

| `cmd` | Tablet action | Broadcast |
|-------|---------------|-----------|
| `clear` | Clear current page strokes | `{"t":"clear"}` |
| `undo` | Undo last stroke (if any) | `{"t":"undo"}` |
| `rotate` | Toggle landscape / portrait | `{"t":"hello",…}` with new `w`/`h` |
| `resync` | **Implemented.** Mark the whole panel dirty (stream mirror) | `{"t":"fb",…,"key":1}` to the viewers |
| `mirror_on` | **Implemented.** Start sending `fb` to *this* viewer | `{"t":"fb",…,"key":1}` to this viewer |
| `mirror_off` | **Implemented.** Stop sending `fb` to this viewer | — |

Aliases accepted by the tablet parser: `erase` / `eraseall` → clear;  
`landscape` / `orient` → rotate.

Unknown `t` / `cmd` values are ignored (forward compatible).

---

## Client rules (summary)

1. On first `hello`, set canvas size to `w`×`h` and clear.
2. On orientation-only `hello` (same page), resize and **keep ink**.
3. On `down`/`move`/`up`, draw polylines with tool styling.
4. On `clear`, wipe the page; on `undo`, drop last stroke.
5. Prefer sending `cmd` for Clear / Undo so the tablet is source of truth — **once the device implements the parser** (see the warning above). Today, only tablet → desktop events are live.
6. On `ans`, composite the PNG under the ink; on `eraserect`, drop strokes
   whose bbox centre is inside the rect.
7. To show the mirror, send `cmd:mirror_on` (again after every reconnect) and
   `cmd:mirror_off` when you stop; `cmd:resync` asks for a fresh keyframe. On
   `fb`, paste the PNG unscaled at (`x`,`y`) on a `fbw`×`fbh` canvas, in
   arrival order.
8. Ignore unknown `t` values for forward compatibility.

## Future (proto 2+)

- Length-prefixed PNG resync frames (`ans` now covers answer bitmaps, base64 on
  one NDJSON line — a length-prefixed frame would avoid the ~33% base64 cost)  
- `request_resync`  
- Auth token on connect  
