#!/usr/bin/env python3
"""
Mock reMarkable stream server for testing Stream Whiteboard without the device.

Listens on 0.0.0.0:27182 (NDJSON). On each client connect it sends a `hello`,
then slowly draws a few diagonal strokes in a loop so the viewer shows live ink.

Usage:
  python3 tools/mock_tablet.py
  # then in the app set host 127.0.0.1, port 27182, click Apply
"""
import json
import socket
import time

HOST, PORT = "0.0.0.0", 27182


def send(conn, obj):
    conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def draw_session(conn):
    send(conn, {"t": "hello", "proto": 1, "w": 1404, "h": 1872, "page": 0, "pages": 1})
    send(conn, {"t": "clear"})
    sid = 1
    y = 100
    while True:
        # one diagonal stroke, point by point, ~40 Hz
        send(conn, {"t": "down", "id": sid, "tool": "pen", "color": sid % 6, "width": 1, "x": 100, "y": y})
        for i in range(1, 60):
            send(conn, {"t": "move", "id": sid, "x": 100 + i * 18, "y": y + i * 4})
            time.sleep(0.025)
        send(conn, {"t": "up", "id": sid})
        sid += 1
        y += 90
        if y > 1700:
            send(conn, {"t": "clear"})
            y = 100
        time.sleep(0.5)


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)
    print(f"[mock] listening on {HOST}:{PORT} — Ctrl+C to stop")
    while True:
        conn, addr = srv.accept()
        print(f"[mock] client {addr}")
        try:
            draw_session(conn)
        except (BrokenPipeError, ConnectionResetError, OSError):
            print("[mock] client gone")
        finally:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
