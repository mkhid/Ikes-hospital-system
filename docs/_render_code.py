"""
Render syntax-highlighted code figures for the thesis.

Uses Python's built-in tokenize for highlighting and Pillow for layout, so it
adds no dependency to the project. Output is a light theme, which prints better
than a dark one and costs less ink.
"""
import io
import pathlib
import textwrap
import tokenize
from keyword import kwlist

from PIL import Image, ImageDraw, ImageFont

PROJECT = pathlib.Path(__file__).resolve().parent.parent
OUT = PROJECT / "docs" / "figures"

# ---------------------------------------------------------------- palette
BG          = (255, 255, 255)
GUTTER_BG   = (247, 250, 252)
GUTTER_FG   = (166, 180, 196)
RULE        = (222, 231, 240)
PLAIN       = (32, 43, 56)
KEYWORD     = (167, 29, 125)
STRING      = (24, 110, 66)
COMMENT     = (124, 140, 158)
NUMBER      = (184, 87, 14)
DEFNAME     = (17, 87, 160)
DECORATOR   = (184, 87, 14)
SELF        = (140, 90, 175)
BUILTIN     = (15, 118, 110)

HEADER_BG   = (15, 118, 110)
HEADER_FG   = (255, 255, 255)
SUBHDR_BG   = (236, 244, 246)
SUBHDR_FG   = (70, 92, 108)
CAP_BG      = (248, 250, 252)
CAP_FG      = (44, 60, 76)
CAP_ACCENT  = (15, 118, 110)
BORDER      = (205, 217, 229)

BUILTINS = {
    "len", "range", "set", "dict", "list", "int", "str", "float", "bool", "sum",
    "min", "max", "sorted", "any", "all", "print", "isinstance", "getattr",
    "enumerate", "zip", "abs", "round", "type", "super", "property", "hasattr",
}

FONT_DIR = pathlib.Path(r"C:\Windows\Fonts")
SIZE = 26
LINE_H = 36
PAD_X = 22


def _font(name, size=SIZE):
    return ImageFont.truetype(str(FONT_DIR / name), size)


REG = _font("consola.ttf")
BOLD = _font("consolab.ttf")
ITAL = _font("consolai.ttf")
UI_BOLD = ImageFont.truetype(str(FONT_DIR / "segoeuib.ttf"), 27)
UI_REG = ImageFont.truetype(str(FONT_DIR / "segoeui.ttf"), 22)
UI_SM = ImageFont.truetype(str(FONT_DIR / "segoeui.ttf"), 21)
UI_SM_B = ImageFont.truetype(str(FONT_DIR / "segoeuib.ttf"), 21)

CHAR_W = REG.getlength("M")


# ------------------------------------------------------------- highlight
def highlight(source):
    """Return per-line lists of (text, colour, font) segments."""
    lines = source.splitlines()
    # style grid: one entry per character
    styles = [[(PLAIN, REG) for _ in range(len(l) + 1)] for l in lines]

    def paint(srow, scol, erow, ecol, colour, font):
        for r in range(srow, erow + 1):
            if r - 1 >= len(styles):
                break
            row = styles[r - 1]
            a = scol if r == srow else 0
            b = ecol if r == erow else len(row)
            for c in range(a, min(b, len(row))):
                row[c] = (colour, font)

    try:
        toks = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        toks = []

    prev = None
    for tok in toks:
        num, val, (sr, sc), (er, ec), _ = tok
        if num == tokenize.COMMENT:
            paint(sr, sc, er, ec, COMMENT, ITAL)
        elif num == tokenize.STRING:
            paint(sr, sc, er, ec, STRING, REG)
        elif num == tokenize.NUMBER:
            paint(sr, sc, er, ec, NUMBER, REG)
        elif num == tokenize.NAME:
            if val in kwlist:
                paint(sr, sc, er, ec, KEYWORD, BOLD)
            elif val in ("self", "cls"):
                paint(sr, sc, er, ec, SELF, REG)
            elif prev and prev[1] in ("def", "class"):
                paint(sr, sc, er, ec, DEFNAME, BOLD)
            elif val in BUILTINS:
                paint(sr, sc, er, ec, BUILTIN, REG)
        elif num == tokenize.OP and val == "@" and sc == len(lines[sr - 1]) - len(lines[sr - 1].lstrip()):
            paint(sr, sc, er, ec, DECORATOR, REG)
        if num not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            prev = (num, val)

    out = []
    for line, style_row in zip(lines, styles):
        segs, cur, cur_style = [], "", None
        for i, ch in enumerate(line):
            st = style_row[i] if i < len(style_row) else (PLAIN, REG)
            if st != cur_style:
                if cur:
                    segs.append((cur, cur_style[0], cur_style[1]))
                cur, cur_style = ch, st
            else:
                cur += ch
        if cur:
            segs.append((cur, cur_style[0], cur_style[1]))
        out.append(segs)
    return out


