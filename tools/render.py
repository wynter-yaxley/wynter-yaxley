#!/usr/bin/env python3
"""Draw the profile's header, project cards and toolbox into assets/.

Every glyph is converted to outlines, so the images look the same whatever
fonts a visitor has installed. The fonts themselves are not kept here:

    python3 tools/render.py --fonts DIR

DIR must hold NotoSansSC-VF.ttf, EBGaramond.ttf and EBGaramond-Italic.ttf
(the variable builds from Google Fonts, SIL Open Font License).

Warm white paper and near-black type; colour lives only in the prints (sage, ochre,
a clay sun, a slate frame). Type and line break out of the frames that hold them.
"""
import argparse
import math
import random
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = Path(__file__).resolve().parent.parent / 'assets'

LIGHT = dict(
    paper='#FBF9F6', edge='#E8E2D8', ink='#2B2723', ink2='#5D564D', ink3='#958C80',
    clay='#C88A6E', clay_soft='#EDCFBF', slate='#7F97A6', feather='#F8F6F0',
    sage='#C2CCB3', sage_dot='#AAB89C', sage_deep='#56664F',
    ochre='#E6D9BF', ochre_dot='#D2C19C', ochre_deep='#7E6A45',
)
DARK = dict(
    paper='#1E1D1B', edge='#34302B', ink='#EEE8DE', ink2='#C0B7AA', ink3='#877E72',
    clay='#BE8669', clay_soft='#4B3A31', slate='#86A0AF', feather='#8E9384',
    sage='#2E372B', sage_dot='#3B4637', sage_deep='#A8B99E',
    ochre='#352F24', ochre_dot='#463D2E', ochre_deep='#CDB98F',
)

def num(v):
    s = f'{v:.1f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


# ---------------------------------------------------------------- type

def _subtables(tt, table, tag):
    if table not in tt:
        return
    t = tt[table].table
    seen = set()
    for fr in t.FeatureList.FeatureRecord:
        if fr.FeatureTag != tag:
            continue
        for i in fr.Feature.LookupListIndex:
            if i in seen:
                continue
            seen.add(i)
            lookup = t.LookupList.Lookup[i]
            for st in lookup.SubTable:
                kind = lookup.LookupType
                if hasattr(st, 'ExtSubTable'):
                    kind, st = st.ExtensionLookupType, st.ExtSubTable
                yield kind, st


class Kerner:
    """Pair kerning from GPOS 'kern' (formats 1 and 2)."""

    def __init__(self, tt):
        self.tables = []
        for kind, st in _subtables(tt, 'GPOS', 'kern'):
            if kind != 2:
                continue
            cover = st.Coverage.glyphs
            if st.Format == 1:
                pairs = {}
                for first, ps in zip(cover, st.PairSet):
                    pairs[first] = {r.SecondGlyph: getattr(r.Value1, 'XAdvance', 0) or 0
                                    for r in ps.PairValueRecord}
                self.tables.append((1, pairs))
            else:
                matrix = [[getattr(c2.Value1, 'XAdvance', 0) or 0 for c2 in c1.Class2Record]
                          for c1 in st.Class1Record]
                self.tables.append((2, (set(cover), st.ClassDef1.classDefs,
                                        st.ClassDef2.classDefs, matrix)))

    def __call__(self, a, b):
        for fmt, data in self.tables:
            if fmt == 1:
                if a in data and b in data[a]:
                    return data[a][b]
            elif a in data[0]:
                return data[3][data[1].get(a, 0)][data[2].get(b, 0)]
        return 0


class Font:
    def __init__(self, path):
        self.tt = TTFont(path)
        self.upm = self.tt['head'].unitsPerEm
        self.cmap = self.tt.getBestCmap()
        self.kern = Kerner(self.tt)
        self._subst = {}

    def subst(self, tag):
        if tag not in self._subst:
            m = {}
            for kind, st in _subtables(self.tt, 'GSUB', tag):
                if kind == 1:
                    m.update(st.mapping)
            self._subst[tag] = m
        return self._subst[tag]

    def at(self, wght):
        return Face(self, wght)


class Face:
    def __init__(self, font, wght):
        self.font = font
        self.gs = font.tt.getGlyphSet(location={'wght': wght})


