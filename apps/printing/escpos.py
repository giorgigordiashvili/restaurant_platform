"""
Minimal ESC/POS encoder: a rendered ticket image becomes raster bytes any
cheap thermal printer understands (Xprinter, Goojprt, Epson TM clones).

We deliberately do not depend on python-escpos on the server: the only
thing we need is the raster-image command, a feed, a cut and a cash-drawer
kick, and the byte sequences are stable across vendors. The print bridge
(tools/print_bridge) just streams these bytes to the device.
"""

from __future__ import annotations

from PIL import Image

ESC = b"\x1b"
GS = b"\x1d"

INIT = ESC + b"@"
ALIGN_LEFT = ESC + b"a\x00"
FEED = b"\n"
FULL_CUT = GS + b"V\x00"
PARTIAL_CUT = GS + b"V\x01"
# ESC p m t1 t2: pulse drawer pin 2 for ~100 ms (t1 * 2 ms on, t2 * 2 ms off).
DRAWER_KICK = ESC + b"p\x00\x32\xfa"

# Printable dots per paper width; 203 dpi heads.
PAPER_WIDTH = {"80": 576, "58": 384}
BAND_HEIGHT = 128  # rows per GS v 0 block; keeps command sizes small for slow serial links


def raster_block(image: Image.Image) -> bytes:
    """One ``GS v 0`` block for a 1-bit image (black = ink)."""
    width, height = image.size
    row_bytes = (width + 7) // 8
    # Pillow "1" mode packs bits with 1 = white; invert so 1 = black as ESC/POS expects.
    data = image.convert("1").point(lambda p: 255 - p).tobytes()
    header = GS + b"v0" + bytes([0, row_bytes & 0xFF, row_bytes >> 8, height & 0xFF, height >> 8])
    return header + data


def image_to_escpos(image: Image.Image, *, paper: str = "80", cut: bool = True, drawer: bool = False) -> bytes:
    """
    Wrap a rendered ticket (any width; scaled down to the paper if wider)
    in ESC/POS: init, raster bands, feed, cut, optional drawer kick.
    """
    width = PAPER_WIDTH.get(str(paper), 576)
    if image.width != width:
        ratio = width / image.width
        image = image.resize((width, max(1, int(image.height * ratio))))
    mono = image.convert("L").point(lambda p: 0 if p < 160 else 255).convert("1")
    out = bytearray(INIT + ALIGN_LEFT)
    for top in range(0, mono.height, BAND_HEIGHT):
        band = mono.crop((0, top, mono.width, min(top + BAND_HEIGHT, mono.height)))
        out += raster_block(band)
    out += FEED * 4
    if cut:
        out += PARTIAL_CUT
    if drawer:
        out += DRAWER_KICK
    return bytes(out)


def text_only(lines: list[str], *, cut: bool = True) -> bytes:
    """ASCII-only fallback (used for the bridge self-test)."""
    out = bytearray(INIT)
    for line in lines:
        out += line.encode("ascii", "replace") + FEED
    out += FEED * 4
    if cut:
        out += PARTIAL_CUT
    return bytes(out)