# ---------------------------------------------------------------- render
def render(figure_no, title, subtitle, rows, caption, filename):
    """
    rows: list of (line_number | None, text). A None line number renders as an
    ellipsis marker, so an excerpt keeps the real line numbers on either side
    of the gap instead of renumbering sequentially.
    """
    source = "\n".join(text for _, text in rows)
    segs_per_line = highlight(source)
    raw_lines = source.splitlines()

    numbers = [n for n, _ in rows if n is not None]
    widest = max(numbers) if numbers else 1
    gutter_w = int(CHAR_W * (len(str(widest)) + 2)) + 14
    code_w = int(max((REG.getlength(l) for l in raw_lines), default=400)) + PAD_X * 2
    width = max(gutter_w + code_w, 1250)

    header_h, sub_h = 58, 44
    code_h = LINE_H * len(rows) + 24

    # wrap the caption to the image width
    cap_chars = max(40, int((width - 60) / UI_SM.getlength("n")))
    cap_lines = []
    for para in caption.split("\n"):
        cap_lines.extend(textwrap.wrap(para, cap_chars) or [""])
    cap_h = 22 + len(cap_lines) * 30 + 20

    height = header_h + sub_h + code_h + cap_h
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    # header
    d.rectangle([0, 0, width, header_h], fill=HEADER_BG)
    d.text((PAD_X, 15), f"{figure_no}   {title}", font=UI_BOLD, fill=HEADER_FG)

    # sub-header (file path)
    y = header_h
    d.rectangle([0, y, width, y + sub_h], fill=SUBHDR_BG)
    d.text((PAD_X, y + 11), subtitle, font=UI_REG, fill=SUBHDR_FG)
    y += sub_h

    # code area
    code_top = y
    d.rectangle([0, y, gutter_w, y + code_h], fill=GUTTER_BG)
    d.line([(gutter_w, y), (gutter_w, y + code_h)], fill=RULE, width=1)

    ty = y + 10
    for (n, _), segs in zip(rows, segs_per_line):
        if n is None:
            # Gap marker: an excerpt continues below with its real numbering.
            # Drawn as dots rather than U+22EE, which Consolas does not carry.
            cx = gutter_w + PAD_X + 6
            for dy in (10, 17, 24):
                d.ellipse([cx, ty + dy, cx + 3, ty + dy + 3], fill=GUTTER_FG)
            ty += LINE_H
            continue
        num = str(n)
        d.text((gutter_w - 12 - REG.getlength(num), ty), num, font=REG, fill=GUTTER_FG)
        x = gutter_w + PAD_X
        for text, colour, font in segs:
            d.text((x, ty), text, font=font, fill=colour)
            x += font.getlength(text)
        ty += LINE_H

    y = code_top + code_h
    d.line([(0, y), (width, y)], fill=RULE, width=1)

    # caption
    d.rectangle([0, y, width, height], fill=CAP_BG)
    d.rectangle([0, y, 5, height], fill=CAP_ACCENT)
    cy = y + 18
    for i, line in enumerate(cap_lines):
        d.text((PAD_X, cy), line, font=UI_SM_B if i == 0 else UI_SM, fill=CAP_FG)
        cy += 30

    d.rectangle([0, 0, width - 1, height - 1], outline=BORDER, width=1)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / filename
    img.save(path, "PNG", dpi=(200, 200))
    return path, width, height
