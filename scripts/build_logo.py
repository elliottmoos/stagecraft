"""Build docs/logo.svg (the mark and the "stagecraft" wordmark as outlines) and
docs/logo-mark.svg (the mark alone, for favicons).

The mark is the shape of a Stagecraft pipeline: one step, a stage of three parallel steps, one
step. The amber is spike tape. Both files carry their own light and dark colours.

The wordmark is Schibsted Grotesk Bold (SIL Open Font License), outlined so the logo needs no
web font. Fetch the font, then run:

    curl -s -A curl "https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@700" \\
      | grep -o 'https://[^)]*' | xargs curl -s -o /tmp/schibsted-700.ttf
    uv run --no-project --with fonttools --with uharfbuzz \\
      python scripts/build_logo.py /tmp/schibsted-700.ttf docs
"""

import sys
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

font_path, out_dir = Path(sys.argv[1]), Path(sys.argv[2])

INK_LIGHT, INK_DARK, TAPE = "#1a1e29", "#eef0f4", "#e8a317"
STYLE = f"""<style>
    .ink {{ fill: {INK_LIGHT}; }}
    .edge {{ stroke: {INK_LIGHT}; }}
    @media (prefers-color-scheme: dark) {{
      .ink {{ fill: {INK_DARK}; }}
      .edge {{ stroke: {INK_DARK}; }}
    }}
  </style>"""
MARK = f"""<g fill="none" class="edge" stroke-width="3.2" stroke-linecap="round">
    <path d="M14 32 L32 13 M14 32 L32 32 M14 32 L32 51 M32 13 L50 32 M32 32 L50 32 M32 51 L50 32"/>
  </g>
  <rect class="ink" x="6" y="25" width="14" height="14" rx="3.5"/>
  <rect x="25" y="6" width="14" height="14" rx="3.5" fill="{TAPE}"/>
  <rect x="25" y="25" width="14" height="14" rx="3.5" fill="{TAPE}"/>
  <rect x="25" y="44" width="14" height="14" rx="3.5" fill="{TAPE}"/>
  <rect class="ink" x="44" y="25" width="14" height="14" rx="3.5"/>"""

# Shape the wordmark with the font's own kerning, then outline each glyph.
blob = hb.Blob.from_file_path(str(font_path))
hb_font = hb.Font(hb.Face(blob))
buf = hb.Buffer()
buf.add_str("stagecraft")
buf.guess_segment_properties()
hb.shape(hb_font, buf, {"kern": True, "liga": False})

tt = TTFont(font_path)
upem = tt["head"].unitsPerEm
x_height = tt["OS/2"].sxHeight
glyph_set = tt.getGlyphSet()
order = tt.getGlyphOrder()

SIZE = 31.0  # wordmark size next to a 64-unit mark (the concept's 40px beside an 84px mark)
TRACKING = -0.02 * upem  # the concept's letter-spacing: -0.02em
scale = SIZE / upem
GAP = 14
baseline = 32 + x_height * scale / 2  # centre the x-height on the mark's middle row

pen = SVGPathPen(glyph_set)
x = 0.0
for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
    name = order[info.codepoint]
    # Font units, y up -> SVG units, y down, placed after the mark.
    transform = (scale, 0, 0, -scale, 64 + GAP + x * scale, baseline)
    glyph_set[name].draw(TransformPen(pen, transform))
    x += pos.x_advance + TRACKING
width = 64 + GAP + (x - TRACKING) * scale


def svg(view_width: float, width: float, height: float, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view_width:.1f} 64" '
        f'width="{width:.0f}" height="{height:.0f}" role="img" aria-labelledby="title">\n'
        f'  <title id="title">Stagecraft</title>\n  {STYLE}\n  {body}\n</svg>\n'
    )


lockup = svg(width, width * 1.5, 96, f'{MARK}\n  <path class="ink" d="{pen.getCommands()}"/>')
mark = svg(64, 64, 64, MARK)
(out_dir / "logo.svg").write_text(lockup)
(out_dir / "logo-mark.svg").write_text(mark)
print(f"logo.svg {width:.1f}x64, {len(lockup)} bytes; logo-mark.svg {len(mark)} bytes")
