"""Рисует иконки PWA (пиксельный Недоделок) без внешних библиотек.

    python tools/make_icons.py

Пишет client/icons/icon-192.png, icon-512.png и icon-maskable-512.png.
"""
import struct
import zlib
from pathlib import Path

BG = (42, 36, 56)
BODY = (199, 179, 230)
DARK = (125, 104, 168)
EAR = (231, 217, 255)
WHITE = (255, 255, 255)
PUPIL = (42, 36, 56)
YELLOW = (255, 244, 168)
RED = (192, 57, 43)


def grid(size):
    g = [[BG] * size for _ in range(size)]

    def ell(cx, cy, rx, ry, c):
        for y in range(size):
            for x in range(size):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                    g[y][x] = c

    def rect(x0, y0, w, h, c):
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                if 0 <= x < size and 0 <= y < size:
                    g[y][x] = c

    for lx in (11, 16, 21):                      # три лапы (нечётное число)
        rect(lx, 24, 2, 4, DARK)
    rect(26, 17, 2, 2, BODY); rect(28, 13, 2, 5, BODY); ell(29, 12, 1.6, 1.6, DARK)   # хвост
    ell(16, 19, 10, 8, BODY)                      # корпус-картофелина
    rect(11, 7, 1, 6, DARK); ell(11, 6, 3.4, 2.2, EAR); ell(11, 6, 1.8, 1.0, DARK)    # ухо-антенна
    ell(13, 17, 2.4, 2.2, WHITE); rect(13, 17, 1, 1, PUPIL)                            # три разных глаза
    ell(18, 15, 1.8, 1.6, WHITE); rect(18, 15, 1, 1, PUPIL)
    ell(22, 18, 1.2, 1.2, YELLOW); rect(22, 18, 1, 1, PUPIL)
    rect(14, 22, 5, 1, PUPIL)                     # рот
    rect(18, 22, 4, 3, RED)                       # штамп
    return g


def png_bytes(pixels, scale):
    size = len(pixels)
    out = size * scale
    raw = bytearray()
    for row in pixels:
        line = bytearray([0])
        for r, g_, b in row:
            line += bytes((r, g_, b)) * scale
        raw += bytes(line) * scale
    # у каждой строки результата свой байт фильтра: добавлен в line
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", out, out, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def centered(pixels, scale, canvas):
    """Зверёк в масштабе scale по центру холста canvas x canvas (для maskable)."""
    small = [[px for px in row for _ in range(scale)] for row in pixels for _ in range(scale)]
    off = (canvas - len(small)) // 2
    out = [[BG] * canvas for _ in range(canvas)]
    for y, row in enumerate(small):
        out[y + off][off:off + len(row)] = row
    return out


def main():
    dest = Path(__file__).resolve().parent.parent / "client" / "icons"
    dest.mkdir(parents=True, exist_ok=True)
    base = grid(32)
    (dest / "icon-192.png").write_bytes(png_bytes(base, 6))
    (dest / "icon-512.png").write_bytes(png_bytes(base, 16))
    (dest / "icon-maskable-512.png").write_bytes(png_bytes(centered(base, 12, 512), 1))
    print("готово:", ", ".join(p.name for p in sorted(dest.glob("*.png"))))


if __name__ == "__main__":
    main()
