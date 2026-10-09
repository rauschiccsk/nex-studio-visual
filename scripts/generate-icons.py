#!/usr/bin/env python3
"""
Renders the raster forms of the app mark from the template ``frontend/public/icon.svg``.

Why a custom generator: this environment has NO tool for producing PNGs - no Pillow,
ImageMagick, rsvg-convert or inkscape. The monogram is plain geometry (polyline
strokes), so it can be rendered exactly and without any dependency. The project thus
did not gain a 30 MB library for three images.

The template is PARSED, not retyped. The numbers (corner radius, colours, stroke width,
points) are taken from it, so the forms cannot drift apart. Every PNG additionally
carries the template fingerprint - when the template changes and the icons are not
regenerated, the test catches it.

Usage:  python3 scripts/generate-icons.py
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "frontend" / "public" / "icon.svg"
OUTPUT = ROOT / "frontend" / "public"

#: Share of the canvas the content of the maskable form may occupy. Systems crop the
#: icon to a circle or another shape; without this margin the monogram would lose its edges.
MASKABLE_SCALE = 0.72


# -- Template -----------------------------------------------------------------


def _color(text: str) -> tuple[int, int, int]:
    z = text.lstrip("#")
    return int(z[0:2], 16), int(z[2:4], 16), int(z[4:6], 16)


def load_template(path: Path) -> dict:
    svg = path.read_text(encoding="utf-8")

    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    if not m:
        raise SystemExit("template has no viewBox - cannot tell its size")
    side = int(m.group(1))
    if side != int(m.group(2)):
        raise SystemExit("template is not square")

    m = re.search(r'<rect[^>]*rx="(\d+)"[^>]*fill="(#[0-9a-fA-F]{6})"', svg)
    if not m:
        raise SystemExit("template has no background square")
    radius, background = int(m.group(1)), _color(m.group(2))

    m = re.search(r'stroke="(#[0-9a-fA-F]{6})"[^>]*stroke-width="(\d+)"', svg)
    if not m:
        raise SystemExit("template has no monogram stroke description")
    stroke_color, stroke_width = _color(m.group(1)), int(m.group(2))

    strokes: list[list[tuple[float, float]]] = []
    for d in re.findall(r'<path\s+d="([^"]+)"', svg):
        # Polylines of ANY length: every M/L command contributes one point.
        points = [(float(x), float(y)) for x, y in re.findall(r"[ML]\s*(\d+)\s+(\d+)", d)]
        if len(points) < 2:
            raise SystemExit(f"stroke `{d}` has fewer than two points")
        strokes.append(points)
    if not strokes:
        raise SystemExit("template has not a single monogram stroke")

    return {
        "side": side,
        "radius": radius,
        "background": background,
        "stroke_color": stroke_color,
        "stroke_width": stroke_width,
        "strokes": strokes,
        "fingerprint": hashlib.sha256(svg.encode("utf-8")).hexdigest()[:16],
    }


# -- Rendering ----------------------------------------------------------------


def _distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    length2 = vx * vx + vy * vy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / length2))
    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
    return math.hypot(dx, dy)


def _distance_to_rounded_square(px: float, py: float, side: float, r: float) -> float:
    """Negative value = inside. With r = 0 it is a plain square."""
    s = side / 2.0
    qx, qy = abs(px - s) - (s - r), abs(py - s) - (s - r)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - r


def _coverage(distance: float, pixel_radius: float) -> float:
    """Smooth transition on the edge - without it the edges would be jagged."""
    return max(0.0, min(1.0, 0.5 - distance / (2.0 * pixel_radius)))


def render(template: dict, size: int, maskable: bool) -> bytearray:
    side = template["side"]
    scale = size / side
    pixel = 1.0 / scale  # size of an output pixel, converted into template units

    radius = 0.0 if maskable else template["radius"]
    # Maskable form: shrink the content towards the centre, the background fills the whole canvas.
    shrink = MASKABLE_SCALE if maskable else 1.0
    center = side / 2.0

    pr, pg, pb = template["background"]
    tr, tg, tb = template["stroke_color"]
    half_stroke = template["stroke_width"] / 2.0 * shrink

    segments: list[tuple[float, float, float, float]] = []
    for stroke in template["strokes"]:
        pts = [(center + (x - center) * shrink, center + (y - center) * shrink) for x, y in stroke]
        segments += [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(len(pts) - 1)]

    image = bytearray(size * size * 4)
    for py in range(size):
        y = (py + 0.5) * pixel
        row = py * size * 4
        for px in range(size):
            x = (px + 0.5) * pixel

            if maskable:
                # The maskable form must fill the canvas COMPLETELY. A softened edge would
                # leave a translucent rim after cropping - the system crops the icon itself
                # and expects a full square.
                a_background = 1.0
            else:
                a_background = _coverage(_distance_to_rounded_square(x, y, side, radius), pixel)
                if a_background <= 0.0:
                    continue

            d_stroke = min(_distance_to_segment(x, y, *s) for s in segments) - half_stroke
            a_stroke = _coverage(d_stroke, pixel)

            # The monogram lies ON the background, so the colours blend by its coverage.
            r = round(pr + (tr - pr) * a_stroke)
            g = round(pg + (tg - pg) * a_stroke)
            b = round(pb + (tb - pb) * a_stroke)

            i = row + px * 4
            image[i] = r
            image[i + 1] = g
            image[i + 2] = b
            image[i + 3] = round(255 * a_background)
    return image


# -- PNG writing ----------------------------------------------------------------


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def write_png(path: Path, size: int, image: bytearray, fingerprint: str) -> None:
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # filter "None"
        rows += image[y * size * 4 : (y + 1) * size * 4]

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        # Template fingerprint: when the template changes and the icons are not
        # regenerated, the test catches it.
        + _chunk(b"tEXt", b"nex-icon-source\x00" + fingerprint.encode("ascii"))
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _chunk(b"IEND", b"")
    )


def main() -> None:
    template = load_template(TEMPLATE)
    print(f"template: {TEMPLATE.name}  fingerprint {template['fingerprint']}")

    for name, size, maskable in (
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-512-maskable.png", 512, True),
    ):
        image = render(template, size, maskable)
        path = OUTPUT / name
        write_png(path, size, image, template["fingerprint"])
        print(f"  {name:<24} {size}x{size}  {path.stat().st_size:>7} B{'  (with crop margin)' if maskable else ''}")


if __name__ == "__main__":
    main()
