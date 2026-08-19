#!/usr/bin/env python3
"""
Mock reMarkable stream server for testing Stream Whiteboard without the device.

Listens on 0.0.0.0:27182 (NDJSON). On each client connect it sends a `hello`,
then slowly draws a few diagonal strokes. Accepts desktop control cmds:
  {"t":"cmd","cmd":"clear|undo|rotate"}

Usage:
  python3 tools/mock_tablet.py
  # then in the app set host 127.0.0.1, port 27182, click Apply
"""
import json
import select
import socket
import time

HOST, PORT = "0.0.0.0", 27182


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