class RelPen(BasePen):
    """SVG path data in relative coordinates, a third smaller than absolute.

    Each contour starts with an absolute M, so glyphs can share one path.
    """

    def __init__(self, gs):
        super().__init__(gs)
        self.d, self.at, self.start = [], (0.0, 0.0), (0.0, 0.0)

    def _rel(self, cmd, *pts):
        x0, y0 = self.at
        pts = [(round(x, 1), round(y, 1)) for x, y in pts]
        self.at = pts[-1]
        self.d.append(cmd + ' '.join(f'{num(x - x0)} {num(y - y0)}' for x, y in pts).replace(' -', '-'))

    def _moveTo(self, p):
        self.at = self.start = (round(p[0], 1), round(p[1], 1))
        self.d.append(f'M{num(self.at[0])} {num(self.at[1])}')

    def _lineTo(self, p):
        self._rel('l', p)

    def _qCurveToOne(self, p1, p2):
        self._rel('q', p1, p2)

    def _curveToOne(self, p1, p2, p3):
        self._rel('c', p1, p2, p3)

    def _closePath(self):
        self.d.append('z')
        self.at = self.start


def glyph_d(face, name, x, y, k):
    pen = RelPen(face.gs)
    face.gs[name].draw(TransformPen(pen, (k, 0, 0, -k, x, y)))
    return ''.join(pen.d)


def shape(runs, size, track=0.0):
    """Lay runs of (face, text[, features]) on one line; track is in em."""
    x, placed, prev = 0.0, [], None
    for run in runs:
        face, s, feats = (run + ((),))[:3]
        f, k = face.font, size / face.font.upm
        names = [f.cmap.get(ord(c), '.notdef') for c in s]
        for tag in feats:
            m = f.subst(tag)
            names = [m.get(n, n) for n in names]
        for n in names:
            if prev and prev[0] is f:
                x += f.kern(prev[1], n) * k
            placed.append((face, n, x, k))
            x += face.gs[n].width * k + track * size
            prev = (f, n)
    return placed, (x - track * size if placed else 0.0)


def text(runs, size, x, y, fill, anchor='start', track=0.0, attrs=''):
    placed, w = shape(runs, size, track)
    x0 = x - {'start': 0, 'middle': w / 2, 'end': w}[anchor]
    d = ''.join(glyph_d(face, n, x0 + gx, y, k) for face, n, gx, k in placed)
    return f'<path fill="{fill}" d="{d}"{attrs}/>'


def width(runs, size, track=0.0):
    return shape(runs, size, track)[1]


def fits(runs, size, room, what):
    if width(runs, size) > room:
        print(f'  warning: {what!r} is wider than {room}')


