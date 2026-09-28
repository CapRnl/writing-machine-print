"""排版：把文字排成每页的笔画（单位毫米，原点在页面左上角，y 向下）。

字宽规则按奎享雕刻"笔记"功能实测还原：字高 = 行高 × 字号%；每字占宽 = 字形实际宽度 × 缩放 + 字间隔；
字形底边（字库 y=0）落在本行底线上；句号至少占半个字宽；段首缩进 2 个半角空格宽。

断行（避头尾，同 Word 中文版式）：
  每个字和紧跟其后的"不能放行首"的标点绑成一组，开括号/开引号和它后面的字绑成一组，
  连写的数字字母、破折号、省略号不拆开；一组放不下就整组换行。
  组尾的小标点放不下时允许略超出行尾（悬挂），超出太多则连同前一个字一起换行。

手写感：
  同一个字在同一人的多个版本字库之间轮换，连续两次不用同一版；
  上下位置、大小、倾斜、字距都是"相邻几个字一起缓慢起伏"（随机游走）+ 少量独立抖动，
  每行起笔位置略有参差。（奎享原来的"每字独立随机抖动"效果差，09-27 按用户要求删除，不再保留。）
"""

import math
import random
from dataclasses import dataclass, field

from .normalize import LONG_DASH, FALLBACK

# 不能放在行首的标点（Word 中文"后置标点"）
NO_LINE_START = set('，。、；：？！）》」』】〕〉〗…—”’%％·,.;:?!)]}' + LONG_DASH)
# 不能放在行尾的标点（Word 中文"前置标点"）
NO_LINE_END = set('（《「『【〔〈〖“‘([{')
# 行尾允许悬挂的小标点
HANGABLE = set('，。、；：？！）》」』】〕〉”’,.;:?!)')
# 个别标点至少占多宽（字高的倍数）。截图实测原软件只对句号多留空
MIN_ADVANCE = {'。': 0.5}
# 这些符号在有的版本里写得不规范（如句号写成实心点），可按笔迹限定只从部分版本里取
PUNCT = set('，。、；：？！“”‘’（）《》〈〉【】〔〕…—·')
LONG_DASH_LEN = 1.7      # 破折号写成一笔，长度约为字高的倍数


@dataclass
class Style:
    fonts: list                      # [(GFont, 权重), ...]；同一人的多个版本放在一起轮换
    font_pct: float = 95.0           # 字高占行高的百分比
    gap: float = 1.0                 # 字间隔（毫米）
    indent_half_spaces: int = 2      # 段首缩进：几个半角空格宽
    amount: float = 1.0              # 手写起伏的幅度倍数
    base_line: float = 0.79          # 字形基线在行高的这个比例处（1=压在底线上；0.79 与奎享一致，见 config.BASE_LINE）
    punct_add: dict = field(default_factory=dict)  # 个别标点两侧加宽（毫米）
    space_add: float = 0.0           # 空格增宽（毫米）
    hang: float = 0.6                # 行尾标点最多超出多少（字高的倍数）
    punct_ok: tuple = None           # 标点只从这些版本（fonts 下标）里取；None = 都可以


@dataclass
class Slot:
    """一段可写字的横线：第 page 页，从 x0 写到 x1，底线在 y（毫米）。"""
    page: int
    x0: float
    x1: float
    y: float
    line_h: float


@dataclass
class Placed:
    page: int
    ch: str
    strokes: list                    # [[(x, y), ...], ...] 毫米
    line: int = 0                    # 第几条横线（全文连续编号，校验用）
    x0: float = 0.0                  # 本字占位的左右边界（校验用）
    x1: float = 0.0
    para: int = -1                   # 第几段（表头为 -1，校验用）


class _Walk:
    """相邻字之间缓慢变化的随机量（离散 OU 过程），平稳时标准差为 sigma。"""
    def __init__(self, rng, sigma, rho):
        self.rng, self.sigma, self.rho = rng, sigma, rho
        self.v = rng.gauss(0, sigma) if sigma > 0 else 0.0

    def step(self):
        if self.sigma <= 0:
            return 0.0
        self.v = self.rho * self.v + math.sqrt(1 - self.rho ** 2) * self.rng.gauss(0, self.sigma)
        return self.v


