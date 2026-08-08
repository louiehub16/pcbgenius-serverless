#!/usr/bin/env python3
"""Generate placeholder PCBGenius app icons (pure stdlib, no PIL/network).

Tauri requires the icon files referenced in tauri.conf.json to exist at
build time. This emits valid PNG/ICO/ICNS placeholders: a flat dark-blue
square with a lighter 'PCB' chip motif approximated by a simple pattern.
"""

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / "icons"
OUT.mkdir(parents=True, exist_ok=True)

# Brand colors
BG = (18, 26, 46)      # dark navy
FG = (0, 200, 160)     # teal accent
TRACK = (255, 255, 255)


def chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_bytes(size: int) -> bytes:
    """Build a square PNG of the given size."""
    raw = b""
    row = bytearray()
    for y in range(size):
        row = bytearray(b"\x00")  # filter: None
        for x in range(size):
            is_track = (
                abs(x - y) < max(2, size // 40)          # diagonal bus
                or abs((size - 1 - x) - y) < max(2, size // 40)  # counter diagonal
            )
            is_pad = (
                (x // (size // 8)) % 2 == (y // (size // 8)) % 2
                and size // 8 - 1 <= x % (size // 8) <= size // 8
                and size // 8 - 1 <= y % (size // 8) <= size // 8
            ) if size >= 64 else False
            if is_pad:
                r, g, b = FG
            elif is_track:
                r, g, b = TRACK
            else:
                r, g, b = BG
            row += bytes((r, g, b, 255))
        raw += bytes(row)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def ico_bytes(png_sizes) -> bytes:
    """Wrap multiple PNGs into a valid ICO (Vista+ PNG-compressed entries)."""
    images = [png_bytes(s) for s in png_sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    offset = 6 + 16 * len(images)
    for s, data in zip(png_sizes, images):
        # 0 means 256 in the ICONDIRENTRY dimensions
        entries += struct.pack(
            "<BBBBHHII", s if s < 256 else 0, s if s < 256 else 0, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
    return header + entries + b"".join(images)


def icns_bytes() -> bytes:
    """Single-PNG ICNS (type 'ic07' = 128px). Valid for macOS."""
    data = png_bytes(128)
    entry = b"ic07" + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(entry) + 8) + entry


def main() -> None:
    (OUT / "32x32.png").write_bytes(png_bytes(32))
    (OUT / "128x128.png").write_bytes(png_bytes(128))
    (OUT / "128x128@2x.png").write_bytes(png_bytes(256))
    (OUT / "icon.ico").write_bytes(ico_bytes([16, 32, 48, 64, 128, 256]))
    (OUT / "icon.icns").write_bytes(icns_bytes())
    print("icons written to", OUT)


if __name__ == "__main__":
    main()