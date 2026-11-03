"""Writes a tiny Blu-ray (PGS) subtitle file, for tests: white text drawn with OpenCV."""
import struct

import cv2
import numpy as np


def _seg(kind, pts, data):
    t = int(round(pts * 90000))
    return b"PG" + struct.pack(">IIBH", t, t, kind, len(data)) + data


def _rle(img):
    out = bytearray()
    for row in img:
        x, n = 0, len(row)
        while x < n:
            c = int(row[x])
            L = 1
            while x + L < n and row[x + L] == c and L < 16383:
                L += 1
            if c == 0:
                out += bytes([0, L]) if L < 64 else bytes([0, 0x40 | (L >> 8), L & 0xFF])
            elif L < 3:
                out += bytes([c]) * L
            elif L < 64:
                out += bytes([0, 0x80 | L, c])
            else:
                out += bytes([0, 0xC0 | (L >> 8), L & 0xFF, c])
            x += L
        out += b"\x00\x00"
    return bytes(out)


def text_image(text, scale=1.6):
    lines = text.split("\n")
    font = cv2.FONT_HERSHEY_DUPLEX
    sizes = [cv2.getTextSize(t, font, scale, 3)[0] for t in lines]
    w = max(s[0] for s in sizes) + 24
    lh = max(s[1] for s in sizes) + 22
    img = np.zeros((lh * len(lines) + 12, w), np.uint8)
    for i, t in enumerate(lines):
        cv2.putText(img, t, (12, lh * (i + 1) - 6), font, scale, 1, 3, cv2.LINE_8)
    return img


def write_sup(path, cues, width=1280, height=720):
    """cues: [(start, end, text)] -> PGS file with palette 0 = transparent, 1 = white."""
    out = bytearray()
    for n, (a, b, text) in enumerate(cues):
        img = text_image(text)
        h, w = img.shape
        x, y = (width - w) // 2, height - h - 40
        pcs = struct.pack(">HHBHBBBB", width, height, 0x10, 2 * n, 0x80, 0, 0, 1) + \
            struct.pack(">HBBHH", 0, 0, 0, x, y)
        wds = struct.pack(">BBHHHH", 1, 0, x, y, w, h)
        pds = struct.pack(">BB", 0, 0) + bytes([0, 16, 128, 128, 0]) + bytes([1, 235, 128, 128, 255])
        data = _rle(img)
        ods = struct.pack(">HBB", 0, 0, 0xC0) + struct.pack(">I", len(data) + 4)[1:] + struct.pack(">HH", w, h) + data
        out += _seg(0x16, a, pcs) + _seg(0x17, a, wds) + _seg(0x14, a, pds) + _seg(0x15, a, ods) + _seg(0x80, a, b"")
        clr = struct.pack(">HHBHBBBB", width, height, 0x10, 2 * n + 1, 0x00, 0, 0, 0)
        out += _seg(0x16, b, clr) + _seg(0x17, b, wds) + _seg(0x80, b, b"")
    open(path, "wb").write(bytes(out))
