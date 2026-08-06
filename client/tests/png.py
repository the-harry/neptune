"""Minimal PNG decode + image diff. Standard library only (zlib).

Only what Chrome actually emits from Page.captureScreenshot is supported:
8-bit, non-interlaced, colour type 2 (RGB) or 6 (RGBA). Anything else raises
rather than guessing — a screenshot comparison that silently misreads its input
is worse than no comparison.
"""
from __future__ import annotations

import struct
import zlib


def decode(data: bytes):
    """-> (width, height, channels, raw pixel bytes)"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w, h, ch = 8, bytearray(), 0, 0, 0
    bitdepth = colortype = interlace = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length                     # 4 len + 4 type + body + 4 crc
        if ctype == b"IHDR":
            w, h, bitdepth, colortype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", body)
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
    if bitdepth != 8 or interlace != 0 or colortype not in (2, 6):
        raise ValueError(f"unsupported PNG (bitdepth={bitdepth} colour={colortype} interlace={interlace})")
    ch = 3 if colortype == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        if f == 1:                                     # Sub
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif f == 2:                                   # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:                                   # Average
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:                                   # Paeth
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif f != 0:
            raise ValueError(f"bad PNG filter {f}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, bytes(out)


def diff(a: bytes, b: bytes, tol: int = 12):
    """Fraction of pixels that differ by more than `tol` on any channel.

    A tolerance, not an exact match: antialiasing and a one-frame-different
    animation are not regressions, and a comparison that flags them gets muted
    within a week — at which point it is protecting nothing.

    -> (fraction 0..1, note) ; fraction is None when the two cannot be compared.
    """
    try:
        aw, ah, ac, ap = decode(a)
        bw, bh, bc, bp = decode(b)
    except Exception as exc:  # noqa: BLE001
        return None, f"undecodable: {exc}"
    if (aw, ah) != (bw, bh):
        return None, f"different size: {aw}x{ah} vs {bw}x{bh}"
    n = aw * ah
    bad = 0
    for i in range(n):
        ai, bi = i * ac, i * bc
        if (abs(ap[ai] - bp[bi]) > tol or
                abs(ap[ai + 1] - bp[bi + 1]) > tol or
                abs(ap[ai + 2] - bp[bi + 2]) > tol):
            bad += 1
    return bad / n, f"{bad} of {n} pixels"
