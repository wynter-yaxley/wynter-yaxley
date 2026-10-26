#!/usr/bin/env python3
"""Draw the profile's header, project cards and toolbox into assets/.

Every glyph is converted to outlines, so the images look the same whatever
fonts a visitor has installed. The fonts themselves are not kept here:

    python3 tools/render.py --fonts DIR

DIR must hold NotoSansSC-VF.ttf, EBGaramond.ttf and EBGaramond-Italic.ttf
(the variable builds from Google Fonts, SIL Open Font License).
"""
import argparse
import math
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = Path(__file__).resolve().parent.parent / 'assets'

# Morandi tones: each has a mid value and a pale ground to sit on.
LIGHT = dict(
    bg0='#F4F1EC', bg1='#ECEEEA', edge='#E2DED6',
    ink='#3A3D3B', ink2='#5E625F', ink3='#8E918C', on_tile='#FBFAF7',
    sage='#A9B8A5', sage_bg='#E6EBE4', blue='#A3B2BF', blue_bg='#E4E9ED',
    rose='#CDAEA7', rose_bg='#F2E7E3', sand='#D9C7A3', sand_bg='#F1EBDD',
    mauve='#B3A5B9', mauve_bg='#ECE7EE', stone='#B8B3AA', stone_bg='#ECE9E4',
)
DARK = dict(
    bg0='#222426', bg1='#1E2120', edge='#30343A',
    ink='#E6E3DD', ink2='#B9B6AF', ink3='#85847F', on_tile='#1F2123',
    sage='#7D8C79', sage_bg='#252A25', blue='#788795', blue_bg='#232A30',
    rose='#9A7F79', rose_bg='#2D2625', sand='#A3936F', sand_bg='#2C2922',
    mauve='#8A7D90', mauve_bg='#29252B', stone='#8C877F', stone_bg='#292826',
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


def catmull_rom(ctrl, steps=24):
    """Centripetal Catmull-Rom through ctrl; no cusps, no overshoot."""
    ctrl = [ctrl[0]] + ctrl + [ctrl[-1]]
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
    out.append(ctrl[-2])
    return out


# ---------------------------------------------------------------- header

def header(F, pal):
    w, h = 1000, 300
    # a loose line that wanders through the shapes and stops at the small dot
    ctrl = [(548, 252), (604, 238), (668, 214), (700, 188), (690, 166), (668, 174),
            (676, 200), (728, 208), (800, 176), (868, 120), (918, 88)]
    line = 'M' + 'L'.join(f'{x:.1f} {y:.1f}' for x, y in catmull_rom(ctrl))
    body = [
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop stop-color="{pal["bg0"]}"/><stop offset="1" stop-color="{pal["bg1"]}"/></linearGradient>'
        f'<clipPath id="clip"><rect width="{w}" height="{h}" rx="16"/></clipPath></defs>',
        '<g clip-path="url(#clip)">',
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
        f'<circle cx="806" cy="170" r="112" fill="{pal["sage"]}" opacity=".75"/>',
        f'<rect x="616" y="56" width="148" height="148" rx="30" fill="{pal["blue"]}" opacity=".72" '
        f'transform="rotate(-12 690 130)"/>',
        f'<path d="M838 300a76 76 0 0 1 152 0z" fill="{pal["rose"]}" opacity=".85"/>',
        f'<circle cx="652" cy="250" r="28" fill="{pal["sand"]}" opacity=".9"/>',
        f'<circle cx="928" cy="80" r="10" fill="{pal["mauve"]}"/>',
        f'<path d="{line}" fill="none" stroke="{pal["ink2"]}" stroke-width="1.6" stroke-linecap="round" '
        f'stroke-linejoin="round"/>',
        '</g>',
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="16" fill="none" stroke="{pal["edge"]}"/>',
        text([(F['cn'], '你好，我是')], 18, 64, 96, pal['ink2'], track=0.12),
        text([(F['name'], 'Wynter Yaxley')], 64, 60, 166, pal['ink']),
        text([(F['cn'], '兴之所至，信手为之。')], 20, 64, 214, pal['ink2'], track=0.1),
        text([(F['en_it'], 'Made for the joy of it — useful or not.')], 19, 64, 246, pal['ink3']),
    ]
    return svg(w, h, '\n'.join(body), 'Wynter Yaxley',
               '你好，我是 Wynter Yaxley。兴之所至，信手为之。Made for the joy of it — useful or not.')


# ---------------------------------------------------------------- cards

def icon_bridge(c):
    return (f'<path d="M46 68Q60 42 74 68" fill="none" stroke="{c}" stroke-width="2.4" stroke-linecap="round"/>'
            f'<circle cx="46" cy="68" r="4.2" fill="{c}"/><circle cx="74" cy="68" r="4.2" fill="{c}"/>')


def icon_calendar(c):
    return (f'<g fill="none" stroke="{c}" stroke-width="2.2" stroke-linecap="round">'
            f'<rect x="44" y="46" width="32" height="29" rx="5"/><path d="M44 55H76M53 41v8M67 41v8"/></g>'
            f'<rect x="61" y="62" width="8" height="7" rx="1.6" fill="{c}"/>')


def motif_arcs(tone):
    """Ripples of a bridge, low in the corner."""
    return ''.join(f'<circle cx="472" cy="330" r="{r}" fill="none" stroke="{tone}" stroke-width="1.4" '
                   f'opacity="{o}"/>' for r, o in ((56, .5), (88, .35), (120, .22)))


def motif_grid(tone):
    """A corner of a timetable, a few slots taken."""
    out = []
    for i in range(5):
        for j in range(3):
            taken = (i * 3 + j * 5) % 4 == 0
            out.append(f'<rect x="{364 + i * 30}" y="{236 + j * 30}" width="22" height="22" rx="5" '
                       f'fill="{tone}" opacity="{.42 if taken else .16}"/>')
    return ''.join(out)


def card(F, pal, tone, icon, motif, repo, cn, en, lang, tags, desc, shift):
    # the gutter between the two cards is drawn into them, so the README needs no spacing
    w, h, gutter = 500, 300, 10
    for runs, size, what in (([(F['cn'], cn)], 17, cn), ([(F['en'], en)], 14.5, en)):
        fits(runs, size, w - 64, what)
    tag_x = 52 + width([(F['label'], lang)], 14) + 18
    body = [
        f'<defs><clipPath id="clip"><rect width="{w}" height="{h}" rx="16"/></clipPath></defs>',
        f'<g transform="translate({gutter * shift} 0)">',
        '<g clip-path="url(#clip)">',
        f'<rect width="{w}" height="{h}" fill="{pal[tone + "_bg"]}"/>',
        motif(pal[tone]),
        '</g>',
        f'<rect x="32" y="32" width="56" height="56" rx="15" fill="{pal[tone]}"/>',
        icon(pal['on_tile']),
        f'<path d="M452 46L466 32M455 32H466V43" fill="none" stroke="{pal["ink3"]}" stroke-width="1.7" '
        f'stroke-linecap="round" stroke-linejoin="round"/>',
        text([(F['title'], repo)], 26, 32, 146, pal['ink']),
        text([(F['cn'], cn)], 17, 32, 188, pal['ink2'], track=0.03),
        text([(F['en'], en)], 14.5, 32, 216, pal['ink3']),
        f'<circle cx="38" cy="262" r="5.5" fill="{pal[tone]}"/>',
        text([(F['label'], lang)], 14, 52, 267, pal['ink2']),
        text([(F['en'], tags)], 14, tag_x, 267, pal['ink3']),
        '</g>',
    ]
    return svg(w + gutter, h, '\n'.join(body), repo, desc)


def card_bridge(F, pal):
    return card(F, pal, 'blue', icon_bridge, motif_arcs, 'agent-control-bridge',
                '让 AI Agent 借 MCP 与桌面版 ChatGPT、Codex 对话。',
                'Lets AI agents talk to ChatGPT and Codex on the desktop.',
                'TypeScript', 'MCP · Windows',
                '让 AI Agent 借 MCP 与桌面上的 ChatGPT、Codex 对话。Lets AI agents talk to ChatGPT and Codex on the desktop.',
                shift=0)


def card_lecture(F, pal):
    return card(F, pal, 'rose', icon_calendar, motif_grid, 'seu-lecture-cli',
                '东南大学讲座预约小工具，会先对一对你的课表。',
                'An unofficial SEU lecture helper that checks your timetable.',
                'Python', 'CLI · 非官方',
                '东南大学讲座预约小工具（非官方），会先对一对你的课表。An unofficial SEU lecture helper that checks your timetable.',
                shift=1)


# ---------------------------------------------------------------- toolbox

TOOLS = [('Python', 'sage'), ('TypeScript', 'blue'), ('Node.js', 'sand'),
         ('MCP', 'mauve'), ('PowerShell', 'stone'), ('WSL', 'rose')]


def toolbox(F, pal):
    w, h, pill, gap, size = 1000, 60, 40, 12, 15
    widths = [width([(F['label'], name)], size) + 58 for name, _ in TOOLS]
    x = 1  # flush left, under the heading
    body = []
    for (name, tone), pw in zip(TOOLS, widths):
        y = (h - pill) / 2
        body += [
            f'<rect x="{num(x)}" y="{num(y)}" width="{num(pw)}" height="{pill}" rx="{pill / 2}" '
            f'fill="{pal[tone + "_bg"]}"/>',
            f'<circle cx="{num(x + 22)}" cy="{h / 2}" r="5" fill="{pal[tone]}"/>',
            text([(F['label'], name)], size, x + 36, h / 2 + 5.2, pal['ink2']),
        ]
        x += pw + gap
    return svg(w, h, '\n'.join(body), '常用 · Toolbox', ' · '.join(n for n, _ in TOOLS))


# ----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fonts', required=True, type=Path)
    args = ap.parse_args()

    sans = Font(args.fonts / 'NotoSansSC-VF.ttf')
    eb = Font(args.fonts / 'EBGaramond.ttf')
    ebi = Font(args.fonts / 'EBGaramond-Italic.ttf')
    F = dict(cn=sans.at(400), en=sans.at(400), label=sans.at(500), title=sans.at(600),
             name=eb.at(500), en_it=ebi.at(400))
    pages = dict(header=header, card_bridge=card_bridge, card_lecture=card_lecture, toolbox=toolbox)
    OUT.mkdir(exist_ok=True)
    for name, draw in pages.items():
        for mode, pal in (('light', LIGHT), ('dark', DARK)):
            path = OUT / f'{name.replace("_", "-")}-{mode}.svg'
            path.write_text(draw(F, pal), encoding='utf-8')
            print(f'{path.name:24} {path.stat().st_size / 1024:6.1f} KB')


if __name__ == '__main__':
    main()
