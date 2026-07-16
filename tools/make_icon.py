#!/usr/bin/env python3
"""Write a plain 1024x1024 PNG (solid accent color) for use as an app icon source.

Usage: python3 tools/make_icon.py desktop-app/src-tauri/icon-src.png
"""
import struct
import sys
import zlib

SIZE = 1024
RGB = (61, 90, 128)  # --acc from the viewer


def chunk(tag, data):
    body = tag + data
    return struct.pack("!I", len(data)) + body + struct.pack("!I", zlib.crc32(body) & 0xFFFFFFFF)


def main(path):
    r, g, b = RGB
    row = b"\x00" + bytes([r, g, b] * SIZE)  # filter byte 0 + RGB pixels
    raw = row * SIZE
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack("!IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0))  # 8-bit RGB
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    print(f"wrote {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "icon-src.png")
