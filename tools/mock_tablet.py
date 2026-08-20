#!/usr/bin/env python3
"""
Mock reMarkable stream server for testing Stream Whiteboard without the device.

Listens on 0.0.0.0:27182 (NDJSON). On each client connect it sends a `hello`,
then slowly draws a few diagonal strokes. Accepts desktop control cmds:
  {"t":"cmd","cmd":"clear|undo|rotate"}

Every third stroke it also fakes a circle-to-ask: an `eraserect` for the ink it
"answered", then an `ans` bitmap — the tablet's AI answers are PNG layers rather
than strokes, so this is the only way to exercise that path without the device.

Usage:
  python3 tools/mock_tablet.py
  # then in the app set host 127.0.0.1, port 27182, click Apply
"""
import base64
import json
import select
import socket
import struct
import time
import zlib

HOST, PORT = "0.0.0.0", 27182

# 5x7 block glyphs — enough to spell a recognisable answer. stdlib only, so
# there is no font engine here; this is deliberately crude.
GLYPHS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    "?": ("01110", "10001", "00001", "00110", "00100", "00000", "00100"),
    " ": ("00000",) * 7,
}


def png_rgba(w, h, rows):
    """Minimal RGBA PNG encoder (zlib + struct — no pillow)."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)   # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def answer_png(lines, w, h, px=10):
    """Black text on transparent — the shape the tablet sends after turning its
    near-white background see-through."""
    rows = [bytearray(w * 4) for _ in range(h)]
    for li, text in enumerate(lines):
        y0 = 60 + li * (8 * px)
        for ci, ch in enumerate(text.upper()):
            g = GLYPHS.get(ch)
            if not g:
                continue
            x0 = 60 + ci * (6 * px)
            for gy, bits in enumerate(g):
                for gx, bit in enumerate(bits):
                    if bit != "1":
                        continue
                    for dy in range(px):
                        yy = y0 + gy * px + dy
                        if not (0 <= yy < h):
                            continue
                        row = rows[yy]
                        for dx in range(px):
                            xx = x0 + gx * px + dx
                            if 0 <= xx < w:
                                o = xx * 4
                                row[o] = row[o + 1] = row[o + 2] = 0
                                row[o + 3] = 255
    return png_rgba(w, h, rows)


def send(conn, obj):
    conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def try_read_cmds(conn, buf):
    """Non-blocking-ish read of client NDJSON cmds. Returns (buf, list of cmd str)."""
    cmds = []
    try:
        conn.setblocking(False)
        chunk = conn.recv(4096)
        if chunk == b"":
            raise ConnectionResetError("closed")
        if chunk:
            buf += chunk.decode("utf-8", errors="replace")
    except BlockingIOError:
        pass
    finally:
        try:
            conn.setblocking(True)
        except OSError:
            pass

    while "\n" in buf:
        line, buf = buf.split("\n", 1)
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("t") == "cmd" and msg.get("cmd"):
            cmds.append(str(msg["cmd"]))
    return buf, cmds


def send_answer(conn, w, h, rect):
    """Fake a circle-to-ask: erase the circled ink, then send the answer bitmap."""
    send(conn, {"t": "eraserect", "x0": rect[0], "y0": rect[1], "x1": rect[2], "y1": rect[3]})
    png = answer_png(["WHO IS BATMAN?", "BRUCE WAYNE", "IS BATMAN"], w, h)
    send(conn, {
        "t": "ans", "cx": 0, "cy": 0, "w": w, "h": h,
        "png": base64.b64encode(png).decode("ascii"),
    })
    print(f"[mock] ans {w}x{h} ({len(png)} bytes png)")


def draw_session(conn):
    w, h = 1404, 1872
    land = False
    send(conn, {"t": "hello", "proto": 1, "w": w, "h": h, "page": 0, "pages": 1})
    send(conn, {"t": "clear"})
    sid = 1
    y = 100
    stroke_count = 0
    buf = ""
    while True:
        # drain any desktop commands
        buf, cmds = try_read_cmds(conn, buf)
        for cmd in cmds:
            print(f"[mock] cmd {cmd}")
            if cmd in ("clear", "erase", "eraseall"):
                send(conn, {"t": "clear"})
                stroke_count = 0
                y = 100
            elif cmd == "undo":
                if stroke_count > 0:
                    stroke_count -= 1
                    send(conn, {"t": "undo"})
            elif cmd in ("rotate", "landscape", "orient"):
                land = not land
                w, h = (1872, 1404) if land else (1404, 1872)
                send(conn, {"t": "hello", "proto": 1, "w": w, "h": h, "page": 0, "pages": 1})
                print(f"[mock] orientation {w}x{h}")

        # one diagonal stroke, point by point, ~40 Hz
        send(conn, {"t": "down", "id": sid, "tool": "pen", "color": sid % 6, "width": 1, "x": 100, "y": y})
        for i in range(1, 60):
            # poll cmds mid-stroke so Clear/Undo feel responsive
            buf, cmds = try_read_cmds(conn, buf)
            for cmd in cmds:
                print(f"[mock] cmd {cmd}")
                if cmd in ("clear", "erase", "eraseall"):
                    send(conn, {"t": "up", "id": sid})
                    send(conn, {"t": "clear"})
                    stroke_count = 0
                    y = 100
                    sid += 1
                    time.sleep(0.2)
                    break
                if cmd == "undo":
                    send(conn, {"t": "up", "id": sid})
                    send(conn, {"t": "undo"})
                    if stroke_count > 0:
                        stroke_count -= 1
                    sid += 1
                    time.sleep(0.2)
                    break
                if cmd in ("rotate", "landscape", "orient"):
                    land = not land
                    w, h = (1872, 1404) if land else (1404, 1872)
                    send(conn, {"t": "hello", "proto": 1, "w": w, "h": h, "page": 0, "pages": 1})
            else:
                send(conn, {"t": "move", "id": sid, "x": 100 + i * 18, "y": y + i * 4})
                time.sleep(0.025)
                continue
            break
        else:
            send(conn, {"t": "up", "id": sid})
            stroke_count += 1
            sid += 1
            if stroke_count % 3 == 0:
                send_answer(conn, w, h, (80, max(0, y - 40), 1200, y + 260))
            y += 90
            if y > min(h - 100, 1700):
                send(conn, {"t": "clear"})
                stroke_count = 0
                y = 100
            time.sleep(0.5)
            continue
        # mid-stroke break path already advanced sid
        time.sleep(0.3)


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    print(f"[mock] listening on {HOST}:{PORT} — Ctrl+C to stop")
    print("[mock] accepts cmd clear|undo|rotate from the desktop app")
    while True:
        conn, addr = srv.accept()
        print(f"[mock] client {addr}")
        try:
            draw_session(conn)
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            print(f"[mock] client gone ({e})")
        finally:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