def svg(w, h, body, title, desc):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
<title id="t">{title}</title>
<desc id="d">{desc}</desc>
{body}
</svg>
'''


def catmull_rom(ctrl, steps=24, closed=False):
    """Centripetal Catmull-Rom through ctrl; no cusps, no overshoot."""
    ctrl = ctrl[-1:] + ctrl + ctrl[:2] if closed else [ctrl[0]] + ctrl + [ctrl[-1]]
    out = []
    for i in range(1, len(ctrl) - 2):
        p = ctrl[i - 1:i + 3]
        t = [0.0]
        for a, b in zip(p, p[1:]):
            t.append(t[-1] + (math.dist(a, b) or 1e-3) ** 0.5)
        for s in range(steps):
            u = t[1] + (t[2] - t[1]) * s / steps

            def lerp(a, b, ta, tb):
                return tuple(((tb - u) * x + (u - ta) * y) / (tb - ta) for x, y in zip(a, b))

            a1, a2, a3 = (lerp(p[j], p[j + 1], t[j], t[j + 1]) for j in range(3))
            b1, b2 = lerp(a1, a2, t[0], t[2]), lerp(a2, a3, t[1], t[3])
            out.append(lerp(b1, b2, t[1], t[2]))
    if not closed:
        out.append(ctrl[-2])
    return out


# ---------------------------------------------------------------- drawing

def points(pts):
    return 'L'.join(f'{x:.1f} {y:.1f}' for x, y in pts)


def blob(cx, cy, r, bumps, turn=0.0):
    """A soft, uneven round shape: one radius factor per evenly spaced point."""
    n = len(bumps)
    p = [(cx + r * b * math.cos(turn + 2 * math.pi * i / n), cy + r * b * math.sin(turn + 2 * math.pi * i / n))
         for i, b in enumerate(bumps)]
    return 'M' + points(catmull_rom(p, closed=True)) + 'Z'


def stroke(d, pal, w=2.2, color='ink'):
    return (f'<path d="{d}" fill="none" stroke="{pal[color]}" stroke-width="{w}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def sparkle(x, y, r):
    return (f'M{x} {y - r}Q{x} {y} {x + r} {y}Q{x} {y} {x} {y + r}'
            f'Q{x} {y} {x - r} {y}Q{x} {y} {x} {y - r}Z')


# ---------------------------------------------------------------- the feather
#
# One motif, cropped differently wherever it appears: a cuckoo's feather (Yaxley
# is the cuckoo's clearing), which is also a quill, for making things. It is
# printed in halftone, the way a riso print or an old engraving would carry it.

def halftone(pal, tone):
    """Two dot screens at 45 degrees: one darkens the ground, one prints the feather."""
    return (f'<pattern id="ground" width="4.2" height="4.2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<circle cx="2.1" cy="2.1" r="1.05" fill="{pal[tone + "_dot"]}"/></pattern>'
            f'<pattern id="ink" width="4.2" height="4.2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<circle cx="2.1" cy="2.1" r="1.8" fill="{pal["feather"]}"/></pattern>')


def feather(pal, tone, base, bend, tip, left, right, seed):
    rnd = random.Random(seed)

    def at(t):
        return tuple((1 - t) ** 2 * a + 2 * t * (1 - t) * b + t * t * c for a, b, c in zip(base, bend, tip))

    def normal(t):
        dx = 2 * (1 - t) * (bend[0] - base[0]) + 2 * t * (tip[0] - bend[0])
        dy = 2 * (1 - t) * (bend[1] - base[1]) + 2 * t * (tip[1] - bend[1])
        n = math.hypot(dx, dy)
        return -dy / n, dx / n

    def edge(t, side):
        t = min(t, 1.0)
        reach = (left if side > 0 else right) * math.sin(math.pi * max(0.0, min(1.0, (t - .1) / .92))) ** .6 * (1 - .25 * t)
        (x, y), (nx, ny) = at(t), normal(t)
        return x + side * nx * reach, y + side * ny * reach

    ts = [.12 + .88 * i / 180 for i in range(181)]
    vane = 'M' + points([edge(t, 1) for t in ts] + [edge(t, -1) for t in reversed(ts)]) + 'Z'
    gaps, barbs = [], []
    for side in (1, -1):
        # real vanes split where the barbs come unzipped: narrow, uneven, more of them
        for _ in range(5):
            t, gap = rnd.uniform(.22, .9), rnd.uniform(.012, .028)
            gaps.append('M' + points([at(t), edge(t + .05, side), edge(t + .05 + gap, side), at(t + .006)]) + 'Z')
        t = .15
        while t < .97:
            (x, y), (nx, ny) = at(t), normal(t)
            ex, ey = edge(t + .07, side)
            # each barb leaves the shaft square-ish and sweeps toward the tip
            cx, cy = x + side * nx * (abs(ex - x) + abs(ey - y)) * .35, y + side * ny * (abs(ex - x) + abs(ey - y)) * .35
            barbs.append(f'M{x:.1f} {y:.1f}Q{cx:.1f} {cy:.1f} {ex:.1f} {ey:.1f}')
            t += .011
    fluff = []
    for i in range(9):
        t = rnd.uniform(.05, .14)
        (x, y), (nx, ny) = at(t), normal(t)
        side, reach = rnd.choice((1, -1)), rnd.uniform(14, 34)
        fluff.append(f'M{x:.1f} {y:.1f}q{side * nx * reach * .6 + rnd.uniform(-8, 8):.1f} '
                     f'{side * ny * reach * .6 + rnd.uniform(-8, 8):.1f} {side * nx * reach:.1f} {side * ny * reach:.1f}')
    shaft = 'M' + points([at(i / 60) for i in range(61)])
    return [
        f'<path d="{vane}" fill="url(#ink)"/>',
        f'<path d="{"".join(gaps)}" fill="{pal[tone]}"/>',
        f'<path d="{"".join(barbs)}" fill="none" stroke="{pal[tone]}" stroke-width=".9" opacity=".7"/>',
        f'<path d="{"".join(fluff)}" fill="none" stroke="{pal["feather"]}" stroke-width="1.1" stroke-linecap="round"/>',
        f'<path d="{shaft}" fill="none" stroke="{pal[tone + "_deep"]}" stroke-width="2" stroke-linecap="round"/>',
    ]


def print_panel(pal, tone, x, y, w, h, *art):
    """A rounded print: ground, screen, then whatever is printed on it, cut to the edge."""
    return [
        f'<clipPath id="panel"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18"/></clipPath>',
        '<g clip-path="url(#panel)">',
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{pal[tone]}"/>',
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#ground)"/>',
        *art,
        '</g>',
    ]


# ---------------------------------------------------------------- header

def bird(x, y, r):
    """Two strokes for wings, the way a bird is written in the margin of a sketchbook."""
    return f'M{x - r} {y}Q{x - r / 2} {y - r * .6} {x} {y}Q{x + r / 2} {y - r * .6} {x + r} {y}'


def header(F, pal):
    """Wynter: warm and cold at once, so a winter print with a low warm sun.
    Yaxley: an old English place, geac-leah, the cuckoo's clearing; a bird leaves it."""
    w, h, m = 1000, 500, 72
    px, py, pw, ph = 548, 104, 380, 300
    # Yaxley starts where the t of Wynter does, so the step follows a letter, not a guess
    placed, _ = shape([(F['name'], 'Wynter')], 112)
    step = 66 + next(gx for face, n, gx, k in placed if n == 't')
    yaxley_w = width([(F['name_it'], 'Yaxley')], 112)
    motto_end = m + width([(F['en_it'], 'useful or not.')], 16)
    body = [
        f'<defs>{halftone(pal, "sage")}</defs>',
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="18" fill="{pal["paper"]}" stroke="{pal["edge"]}"/>',
        # a frame that missed its register, behind the print
        f'<rect x="{px + 14}" y="{py - 14}" width="{pw}" height="{ph}" rx="18" fill="none" stroke="{pal["slate"]}" '
        f'stroke-width="1.2"/>',
        *print_panel(pal, 'sage', px, py, pw, ph,
                     f'<circle cx="{px + 74}" cy="{py + 70}" r="26" fill="{pal["clay_soft"]}"/>',
                     *feather(pal, 'sage', (604, 480), (812, 336), (930, 70), 80, 52, seed=3)),
        # the bird has left the clearing; its way out is dotted
        f'<path d="M846 {py}Q852 82 872 66" fill="none" stroke="{pal["ink"]}" stroke-width="2" '
        f'stroke-dasharray="1 7" stroke-linecap="round"/>',
        stroke(bird(892, 60, 16), pal, 2.2),
        text([(F['cn'], '你好，我是')], 17, m, 104, pal['ink2'], track=0.14),
        text([(F['name'], 'Wynter')], 112, m - 6, 214, pal['ink']),
        text([(F['name_it'], 'Yaxley')], 112, step, 326, pal['ink']),
        text([(F['en'], '†')], 26, step + yaxley_w - 14, 250, pal['slate']),
        text([(F['cn'], '兴之所至，')], 17, m, 262, pal['ink2'], track=0.1),
        text([(F['cn'], '信手为之。')], 17, m, 288, pal['ink2'], track=0.1),
        text([(F['en_it'], 'Made for the joy of it,')], 16, m, 316, pal['ink3']),
        text([(F['en_it'], 'useful or not.')], 16, m, 336, pal['ink3']),
        f'<rect x="{motto_end + 4:.1f}" y="334" width="9" height="2.2" fill="{pal["slate"]}"/>',
        # a footnote, the way an old English gazetteer would give it
        f'<path d="M{m} 398h40" stroke="{pal["edge"]}" stroke-width="1.2"/>',
        text([(F['en'], '† '), (F['cn'], 'Yaxley，英格兰古地名，意为“杜鹃的林间空地”。')], 13.5, m, 424, pal['ink2'],
             track=0.04),
        text([(F['en_it'], 'from Old English gēac-lēah, the cuckoo’s clearing.')], 14, m + 9, 446, pal['ink3']),
        text([(F['label'], 'MAKING THINGS · EST. 2022')], 10.5, px + pw, 446, pal['slate'], anchor='end', track=0.24),
    ]
    return svg(w, h, '\n'.join(body), 'Wynter Yaxley',
               '你好，我是 Wynter Yaxley。兴之所至，信手为之。Made for the joy of it, useful or not. '
               'Yaxley，英格兰古地名，意为“杜鹃的林间空地”。From Old English gēac-lēah, the cuckoo’s clearing.')


# ---------------------------------------------------------------- cards

def card(F, pal, tone, quill, number, repo, cn, en, lang, tags, desc, shift):
    """Print above, words below, nothing boxed in; the numeral is too big for its print."""
    # the gutter between the two cards is drawn into them, so the README needs no spacing
    w, h, gutter, ph = 500, 384, 10, 230
    for runs, size, what in (([(F['cn'], cn)], 16.5, cn), ([(F['en'], en)], 14, en)):
        fits(runs, size, w - 40, what)
    tag_x = 20 + width([(F['label'], lang)], 14) + 16
    body = [
        f'<defs>{halftone(pal, tone)}</defs>',
        f'<g transform="translate({gutter * shift} 0)">',
        *print_panel(pal, tone, 0, 0, w, ph, *feather(pal, tone, *quill),
                     text([(F['name'], number)], 200, w - 26, ph + 40, pal[tone + '_deep'], anchor='end',
                          attrs=' opacity=".85"')),
        text([(F['title'], repo)], 25, 2, 278, pal['ink']),
        f'<path d="M{w - 22} 272L{w - 8} 258M{w - 19} 258H{w - 8}V269" fill="none" stroke="{pal["ink3"]}" '
        f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
        text([(F['cn'], cn)], 16.5, 2, 310, pal['ink2'], track=0.03),
        text([(F['en'], en)], 14, 2, 335, pal['ink3']),
        f'<circle cx="7" cy="365" r="4.5" fill="{pal["clay"]}"/>',
        text([(F['label'], lang)], 14, 20, 370, pal['ink2']),
        text([(F['en'], tags)], 14, tag_x, 370, pal['ink3']),
        '</g>',
    ]
    return svg(w + gutter, h, '\n'.join(body), repo, desc)


def card_bridge(F, pal):
    return card(F, pal, 'sage', ((-60, 330), (300, 226), (560, -40), 150, 104, 5), '01', 'agent-control-bridge',
                '让 AI Agent 借 MCP 与桌面版 ChatGPT、Codex 对话。',
                'Lets AI agents talk to ChatGPT and Codex on the desktop.',
                'TypeScript', 'MCP · Windows',
                '让 AI Agent 借 MCP 与桌面版 ChatGPT、Codex 对话。Lets AI agents talk to ChatGPT and Codex on the desktop.',
                shift=0)


def card_lecture(F, pal):
    return card(F, pal, 'ochre', ((560, 300), (196, 226), (-90, -30), 140, 110, 8), '02', 'seu-lecture-cli',
                '东南大学讲座预约小工具，会先对一对你的课表。',
                'An unofficial SEU lecture helper that checks your timetable.',
                'Python', 'CLI · 非官方',
                '东南大学讲座预约小工具（非官方），会先对一对你的课表。An unofficial SEU lecture helper that checks your timetable.',
                shift=1)


# ---------------------------------------------------------------- toolbox

TOOLS = ['Python', 'TypeScript', 'Node.js', 'MCP', 'PowerShell', 'WSL']


def toolbox(F, pal):
    """Pills on one line, warm and cool dots taking turns."""
    w, h, pill, gap, size = 1000, 60, 40, 10, 15
    widths = [width([(F['label'], name)], size) + 52 for name in TOOLS]
    x, body = 1, []
    for i, (name, pw) in enumerate(zip(TOOLS, widths)):
        y = (h - pill) / 2
        body += [
            f'<rect x="{num(x + .5)}" y="{y + .5}" width="{num(pw - 1)}" height="{pill - 1}" '
            f'rx="{(pill - 1) / 2}" fill="{pal["paper"]}" stroke="{pal["edge"]}"/>',
            f'<circle cx="{num(x + 20)}" cy="{y + pill / 2}" r="4" fill="{pal["clay" if i % 2 else "slate"]}"/>',
            text([(F['label'], name)], size, x + 32, y + pill / 2 + 5.2, pal['ink2']),
        ]
        x += pw + gap
    return svg(w, h, '\n'.join(body), '常用 · Toolbox', ' · '.join(TOOLS))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fonts', required=True, type=Path)
    args = ap.parse_args()

    sans = Font(args.fonts / 'NotoSansSC-VF.ttf')
    eb = Font(args.fonts / 'EBGaramond.ttf')
    ebi = Font(args.fonts / 'EBGaramond-Italic.ttf')
    F = dict(cn=sans.at(400), en=sans.at(400), label=sans.at(500), title=sans.at(600),
             name=eb.at(500), name_it=ebi.at(500), en_it=ebi.at(400))
    pages = dict(header=header, card_bridge=card_bridge, card_lecture=card_lecture, toolbox=toolbox)
    OUT.mkdir(exist_ok=True)
    for name, draw in pages.items():
        for mode, pal in (('light', LIGHT), ('dark', DARK)):
            path = OUT / f'{name.replace("_", "-")}-{mode}.svg'
            path.write_text(draw(F, pal), encoding='utf-8')
            print(f'{path.name:24} {path.stat().st_size / 1024:6.1f} KB')


if __name__ == '__main__':
    main()
