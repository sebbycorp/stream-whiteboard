#!/usr/bin/env python3
"""
SebbyCorp Notepad · Live bridge

Tablet speaks TCP NDJSON on :27182.
Browsers speak WebSocket. This process bridges them.

Works on Windows, macOS, and Linux with Python 3.9+ (stdlib only).

Usage:
  python bridge.py --tablet 172.16.10.175
  python bridge.py --tablet 10.11.99.1          # USB
  # then open viewer.html (or: python bridge.py --serve)

If Wi-Fi SSH/stream is blocked, tunnel first:
  ssh -L 27182:127.0.0.1:27182 root@10.11.99.1
  python bridge.py --tablet 127.0.0.1
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import json
import os
import socket
import struct
import sys
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_WS_PORT = 27183
DEFAULT_TABLET_PORT = 27182


# ── minimal WebSocket server (no deps) ──────────────────────────────────────

def _ws_accept_key(key: str) -> str:
    import base64, hashlib
    guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    dig = hashlib.sha1((key + guid).encode()).digest()
    return base64.b64encode(dig).decode()


def _ws_send_text(sock: socket.socket, text: str) -> None:
    data = text.encode("utf-8")
    n = len(data)
    header = bytearray([0x81])  # FIN + text
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", n))
    sock.sendall(header + data)


def _ws_read_frame(sock: socket.socket) -> bytes | None:
    hdr = sock.recv(2)
    if len(hdr) < 2:
        return None
    opcode = hdr[0] & 0x0F
    masked = (hdr[1] & 0x80) != 0
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]
    mask = sock.recv(4) if masked else b""
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            return None
        data += chunk
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    if opcode == 0x8:  # close
        return None
    return data


class Hub:
    def __init__(self) -> None:
        self.clients: list[socket.socket] = []
        self.lock = threading.Lock()

    def add(self, s: socket.socket) -> None:
        with self.lock:
            self.clients.append(s)

    def remove(self, s: socket.socket) -> None:
        with self.lock:
            if s in self.clients:
                self.clients.remove(s)

    def broadcast(self, line: str) -> None:
        dead = []
        with self.lock:
            for c in self.clients:
                try:
                    _ws_send_text(c, line if line.endswith("\n") else line + "\n")
                except OSError:
                    dead.append(c)
            for c in dead:
                if c in self.clients:
                    self.clients.remove(c)
                try:
                    c.close()
                except OSError:
                    pass


def tablet_reader(host: str, port: int, hub: Hub, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            print(f"[bridge] connecting tablet {host}:{port} …")
            s = socket.create_connection((host, port), timeout=8)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print(f"[bridge] tablet connected")
            buf = b""
            s.settimeout(1.0)
            while not stop.is_set():
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line:
                        continue
                    text = line.decode("utf-8", errors="replace")
                    # validate JSON lightly
                    try:
                        json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    hub.broadcast(text)
            s.close()
            print("[bridge] tablet disconnected; retry in 2s")
        except OSError as e:
            print(f"[bridge] tablet error: {e}; retry in 2s")
        stop.wait(2.0)


def ws_server(port: int, hub: Hub, stop: threading.Event) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(8)
    srv.settimeout(1.0)
    print(f"[bridge] WebSocket ws://127.0.0.1:{port}")
    while not stop.is_set():
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            continue
        threading.Thread(target=ws_client, args=(conn, addr, hub), daemon=True).start()
    srv.close()


def ws_client(conn: socket.socket, addr, hub: Hub) -> None:
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = conn.recv(4096)
            if not chunk:
                return
            req += chunk
        headers = req.decode("utf-8", errors="replace").split("\r\n")
        key = ""
        for h in headers:
            if h.lower().startswith("sec-websocket-key:"):
                key = h.split(":", 1)[1].strip()
        if not key:
            conn.close()
            return
        accept = _ws_accept_key(key)
        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        conn.sendall(resp.encode())
        print(f"[bridge] viewer + {addr}")
        hub.add(conn)
        # keep reading until close (viewers rarely send)
        conn.settimeout(1.0)
        while True:
            try:
                frame = _ws_read_frame(conn)
            except socket.timeout:
                continue
            except OSError:
                break
            if frame is None:
                break
    finally:
        hub.remove(conn)
        try:
            conn.close()
        except OSError:
            pass
        print(f"[bridge] viewer - {addr}")


def serve_static(port: int) -> None:
    os.chdir(HERE)

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print("[http]", fmt % args)

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"[bridge] viewer http://127.0.0.1:{port}/viewer.html")
    httpd.serve_forever()


def main() -> int:
    ap = argparse.ArgumentParser(description="Notepad live stream bridge")
    ap.add_argument("--tablet", default="172.16.10.175", help="tablet IP")
    ap.add_argument("--tablet-port", type=int, default=DEFAULT_TABLET_PORT)
    ap.add_argument("--ws-port", type=int, default=DEFAULT_WS_PORT)
    ap.add_argument("--http-port", type=int, default=8765)
    ap.add_argument("--serve", action="store_true", help="also serve viewer.html")
    ap.add_argument("--open", action="store_true", help="open browser")
    args = ap.parse_args()

    hub = Hub()
    stop = threading.Event()

    threading.Thread(
        target=tablet_reader,
        args=(args.tablet, args.tablet_port, hub, stop),
        daemon=True,
    ).start()
    threading.Thread(
        target=ws_server, args=(args.ws_port, hub, stop), daemon=True
    ).start()

    if args.serve:
        threading.Thread(target=serve_static, args=(args.http_port,), daemon=True).start()
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{args.http_port}/viewer.html")

    print("[bridge] running — Ctrl+C to stop")
    print(f"[bridge] viewer connects to ws://127.0.0.1:{args.ws_port}")
    try:
        while True:
            stop.wait(3600)
    except KeyboardInterrupt:
        stop.set()
        print("\n[bridge] bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
