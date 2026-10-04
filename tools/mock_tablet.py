#!/usr/bin/env python3
"""
Mock reMarkable stream server for testing Stream Whiteboard without the device.

Listens on 0.0.0.0:27182 (NDJSON). On each client connect it sends a `hello`,
then slowly draws a few diagonal strokes. Accepts desktop control cmds:
  {"t":"cmd","cmd":"clear|undo|rotate"}

Every third stroke it also fakes a circle-to-ask: an `eraserect` for the ink it
"answered", then an `ans` bitmap — the tablet's AI answers are PNG layers rather
than strokes, so this is the only way to exercise that path without the device.

Stream mirror ("Live stream: Screen"): the hello carries fbw/fbh/mirror, and the
mock keeps a fake 1404x1872 RGB panel (grey top bar, side dock, footer, paper).
fb is opt-in per viewer, as on the device: nothing until the viewer sends
`cmd:mirror_on` (answered with a whole-panel keyframe, key:1), then a small `fb`
tile for the damage of every finished stroke, and a fresh keyframe on
`cmd:resync` or clear; `cmd:mirror_off` stops it. down/move carry the
panel-pixel pen position (px/py, plus zs on down) for the app's overlay ink.
`--no-mirror` sends an old-style hello (no fb ever), as a tablet set to Ink.

Usage:
  python3 tools/mock_tablet.py [--no-mirror] [--port N]
  # then in the app set host 127.0.0.1, port 27182, click Apply
"""
import base64
import json
import select
import socket
import struct
import sys
import time
import zlib

HOST, PORT = "0.0.0.0", 27182
MIRROR = "--no-mirror" not in sys.argv
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])

# Fake panel geometry for the mirror (full screen, incl. tablet chrome).
FB_W, FB_H = 1404, 1872
TOP_BAR, FOOTER, DOCK_W = 96, 64, 88

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


