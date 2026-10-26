#!/usr/bin/env python3
"""Draw the profile's header, project cards and toolbox into assets/.

Every glyph is converted to outlines, so the images look the same whatever
fonts a visitor has installed. The fonts themselves are not kept here:

    python3 tools/render.py --fonts DIR

DIR must hold NotoSansSC-VF.ttf, EBGaramond.ttf and EBGaramond-Italic.ttf
(the variable builds from Google Fonts, SIL Open Font License).

One warm family throughout: ivory paper, oat and clay for the flat shapes,
warm ink for type and for the loose line drawn over them.
"""
import argparse
import math
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = Path(__file__).resolve().parent.parent / 'assets'

LIGHT = dict(
    paper='#F5F1EA', edge='#E6DFD3', ink='#2F2A25', ink2='#5F574E', ink3='#978E82',
    oat='#E4D9C6', clay='#C79079', clay_soft='#EBD2C4',
)
DARK = dict(
    paper='#22201D', edge='#36322C', ink='#EDE7DD', ink2='#BFB6A8', ink3='#877F73',
    oat='#3B352D', clay='#B9846C', clay_soft='#4A3B32',
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


# ---------------------------------------------------------------- header

def header(F, pal):
    w, h = 1000, 300
    # a line that wanders, turns once on itself, and ends in a small spark
    walk = catmull_rom([(606, 252), (660, 236), (706, 210), (722, 182), (704, 166), (686, 180),
                        (700, 206), (754, 214), (816, 176), (862, 130), (884, 112)])
    art = [
        f'<path d="{blob(812, 158, 116, [1, .94, 1.04, .97, 1.03, .9, 1.05, .95], .3)}" fill="{pal["oat"]}"/>',
        f'<path d="{blob(690, 214, 56, [1, .9, 1.07, .95, 1.03, .93], .8)}" fill="{pal["clay_soft"]}"/>',
        '<g>',
        stroke('M' + points(walk), pal),
        stroke(sparkle(900, 98, 17), pal, 2),
        stroke('M928 70l7-7M934 96h10M918 126l5 6', pal, 1.8),
        '</g>',
        f'<circle cx="884" cy="112" r="4.5" fill="{pal["clay"]}"/>',
    ]
    body = [
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="16" fill="{pal["paper"]}" stroke="{pal["edge"]}"/>',
        *art,
        text([(F['cn'], '你好，我是')], 18, 64, 96, pal['ink2'], track=0.12),
        text([(F['name'], 'Wynter Yaxley')], 64, 60, 166, pal['ink']),
        text([(F['cn'], '兴之所至，信手为之。')], 20, 64, 214, pal['ink2'], track=0.1),
        text([(F['en_it'], 'Made for the joy of it — useful or not.')], 19, 64, 246, pal['ink3']),
    ]
    return svg(w, h, '\n'.join(body), 'Wynter Yaxley',
               '你好，我是 Wynter Yaxley。兴之所至，信手为之。Made for the joy of it — useful or not.')


# ---------------------------------------------------------------- cards

def art_bridge(pal):
    return [
        f'<path d="{blob(84, 62, 40, [1, .92, 1.06, .96, 1.02, .9], .4)}" fill="{pal["oat"]}"/>',
        '<g>',
        stroke('M36 90H134', pal, 1.8),
        stroke('M60 90A25 25 0 0 1 110 90', pal),
        stroke('M40 86C60 86 66 52 85 52S110 86 130 86', pal),
        stroke('M66 102h20M94 110h14', pal, 1.6),
        '</g>',
        f'<circle cx="101" cy="58.5" r="4.2" fill="{pal["clay"]}"/>',
    ]


def art_calendar(pal):
    return [
        f'<path d="{blob(82, 64, 38, [1, .95, 1.05, .9, 1.04, .94], 1.1)}" fill="{pal["clay_soft"]}"/>',
        '<g transform="rotate(-6 80 66)">',
        stroke('M52 42H108Q114 42 114 48V90Q114 96 108 96H52Q46 96 46 90V48Q46 42 52 42Z', pal),
        stroke('M46 56H114M62 34v14M98 34v14', pal),
        *[f'<circle cx="{x}" cy="{y}" r="2.4" fill="{pal["ink"]}"/>'
          for x in (62, 80) for y in (70, 84)],
        stroke('M98 72a9 9 0 1 1-1-.5', pal, 1.8),
        '</g>',
        f'<circle cx="97.5" cy="80.5" r="3.4" fill="{pal["clay"]}"/>',
    ]


def card(F, pal, art, repo, cn, en, lang, tags, desc, shift):
    # the gutter between the two cards is drawn into them, so the README needs no spacing
    w, h, gutter = 500, 300, 10
    for runs, size, what in (([(F['cn'], cn)], 17, cn), ([(F['en'], en)], 14.5, en)):
        fits(runs, size, w - 64, what)
    tag_x = 48 + width([(F['label'], lang)], 14) + 16
    body = [
        f'<g transform="translate({gutter * shift} 0)">',
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="16" fill="{pal["paper"]}" stroke="{pal["edge"]}"/>',
        *art(pal),
        f'<path d="M452 46L466 32M455 32H466V43" fill="none" stroke="{pal["ink3"]}" stroke-width="1.7" '
        f'stroke-linecap="round" stroke-linejoin="round"/>',
        text([(F['title'], repo)], 26, 32, 160, pal['ink']),
        text([(F['cn'], cn)], 17, 32, 200, pal['ink2'], track=0.03),
        text([(F['en'], en)], 14.5, 32, 228, pal['ink3']),
        f'<circle cx="37" cy="263" r="4.5" fill="{pal["clay"]}"/>',
        text([(F['label'], lang)], 14, 48, 268, pal['ink2']),
        text([(F['en'], tags)], 14, tag_x, 268, pal['ink3']),
        '</g>',
    ]
    return svg(w + gutter, h, '\n'.join(body), repo, desc)


def card_bridge(F, pal):
    return card(F, pal, art_bridge, 'agent-control-bridge',
                '让 AI Agent 借 MCP 与桌面版 ChatGPT、Codex 对话。',
                'Lets AI agents talk to ChatGPT and Codex on the desktop.',
                'TypeScript', 'MCP · Windows',
                '让 AI Agent 借 MCP 与桌面版 ChatGPT、Codex 对话。Lets AI agents talk to ChatGPT and Codex on the desktop.',
                shift=0)


def card_lecture(F, pal):
    return card(F, pal, art_calendar, 'seu-lecture-cli',
                '东南大学讲座预约小工具，会先对一对你的课表。',
                'An unofficial SEU lecture helper that checks your timetable.',
                'Python', 'CLI · 非官方',
                '东南大学讲座预约小工具（非官方），会先对一对你的课表。An unofficial SEU lecture helper that checks your timetable.',
                shift=1)


# ---------------------------------------------------------------- toolbox

TOOLS = ['Python', 'TypeScript', 'Node.js', 'MCP', 'PowerShell', 'WSL']


def toolbox(F, pal):
    w, h, pill, gap, size = 1000, 60, 40, 10, 15
    widths = [width([(F['label'], name)], size) + 40 for name in TOOLS]
    x, y = 1, (h - pill) / 2  # flush left, under the heading
    body = []
    for name, pw in zip(TOOLS, widths):
        body += [
            f'<rect x="{num(x + .5)}" y="{num(y + .5)}" width="{num(pw - 1)}" height="{pill - 1}" '
            f'rx="{(pill - 1) / 2}" fill="{pal["paper"]}" stroke="{pal["edge"]}"/>',
            text([(F['label'], name)], size, x + pw / 2, h / 2 + 5.2, pal['ink2'], anchor='middle'),
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