class Typesetter:
    def __init__(self, style, seed=None, line_base=0, para_base=0):
        """line_base/para_base：行号、段号从几开始。一份记录的表头各栏、议题、正文各用一个排版器（各自的随机数），
        行号段错开，自检时不会把不同部分的行混在一起。"""
        self.st = style
        self.rng = random.Random(seed)
        self.missing = set()
        self._last_variant = {}
        self._slot = None
        self._line_no = line_base - 1
        self._walk = None
        self._font_cache = {}
        self._para_base = para_base
        self._para = para_base - 1

    # ---------------- 字体选择 ----------------
    def _cands(self, ch):
        if ch not in self._font_cache:
            c = [i for i, (f, w) in enumerate(self.st.fonts) if f.has(ch)]
            if ch in PUNCT and self.st.punct_ok:
                c = [i for i in c if i in self.st.punct_ok] or c
            self._font_cache[ch] = c
        return self._font_cache[ch]

    def _resolve(self, ch):
        """返回实际要写的字（缺字时换成替补符号）和候选字库下标。"""
        c = self._cands(ch)
        if c:
            return ch, c
        for alt in FALLBACK.get(ch, ''):
            c = self._cands(alt)
            if c:
                return alt, c
        return ch, []

    def _pick(self, ch, cands):
        """在有这个字的版本里随机取一个，和这个字上一次用的版本不同。"""
        if len(cands) == 1:
            return cands[0]
        last = self._last_variant.get(ch)
        pool = [i for i in cands if i != last] or cands
        i = self.rng.choice(pool)
        self._last_variant[ch] = i
        return i

    # ---------------- 尺寸 ----------------
    def char_h(self, slot):
        return slot.line_h * self.st.font_pct / 100.0

    def gap(self, slot):
        return self.st.gap

    def measure(self, ch, slot):
        """不带随机的字宽（毫米），用于断行预估。"""
        h = self.char_h(slot)
        gap = self.gap(slot)
        if ch == ' ':
            return 0.5 * h + self.st.space_add + gap
        if ch == '　':
            return h + self.st.space_add + gap
        if ch == '\t':
            return 2 * h + gap
        if ch == LONG_DASH:
            return LONG_DASH_LEN * h + gap
        real, cands = self._resolve(ch)
        if not cands:
            return 0.5 * h + gap
        f = self.st.fonts[cands[0]][0]
        g = f.glyph(real)
        if g is None:
            return 0.5 * h + gap
        w = max(g.width * h / f.size * f.norm, MIN_ADVANCE.get(ch, 0.0) * h)
        return w + gap + 2 * self.st.punct_add.get(ch, 0.0)

    # ---------------- 手写起伏 ----------------
    def _line_state(self, slot):
        if slot is not self._slot:
            self._slot = slot
            self._line_no += 1
            a = self.st.amount
            rng = self.rng
            self._walk = {
                'y': _Walk(rng, 0.0175 * a, 0.75),       # 上下起伏（占字高）。09-28 减半，见 _jitter
                'size': _Walk(rng, 0.035 * a, 0.8),      # 大小起伏
                'rot': _Walk(rng, 1.4 * a, 0.6),         # 倾斜起伏（度）
                'gap': _Walk(rng, 0.15 * a, 0.5),        # 字距起伏（占字距比例）
                'slant': rng.gauss(0, 0.8 * a),          # 这一行整体的倾斜
                'start': max(-0.9, min(0.9, rng.gauss(0, 0.35 * a))),   # 起笔位置参差（毫米）
            }
        return self._walk

    def _jitter(self, slot):
        """本字的 (大小倍数, 旋转弧度, 上下偏移占字高, 字距倍数)。"""
        w = self._line_state(slot)
        rng = self.rng
        a = self.st.amount
        k = 1.0 + w['size'].step() + rng.gauss(0, 0.012 * a)
        k = max(0.88, min(1.12, k))
        deg = w['slant'] + w['rot'].step() + rng.gauss(0, 0.9 * a)
        deg = max(-6.0, min(6.0, deg))
        # 上下起伏 09-28 减半（上限 ±9%→±4.5% 字高）：用户看到"半行明显下移，像抖动过大"，按手抄队列 116 页统计，
        # 原来约四分之一的页有连着 8 个字一起偏下 0.7 毫米以上（最多约 1 毫米）；减半并改成字变大时以字中心为准后，最多约 0.44 毫米。
        # 只改幅度、不改取随机数的次数和顺序，换行和每页的字都不变。
        dy = w['y'].step() + rng.gauss(0, 0.005 * a)
        dy = max(-0.045, min(0.045, dy))
        gk = max(0.5, min(1.6, 1.0 + w['gap'].step()))
        return k, math.radians(deg), dy, gk

    # ---------------- 写一个字 ----------------
    def _render_char(self, ch, x, slot):
        """在 (x, 底线) 处写一个字，返回 (Placed 或 None, 占宽)。"""
        st = self.st
        h = self.char_h(slot)
        k, ang, dy, gk = self._jitter(slot)
        gap = self.gap(slot) * gk
        if ch in (' ', '　', '\t'):
            return None, self.measure(ch, slot) - self.gap(slot) + gap
        base = slot.y - slot.line_h * (1.0 - st.base_line) + dy * h
        if ch == LONG_DASH:
            return self._long_dash(x, slot, h, k, ang, base, gap)
        real, cands = self._resolve(ch)
        if not cands:
            self.missing.add(ch)
            return None, 0.5 * h + gap
        f = st.fonts[self._pick(real, cands)][0]
        g = f.glyph(real)
        if g is None:
            self.missing.add(ch)
            return None, 0.5 * h + gap
        s = h / f.size * f.norm * k
        add = st.punct_add.get(ch, 0.0)
        gw = g.width * s
        box = max(gw, MIN_ADVANCE.get(ch, 0.0) * h * k)
        ox = x + add + (box - gw) / 4.0 - g.x0 * s   # 字形左边对齐到 x（加宽的标点略往右放）
        cx, cy = (g.x0 + g.x1) / 2.0, (g.y0 + g.y1) / 2.0
        # 字变大变小以字的中心为准（原来以字顶为准，字一大就整体往下沉，和上下起伏叠在一起显得"半行下移"，09-28 改）
        yc = base + cy * s / k
        ca, sa = math.cos(ang), math.sin(ang)
        strokes = []
        for stroke in g.strokes:
            pts = []
            for gx, gy in stroke:
                if ang:
                    rx, ry = gx - cx, gy - cy
                    gx, gy = cx + rx * ca - ry * sa, cy + rx * sa + ry * ca
                pts.append((ox + gx * s, yc + (gy - cy) * s))
            strokes.append(pts)
        adv = box + gap + 2 * add
        return Placed(slot.page, ch, strokes, self._line_no, x, x + box + 2 * add, self._para), adv

    def _long_dash(self, x, slot, h, k, ang, base, gap):
        """破折号：取字库里的"—"拉长成一笔（保留手写的轻微弯曲），没有就画一条略带起伏的线。"""
        L = LONG_DASH_LEN * h * k
        mid = base - 0.42 * h
        real, cands = self._resolve('—')
        pts = None
        if cands:
            f = self.st.fonts[self._pick('—', cands)][0]
            g = f.glyph('—')
            if g and g.strokes and g.width > 0:
                longest = max(g.strokes, key=len)
                xs = [p[0] for p in longest]
                x0g, x1g = min(xs), max(xs)
                sy = h / f.size * f.norm * k
                cy = sum(p[1] for p in longest) / len(longest)
                pts = [(x + (gx - x0g) / max(x1g - x0g, 1e-6) * L, mid + (gy - cy) * sy) for gx, gy in longest]
        if not pts:
            pts = [(x + t / 8 * L, mid + math.sin(t * 0.9 + self.rng.random()) * 0.012 * h) for t in range(9)]
        if ang:
            ca, sa = math.cos(ang * 0.3), math.sin(ang * 0.3)
            cx = x + L / 2
            pts = [(cx + (px - cx) * ca - (py - mid) * sa, mid + (px - cx) * sa + (py - mid) * ca) for px, py in pts]
        return Placed(slot.page, LONG_DASH, [pts], self._line_no, x, x + L, self._para), L + gap

    # ---------------- 单行（表头格子用）----------------
    def write_line(self, text, slot):
        """在一个槽里按原大写一行字。放不下也不缩小（09-28 用户定："超过之后就提示，不要自动缩小"），
        由 compose 记下来提示改短。"""
        out = []
        x = slot.x0
        self._para = -1
        for ch in text:
            p, adv = self._render_char(ch, x, slot)
            if p:
                out.append(p)
            x += adv
        return out

    # ---------------- 正文流 ----------------
    @staticmethod
    def atoms(text):
        """按避头尾规则切成不可拆分的组。"""
        out = []
        i, n = 0, len(text)
        while i < n:
            start = i
            while i < n and text[i] in NO_LINE_END:
                i += 1
            if i < n:
                ch = text[i]
                if ch.isascii() and (ch.isalnum()):
                    j = i + 1
                    while j < n and j - i < 14 and text[j].isascii() and (text[j].isalnum() or text[j] in '.-:/%'):
                        j += 1
                    # 组尾不能是连接符（"1-"后面换行不好）
                    while j - 1 > i and text[j - 1] in '.-:/':
                        j -= 1
                    i = j
                else:
                    i += 1
            while i < n and text[i] in NO_LINE_START:
                i += 1
            if i == start:          # 保险：避免死循环
                i += 1
            out.append(text[start:i])
        return out

    def flow(self, paragraphs, slots, indent=True):
        """把段落依次排进 slots（可迭代）。返回 (已排字列表, 是否全部排完, 用到的最后一页)。"""
        st = self.st
        out = []
        it = iter(slots)
        last_page = 0
        for para in paragraphs:
            text = para.strip()
            self._para += 1
            slot = next(it, None)
            if slot is None:
                return out, False, last_page
            last_page = slot.page
            w = self._line_state(slot)
            x = slot.x0 + (w['start'] if w else 0.0)
            if indent and text:
                x = slot.x0 + st.indent_half_spaces * self.measure(' ', slot) + (w['start'] if w else 0.0)
            for atom in self.atoms(text):
                core = atom.rstrip(''.join(NO_LINE_START)) if atom[-1] in NO_LINE_START else atom
                tail = atom[len(core):]
                # 实际写出的字有随机大小，预估时留 3% 余量
                wc = sum(self.measure(c, slot) for c in core) * 1.03
                wt = sum(self.measure(c, slot) for c in tail) * 1.03
                g = self.gap(slot)
                full_end = x + wc + wt - g
                fits = full_end <= slot.x1 + 0.01
                if not fits and x > slot.x0 + 1.0:
                    hang_ok = (tail and all(c in HANGABLE for c in tail)
                               and x + wc - g <= slot.x1 + 0.01
                               and full_end <= slot.x1 + st.hang * self.char_h(slot))
                    if not hang_ok:
                        slot = next(it, None)
                        if slot is None:
                            return out, False, last_page
                        last_page = slot.page
                        w = self._line_state(slot)
                        x = slot.x0 + (w['start'] if w else 0.0)
                for c in atom:
                    p, adv = self._render_char(c, x, slot)
                    if p:
                        out.append(p)
                    x += adv
        return out, True, last_page


def page_slots(first_page, page_w, lines, line_h, top=0.0, left=0.0, right=0.0,
               start_row=0, max_pages=200):
    """整页横线槽：从 first_page 页的第 start_row 行（0 起）开始，一直往后翻页。"""
    p = first_page
    r = start_row
    while p < first_page + max_pages:
        while r < lines:
            yield Slot(p, left, page_w - right, top + (r + 1) * line_h, line_h)
            r += 1
        p += 1
        r = 0
