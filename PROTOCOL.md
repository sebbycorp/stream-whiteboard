# Stream Whiteboard protocol

**Transport:** TCP, tablet listens on port **27182** (default).  
**Framing:** one JSON object per line (NDJSON), UTF-8.  
**Direction:**
- **Tablet → desktop** — strokes and page events (server push)
- **Desktop → tablet** — optional control commands (`clear` / `undo` / `rotate`)

Version field: `"proto": 1` on `hello`.

---

## Events (tablet → desktop)

### `hello` — connection / orientation

```json
{"t":"hello","proto":1,"w":1404,"h":1604,"page":0,"pages":2}
```

| Field | Meaning |
|-------|---------|
| `w`, `h` | Writing surface in canvas units — the tablet content band, **not** the full panel. Top bar and footer chrome are stripped. Stroke `x`/`y` use this origin (y=0 is the top of the paper, just below the menu). Portrait RM2 is typically 1404×1604. |
| `page`, `pages` | Current page index (0-based) and page count |

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
{"t":"down","id":12,"tool":"pen","color":0,"width":1,"x":220,"y":400}
```

| Field | Meaning |
|-------|---------|
| `id` | Stroke id (unique until `up`) |
| `tool` | `pen`, `hl`, `erase`, or shape name |
| `color` | Index into tablet ink palette (0=black, …) |
| `width` | 0=S, 1=M, 2=L |
| `x`, `y` | Canvas coordinates |

### `move`

```json
{"t":"move","id":12,"x":225,"y":404}
```

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

---

## Commands (desktop → tablet)

> ⚠️ **Status: specified, NOT implemented on the device (verified 2026-08-19).**
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
```

| `cmd` | Tablet action | Broadcast |
|-------|---------------|-----------|
| `clear` | Clear current page strokes | `{"t":"clear"}` |
| `undo` | Undo last stroke (if any) | `{"t":"undo"}` |
| `rotate` | Toggle landscape / portrait | `{"t":"hello",…}` with new `w`/`h` |

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
6. Ignore unknown `t` values for forward compatibility.

## Future (proto 2+)

- Length-prefixed PNG resync frames  
- `request_resync`  
- Auth token on connect  
