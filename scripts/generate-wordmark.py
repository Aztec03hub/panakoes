#!/usr/bin/env python3
"""Generate the PANA|KOES wordmark SVG from the Inter Tight 800 woff2.

The wordmark is intentionally a pre-rendered SVG with glyphs converted to
outlines: CSS-typeset versions rendered differently across OS / browser /
DPI (baseline and padding rounding made the red box look non-uniform on
Windows while measuring perfect on Linux). Outlines are pixel-identical
everywhere.

Design (Phil, 2026-06-03): the word PANAKOES in Inter Tight 800 with
-0.025em tracking, KOES split slightly right of PANA, PANA wrapped in a
solid uniform rectangle of signal red (#D4202F) with paper (#F5EFE6)
type, KOES in ink (#0F0E0C). The red margin is identical on all four
sides of the PANA ink (measured to ink extents, not advance widths).

Usage:
    uv run --with fonttools --with brotli python3 scripts/generate-wordmark.py

Writes panakoes_site/assets/images/wordmark.svg.
"""

from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "panakoes_site/assets/fonts/inter-tight-800-latin.woff2"
OUT = ROOT / "panakoes_site/assets/images/wordmark.svg"

SIGNAL = "#D4202F"
PAPER = "#F5EFE6"
INK = "#0F0E0C"
TRACK_EM = -0.025  # letter-spacing, matches the site's display type
PAD = 341          # uniform red margin in font units (2048 upm)
GAP_EM = 0.12      # split between PANA box and KOES


def main() -> None:
    font = TTFont(str(FONT))
    upm = font["head"].unitsPerEm
    cap = font["OS/2"].sCapHeight
    glyphset = font.getGlyphSet()
    cmap = font.getBestCmap()

    outlines: dict[str, str] = {}
    widths: dict[str, int] = {}
    ink_bounds: dict[str, tuple] = {}
    for ch in set("PANAKOES"):
        gname = cmap[ord(ch)]
        pen = SVGPathPen(glyphset)
        glyphset[gname].draw(pen)
        outlines[ch] = pen.getCommands()
        widths[ch] = font["hmtx"][gname][0]
        bp = BoundsPen(glyphset)
        glyphset[gname].draw(bp)
        ink_bounds[ch] = bp.bounds

    track = TRACK_EM * upm

    def layout(word: str) -> list[tuple[str, float]]:
        x, parts = 0.0, []
        for ch in word:
            parts.append((ch, x))
            x += widths[ch] + track
        return parts

    pana, koes = layout("PANA"), layout("KOES")
    pana_l = pana[0][1] + ink_bounds["P"][0]
    pana_r = pana[-1][1] + ink_bounds["A"][2]
    koes_l = koes[0][1] + ink_bounds["K"][0]
    koes_r = koes[-1][1] + ink_bounds["S"][2]

    box_w = (pana_r - pana_l) + 2 * PAD
    height = cap + 2 * PAD
    gap = GAP_EM * upm
    total_w = box_w + gap + (koes_r - koes_l)

    def emit(parts: list, ink_l: float, x0: float, fill: str) -> str:
        g = [
            f'<g transform="translate({x0 - ink_l:.0f},{PAD + cap:.0f}) '
            f'scale(1,-1)" fill="{fill}">'
        ]
        for ch, x in parts:
            g.append(f'<path transform="translate({x:.0f},0)" d="{outlines[ch]}"/>')
        g.append("</g>")
        return "".join(g)

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_w:.0f} {height:.0f}" role="img" '
        f'aria-label="PANAKOES">',
        f'<rect width="{box_w:.0f}" height="{height:.0f}" fill="{SIGNAL}"/>',
        emit(pana, pana_l, PAD, PAPER),
        emit(koes, koes_l, box_w + gap, INK),
        "</svg>",
    ]
    OUT.write_text("\n".join(svg) + "\n")
    print(f"wrote {OUT} ({total_w:.0f}x{height:.0f} font units)")


if __name__ == "__main__":
    main()
