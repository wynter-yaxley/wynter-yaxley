#!/usr/bin/env python3
"""Draw the profile's header, project cards and toolbox into assets/.

Every glyph is converted to outlines, so the images look the same whatever
fonts a visitor has installed. The fonts themselves are not kept here:

    python3 tools/render.py --fonts DIR

DIR must hold NotoSansSC-VF.ttf, EBGaramond.ttf and EBGaramond-Italic.ttf
(the variable builds from Google Fonts, SIL Open Font License).

Warm ground (ivory, oat, clay) with one cool counterpart (sky, slate) and a
little moss as an accent; the colour fields are printed through a halftone screen;
shapes break out of the frames that are meant to hold them.
"""
import argparse
import datetime
import hashlib
import json
import math
import re
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = Path(__file__).resolve().parent.parent / 'assets'

LIGHT = dict(
    paper='#F5F1EA', edge='#E3DBCE', ink='#2B2723', ink2='#5D564D', ink3='#958C80',
    oat='#E5D9C5', clay='#C88A6E', clay_soft='#EDCFBF',
    sky='#D3DFE4', slate='#7F97A6',
    moss='#C5CEB4', moss_dot='#B0BC9E', sky_dot='#BDCDD5', oat_dot='#D8CAB1',
)
DARK = dict(
    paper='#1F1D1B', edge='#36312B', ink='#EEE8DE', ink2='#C0B7AA', ink3='#877E72',
    oat='#3A342C', clay='#BE8669', clay_soft='#4B3A31',
    sky='#26343B', slate='#86A0AF',
    moss='#38432F', moss_dot='#46523C', sky_dot='#30414A', oat_dot='#443D33',
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

def screens(pal):
    """Halftone screens at 45 degrees, so the colour fields read as printed."""
    return '<defs>' + ''.join(
        f'<pattern id="{name}" width="4.2" height="4.2" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<circle cx="2.1" cy="2.1" r="{r}" fill="{pal[name + "_dot"]}"/></pattern>'
        for name, r in (('sky', .95), ('moss', 1.2), ('oat', 1.1))) + '</defs>'


def printed(shape, pal, ink):
    """A shape laid down flat, then the screen over it."""
    return f'{shape.format(fill=pal[ink])}{shape.format(fill=f"url(#{ink})")}'


def bird(x, y, r):
    """Two strokes for wings, the way a bird is written in the margin of a sketchbook."""
    return f'M{x - r} {y}Q{x - r / 2} {y - r * .6} {x} {y}Q{x + r / 2} {y - r * .6} {x + r} {y}'


def header(F, pal):
    """Wynter: warm and cold at once, so a winter window with a low warm sun.
    Yaxley: an old English place, geac-leah, the cuckoo's clearing; a bird leaves it."""
    w, h, m = 1000, 480, 72
    wx, wy, ww, wh = 548, 124, 366, 248
    # Yaxley starts where the t of Wynter does, so the step follows a letter, not a guess
    placed, _ = shape([(F['name'], 'Wynter')], 112)
    step = 66 + next(gx for face, n, gx, k in placed if n == 't')
    flight = catmull_rom([(512, 404), (566, 392), (622, 366), (662, 330), (670, 298), (648, 282),
                          (628, 300), (650, 330), (712, 334), (782, 284), (830, 206), (856, 136), (870, 96), (880, 70)])
    exit_at = next(i for i, (x, y) in enumerate(flight) if y < wy)
    body = [
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="18" fill="{pal["paper"]}" stroke="{pal["edge"]}"/>',
        # back: the window, a frame that missed its register, a low sun
        screens(pal),
        printed(f'<rect x="{wx}" y="{wy}" width="{ww}" height="{wh}" fill="{{fill}}"/>', pal, 'sky'),
        f'<rect x="{wx + 14}" y="{wy - 14}" width="{ww}" height="{wh}" fill="none" stroke="{pal["slate"]}" '
        f'stroke-width="1.2"/>',
        f'<circle cx="{wx + 84}" cy="{wy + 86}" r="24" fill="{pal["clay_soft"]}"/>',
        f'<circle cx="{wx + 84}" cy="{wy + 86}" r="34" fill="none" stroke="{pal["clay"]}" stroke-width="1" '
        f'stroke-dasharray="2 6" stroke-linecap="round"/>',
        # middle: the clearing, too large for the window
        printed(f'<path d="{blob(808, 312, 100, [1, .94, 1.04, .97, 1.03, .9, 1.05, .95], .3)}" fill="{{fill}}"/>',
                pal, 'oat'),
        # moss stays an accent: one small stone beside the sun
        printed(f'<circle cx="{wx + 138}" cy="{wy + 132}" r="11" fill="{{fill}}"/>', pal, 'moss'),
        # front: type
        text([(F['cn'], '你好，我是')], 17, m, 96, pal['ink2'], track=0.14),
        text([(F['name'], 'Wynter')], 112, m - 6, 206, pal['ink']),
        text([(F['name_it'], 'Yaxley')], 112, step, 318, pal['ink']),
        text([(F['en'], '†')], 26, step + width([(F['name_it'], 'Yaxley')], 112) - 14, 242, pal['slate']),
        text([(F['cn'], '兴之所至，')], 17, m, 254, pal['ink2'], track=0.1),
        text([(F['cn'], '信手为之。')], 17, m, 280, pal['ink2'], track=0.1),
        text([(F['en_it'], 'Made for the joy of it,')], 16, m, 308, pal['ink3']),
        text([(F['en_it'], 'useful or not.')], 16, m, 328, pal['ink3']),
        # the line: drawn inside, dotted once it has flown
        stroke('M' + points(flight[:exit_at + 1]), pal),
        f'<path d="M{points(flight[exit_at:])}" fill="none" stroke="{pal["ink"]}" stroke-width="2" '
        f'stroke-dasharray="1 7" stroke-linecap="round"/>',
        stroke(bird(898, 60, 16), pal, 2.2),
        f'<circle cx="{flight[0][0]:.1f}" cy="{flight[0][1]:.1f}" r="4.6" fill="{pal["clay"]}"/>',
        # a footnote, the way an old English gazetteer would give it
        f'<path d="M{m} 388h40" stroke="{pal["edge"]}" stroke-width="1.2"/>',
        text([(F['en'], '† '), (F['cn'], 'Yaxley，英格兰古地名，意为“杜鹃的林间空地”。')], 13.5, m, 414, pal['ink2'],
             track=0.04),
        text([(F['en_it'], 'from Old English gēac-lēah, the cuckoo’s clearing.')], 14, m + 9, 436, pal['ink3']),
        text([(F['label'], 'MAKING THINGS · EST. 2022')], 10.5, wx + ww + 14, 436, pal['slate'],
             anchor='end', track=0.24),
    ]
    return svg(w, h, '\n'.join(body), 'Wynter Yaxley',
               '你好，我是 Wynter Yaxley。兴之所至，信手为之。Made for the joy of it, useful or not. '
               'Yaxley，英格兰古地名，意为“杜鹃的林间空地”。From Old English gēac-lēah, the cuckoo’s clearing.')


# ---------------------------------------------------------------- cards

def art_bridge(pal, x, y):
    """A window of sky with a bridge too wide for it."""
    return [
        printed(f'<rect x="{x}" y="{y}" width="150" height="112" fill="{{fill}}"/>', pal, 'sky'),
        printed(f'<path d="{blob(x + 108, y + 40, 30, [1, .92, 1.06, .96, 1.02, .9], .4)}" fill="{{fill}}"/>',
                pal, 'moss'),
        stroke(f'M{x - 26} {y + 84}H{x + 176}', pal, 1.8),
        stroke(f'M{x + 47} {y + 84}A28 28 0 0 1 {x + 103} {y + 84}', pal),
        stroke(f'M{x - 18} {y + 80}C{x + 22} {y + 80} {x + 46} {y + 46} {x + 75} {y + 46}'
               f'S{x + 128} {y + 80} {x + 168} {y + 80}', pal),
        stroke(f'M{x + 52} {y + 98}h22M{x + 84} {y + 106}h16', pal, 1.6),
        f'<circle cx="{x + 94}" cy="{y + 51}" r="4.4" fill="{pal["clay"]}"/>',
    ]


def art_calendar(pal, x, y):
    """A window, and a page of the timetable sliding out of it."""
    cx, cy = x + 82, y + 70
    return [
        printed(f'<rect x="{x}" y="{y}" width="150" height="112" fill="{{fill}}"/>', pal, 'sky'),
        f'<path d="{blob(x + 40, y + 34, 26, [1, .95, 1.05, .9, 1.04, .94], 1.1)}" fill="{pal["clay_soft"]}"/>',
        f'<g transform="rotate(-8 {cx} {cy})">',
        f'<rect x="{cx - 40}" y="{cy - 30}" width="80" height="70" rx="7" fill="{pal["paper"]}" '
        f'stroke="{pal["ink"]}" stroke-width="2.2"/>',
        stroke(f'M{cx - 40} {cy - 14}H{cx + 40}M{cx - 20} {cy - 40}v16M{cx + 20} {cy - 40}v16', pal),
        *[f'<circle cx="{cx + dx}" cy="{cy + dy}" r="2.5" fill="{pal["ink"]}"/>'
          for dx in (-22, -4) for dy in (6, 22)],
        stroke(f'M{cx + 26} {cy + 6}a10 10 0 1 1-1-.5', pal, 1.8),
        f'<circle cx="{cx + 16}" cy="{cy + 14}" r="3.6" fill="{pal["clay"]}"/>',
        '</g>',
    ]


def card(F, pal, art, number, repo, cn, en, lang, tags, desc, shift):
    # the gutter between the two cards is drawn into them, so the README needs no spacing
    w, h, gutter, top = 500, 330, 10, 30
    for runs, size, what in (([(F['cn'], cn)], 17, cn), ([(F['en'], en)], 14.5, en)):
        fits(runs, size, w - 64, what)
    tag_x = 48 + width([(F['label'], lang)], 14) + 16
    ghost = text([(F['name'], number)], 150, w - 18, h + 34, 'none', anchor='end',
                 attrs=f' stroke="{pal["slate"]}" stroke-width="1.1" opacity=".55"')
    body = [
        screens(pal),
        f'<g transform="translate({gutter * shift} 0)">',
        f'<defs><clipPath id="card"><rect y="{top}" width="{w}" height="{h - top}" rx="16"/></clipPath></defs>',
        f'<rect x=".5" y="{top + .5}" width="{w - 1}" height="{h - top - 1}" rx="16" fill="{pal["paper"]}" '
        f'stroke="{pal["edge"]}"/>',
        # a numeral too large for the card, cut off by its edge
        '<g clip-path="url(#card)">' + ghost + '</g>',
        # the window rises out of the card's top edge
        *art(pal, w - 196, 0),
        text([(F['label'], number)], 12, 32, top + 44, pal['slate'], track=0.24),
        f'<path d="M58 {top + 40}h28" stroke="{pal["slate"]}" stroke-width="1.2"/>',
        text([(F['title'], repo)], 27, 32, 186, pal['ink']),
        text([(F['cn'], cn)], 17, 32, 226, pal['ink2'], track=0.03),
        text([(F['en'], en)], 14.5, 32, 254, pal['ink3']),
        f'<circle cx="37" cy="293" r="4.5" fill="{pal["clay"]}"/>',
        text([(F['label'], lang)], 14, 48, 298, pal['ink2']),
        text([(F['en'], tags)], 14, tag_x, 298, pal['ink3']),
        '</g>',
    ]
    return svg(w + gutter, h, '\n'.join(body), repo, desc)


def card_bridge(F, pal):
    return card(F, pal, art_bridge, '01', 'agent-control-bridge',
                '让 AI Agent 借 MCP 与桌面版 ChatGPT、Codex 对话。',
                'Lets AI agents talk to ChatGPT and Codex on the desktop.',
                'TypeScript', 'MCP · Windows',
                '让 AI Agent 借 MCP 与桌面版 ChatGPT、Codex 对话。Lets AI agents talk to ChatGPT and Codex on the desktop.',
                shift=0)


def card_lecture(F, pal):
    return card(F, pal, art_calendar, '02', 'seu-lecture-cli',
                '东南大学讲座预约小工具，会先对一对你的课表。',
                'An unofficial SEU lecture helper that checks your timetable.',
                'Python', 'CLI · 非官方',
                '东南大学讲座预约小工具（非官方），会先对一对你的课表。An unofficial SEU lecture helper that checks your timetable.',
                shift=1)


# ---------------------------------------------------------------- stats

STATS = OUT / 'stats.json'
METRICS = [('stars', '收获星标', 'STARS EARNED', 'sky'),
           ('pull_requests', '拉取请求', 'PULL REQUESTS', 'oat'),
           ('contributions', '近一年贡献', 'CONTRIBUTIONS · 1 YR', 'moss'),
           ('repositories', '公开仓库', 'PUBLIC REPOS', 'oat')]


def stats_card(F, pal):
    """Four numbers, each breaking out of a small printed swatch, like the window above."""
    stats = json.loads(STATS.read_text(encoding='utf-8'))
    w, h, m = 1000, 248, 72
    col = (w - 2 * m) / len(METRICS)
    y, mo, d = (int(v) for v in stats['updated'].split('-'))
    when_cn = f'每 6 小时刷新 · 最近变动 {y} 年 {mo} 月 {d} 日'
    when_en = f'Refreshed every six hours · last changed {d} {datetime.date(y, mo, d):%B} {y}'
    body = [
        screens(pal),
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="18" fill="{pal["paper"]}" stroke="{pal["edge"]}"/>',
        text([(F['cn'], when_cn)], 12.5, w - m, 50, pal['ink3'], anchor='end', track=0.04),
        text([(F['en_it'], when_en)], 13, w - m, 70, pal['ink3'], anchor='end'),
    ]
    for i, (key, cn, en, tone) in enumerate(METRICS):
        x = m + i * col
        body += [
            printed(f'<rect x="{num(x)}" y="94" width="62" height="54" fill="{{fill}}"/>', pal, tone),
            # EB Garamond's default old-style figures: uneven heights, an old English ledger
            text([(F['name'], f'{stats[key]:,}')], 66, x + 20, 150, pal['ink']),
            text([(F['cn'], cn)], 15, x, 186, pal['ink2'], track=0.08),
            text([(F['label'], en)], 10.5, x, 208, pal['slate'], track=0.22),
        ]
    desc = '；'.join(f'{cn} {stats[key]:,}' for key, cn, en, tone in METRICS)
    return svg(w, h, '\n'.join(body), '足迹 · On GitHub', f'{desc}。{when_cn}。{when_en}.')


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
    pages = dict(header=header, card_bridge=card_bridge, card_lecture=card_lecture, stats=stats_card,
                 toolbox=toolbox)
    OUT.mkdir(exist_ok=True)
    # the content hash goes into the file name: GitHub's raw links drop query strings
    # and its CDN caches by path, so a changed drawing must live at a new path
    written = {}
    for name, draw in pages.items():
        for mode, pal in (('light', LIGHT), ('dark', DARK)):
            stem = f'{name.replace("_", "-")}-{mode}'
            data = draw(F, pal).encode('utf-8')
            path = OUT / f'{stem}.{hashlib.sha1(data).hexdigest()[:8]}.svg'
            path.write_bytes(data)
            written[stem] = path.name
            print(f'{path.name:34} {len(data) / 1024:6.1f} KB')
    for old in OUT.glob('*.svg'):
        if old.name not in written.values():
            old.unlink()
    readme = OUT.parent / 'README.md'
    src = readme.read_text(encoding='utf-8')
    src = re.sub(r'assets/([a-z-]+-(?:light|dark))(?:\.\w{8})?\.svg(?:\?v=\w+)?',
                 lambda m: f'assets/{written[m.group(1)]}', src)
    readme.write_text(src, encoding='utf-8')


if __name__ == '__main__':
    main()
