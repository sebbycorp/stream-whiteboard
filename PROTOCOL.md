# Stream Whiteboard protocol

**Transport:** TCP, tablet listens on port **27182** (default).  
**Framing:** one JSON object per line (NDJSON), UTF-8.  
**Direction:** tablet → desktop (server push). Desktop may send control messages later.

Version field: `"proto": 1` on `hello`.

---

## Events

### `hello` — connection / orientation

```json
{"t":"hello","proto":1,"w":1404,"h":1872,"page":0,"pages":2}
```

| Field | Meaning |
|-------|---------|
| `w`, `h` | Logical page size in canvas units (portrait or landscape) |
| `page`, `pages` | Current page index (0-based) and page count |

Sent when stream starts and when orientation may change (e.g. landscape toggle).

### `page`

```json
{"t":"page","page":1,"pages":3}
```

### `clear`

```json
{"t":"clear"}
```

Clear the current page view on the client.

### `undo`

```json
{"t":"undo"}
```

Remove the last stroke on the client (best-effort; clients may full-clear).

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

## Client rules

1. On `hello`, set canvas size to `w`×`h` and clear.
2. On `down`/`move`/`up`, draw polylines with tool styling.
3. On `clear`, wipe the page.
4. Ignore unknown `t` values for forward compatibility.

## Future (proto 2+)

- Length-prefixed PNG resync frames  
- Client → tablet control (`ping`, `request_resync`)  
- Auth token on connect  