def png_rgba(w, h, rows, color_type=6):
    """Minimal PNG encoder (zlib + struct — no pillow). color_type 6 = RGBA,
    2 = RGB (what the tablet's capture_png sends for mirror pieces)."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
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


class Panel:
    """Fake RGB framebuffer for the mirror: tablet chrome + paper + ink."""

    def __init__(self, w=FB_W, h=FB_H):
        self.w, self.h = w, h
        self.reset()

    def reset(self):
        w, h = self.w, self.h
        bar = b"\x3c\x3c\x44" * w
        foot = b"\xdc\xdc\xe0" * w
        body = b"\x30\x50\x80" * DOCK_W + b"\xff\xff\xff" * (w - DOCK_W)
        rule = b"\x30\x50\x80" * DOCK_W + b"\xd8\xe4\xf4" * (w - DOCK_W)
        self.rows = []
        for y in range(h):
            if y < TOP_BAR:
                self.rows.append(bytearray(bar))
            elif y >= h - FOOTER:
                self.rows.append(bytearray(foot))
            elif (y - TOP_BAR) % 80 == 79:
                self.rows.append(bytearray(rule))   # ruled paper, so offsets show
            else:
                self.rows.append(bytearray(body))
        # Dock "buttons": light squares down the side strip.
        for i in range(6):
            y0 = TOP_BAR + 24 + i * 96
            for yy in range(y0, y0 + 56):
                self.rows[yy][16 * 3:(16 + 56) * 3] = b"\xee\xee\xee" * 56

    def dot(self, x, y, r=3, rgb=b"\x10\x10\x10"):
        for yy in range(max(0, y - r), min(self.h, y + r + 1)):
            x0, x1 = max(0, x - r), min(self.w, x + r + 1)
            if x0 < x1:
                self.rows[yy][x0 * 3:x1 * 3] = rgb * (x1 - x0)

    def line(self, x0, y0, x1, y1):
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            self.dot(x0 + (x1 - x0) * i // n, y0 + (y1 - y0) * i // n)

    def png(self, x, y, w, h):
        rows = [self.rows[yy][x * 3:(x + w) * 3] for yy in range(y, y + h)]
        return png_rgba(w, h, rows, color_type=2)


def send_fb(conn, panel, x, y, w, h, key=False):
    """One mirror piece: the panel region (x,y,w,h), snapped/clamped like the device."""
    x0, y0 = max(0, (x // 8) * 8), max(0, (y // 8) * 8)
    x1, y1 = min(panel.w, -(-(x + w) // 8) * 8), min(panel.h, -(-(y + h) // 8) * 8)
    if key:
        x0, y0, x1, y1 = 0, 0, panel.w, panel.h
    if x1 <= x0 or y1 <= y0:
        return
    png = panel.png(x0, y0, x1 - x0, y1 - y0)
    b64 = base64.b64encode(png).decode("ascii")
    send(conn, {"t": "fb", "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
                "key": 1 if key else 0, "png": b64})
    print(f"[mock] fb{' key' if key else ''} {x1 - x0}x{y1 - y0}@{x0},{y0} ({len(b64)} b64 bytes)")


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


def hello(w, h):
    msg = {"t": "hello", "proto": 1, "w": w, "h": h, "page": 0, "pages": 1}
    if MIRROR:
        # cy: panel y of canvas y=0 (this fake panel has no pan/zoom, so a
        # canvas point maps to panel (x, y + TOP_BAR) — the same as px/py).
        msg.update({"fbw": FB_W, "fbh": FB_H, "mirror": 1, "cy": TOP_BAR})
    return msg


def draw_session(conn):
    w, h = 1404, 1872
    land = False
    panel = Panel()
    send(conn, hello(w, h))
    send(conn, {"t": "clear"})
    viewer = {"mirror": False}   # fb is opt-in: nothing until cmd:mirror_on

    def fb(x, y, w_, h_, key=False):
        if MIRROR and viewer["mirror"]:
            send_fb(conn, panel, x, y, w_, h_, key=key)

    def mirror_cmd(cmd):
        """Mirror side effects of a desktop cmd. Returns True if handled."""
        if cmd == "mirror_on":
            viewer["mirror"] = True
            fb(0, 0, FB_W, FB_H, key=True)
            return True
        if cmd == "mirror_off":
            viewer["mirror"] = False
            return True
        if cmd == "resync":
            fb(0, 0, FB_W, FB_H, key=True)
            return True
        if cmd in ("clear", "erase", "eraseall") and MIRROR:
            panel.reset()
            fb(0, 0, FB_W, FB_H, key=True)
        return False

    def stroke_damage(x0, y0, x1, y1):
        """Paint the finished stroke into the fake panel and send its damage tile.
        Canvas y sits below the tablet's top bar on the real panel."""
        if not MIRROR:
            return
        py0, py1 = y0 + TOP_BAR, y1 + TOP_BAR
        if py1 >= FB_H - FOOTER:
            return
        panel.line(x0, py0, x1, py1)
        fb(min(x0, x1) - 8, min(py0, py1) - 8, abs(x1 - x0) + 16, abs(py1 - py0) + 16)

    sid = 1
    y = 100
    stroke_count = 0
    buf = ""
    while True:
        # drain any desktop commands
        buf, cmds = try_read_cmds(conn, buf)
        for cmd in cmds:
            print(f"[mock] cmd {cmd}")
            if mirror_cmd(cmd):
                continue
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
                send(conn, hello(w, h))
                print(f"[mock] orientation {w}x{h}")

        # one diagonal stroke, point by point, ~40 Hz
        send(conn, {"t": "down", "id": sid, "tool": "pen", "color": sid % 6, "width": 1, "x": 100, "y": y,
                    "px": 100, "py": y + TOP_BAR, "zs": 1.0})
        for i in range(1, 60):
            # poll cmds mid-stroke so Clear/Undo feel responsive
            buf, cmds = try_read_cmds(conn, buf)
            for cmd in cmds:
                print(f"[mock] cmd {cmd}")
                if mirror_cmd(cmd):
                    continue
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
                    send(conn, hello(w, h))
            else:
                send(conn, {"t": "move", "id": sid, "x": 100 + i * 18, "y": y + i * 4,
                            "px": 100 + i * 18, "py": y + i * 4 + TOP_BAR})
                time.sleep(0.025)
                continue
            break
        else:
            send(conn, {"t": "up", "id": sid})
            stroke_damage(100, y, 100 + 59 * 18, y + 59 * 4)
            stroke_count += 1
            sid += 1
            if stroke_count % 3 == 0:
                send_answer(conn, w, h, (80, max(0, y - 40), 1200, y + 260))
            y += 90
            if y > min(h - 100, 1700):
                send(conn, {"t": "clear"})
                mirror_cmd("clear")
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
    print("[mock] accepts cmd clear|undo|rotate|resync|mirror_on|mirror_off from the desktop app")
    print(f"[mock] mirror {'on (fb pieces, Live stream: Screen)' if MIRROR else 'off (--no-mirror, Live stream: Ink)'}")
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
