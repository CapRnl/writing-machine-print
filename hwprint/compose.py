"""把一份记录（表头 + 正文）排成若干页：首页填表头、正文从首页横线区接着写，写满自动换到下一页。

排版是确定的：同一个种子、同样的内容，排出来一字不差（09-27 用户要求"每次导入排版都一样"）。
表头每一栏、会议议题、正文各用由种子派生的独立随机数：改表头某一栏只动这一栏；
改正文某处，只有改动处以后的内容重排，前面已经写好的页不变。
"""

import hashlib
import math
from dataclasses import dataclass, field

from .layout import Slot, Typesetter
from .normalize import normalize


@dataclass
class Composition:
    form: object
    placed: list                    # 全部已排的字
    pages: int
    missing: set = field(default_factory=set)
    overflow: list = field(default_factory=list)   # 表头按原大写不下的栏目，如"会议名称（多出约 2 个字）"
    complete: bool = True

    def page_size(self, page):
        f = self.form
        if page == 0:
            return f.page_w, f.first_rows * f.first_pitch
        return f.body_w, f.body_h

    def page_items(self, page):
        return [p for p in self.placed if p.page == page]

    def ruled_lines(self, page):
        f = self.form
        if page == 0:
            return [(0, f.page_w, r * f.first_pitch) for r in range(f.first_rows + 1)]
        return [(0, f.body_w, (r + 1) * f.body_pitch) for r in range(f.body_lines)]

    def labels(self, page):
        if page != 0:
            return []
        f = self.form
        from .forms import GAP
        y = lambda r: (r + 1) * f.first_pitch - f.first_pitch * 0.25
        out = [(c.label_x, y(c.row), c.x0 - GAP - c.label_x, c.label) for c in f.cells]
        out += [(x, y(r), x1 - x, t) for r, x, x1, t in f.extra_labels]
        return out


def _body_slots(form, topic):
    if topic and form.topic_row >= 0:
        yield Slot(0, form.topic_x0, form.page_w, (form.topic_row + 1) * form.first_pitch, form.first_pitch)
    for r in range(form.body_start_row, form.first_rows):
        yield Slot(0, 0.0, form.page_w, (r + 1) * form.first_pitch, form.first_pitch)
    page = 1
    while page < 300:
        for r in range(form.body_lines):
            yield Slot(page, 0.0, form.body_w, (r + 1) * form.body_pitch, form.body_pitch)
        page += 1


def sub_seed(seed, *parts):
    """由整份记录的种子派生各部分自己的种子（与 Python 的 hash 随机化无关，每次运行都一样）。"""
    return int(hashlib.md5(('%d|' % seed + '|'.join(parts)).encode('utf-8')).hexdigest()[:12], 16)


def compose(record, form, style, seed=1):
    placed = []
    overflow = []
    missing = set()
    topic = normalize(record.topic or '')
    body = [t for t in (normalize(p) for p in record.body) if t]
    for i, cell in enumerate(form.cells):
        val = normalize((record.fields.get(cell.key) or '').strip())
        if not val:
            continue
        # 每栏的写法只取决于种子和这一栏自己的内容
        ts = Typesetter(style, seed=sub_seed(seed, 'cell', cell.key, val),
                        line_base=10000 * (i + 1), para_base=10000 * (i + 1))
        slot = Slot(0, cell.x0, cell.x1, (cell.row + 1) * form.first_pitch, form.first_pitch)
        name = cell.label.replace(' ', '').strip('：')
        if cell.wrap_rows:
            slots = [slot] + [Slot(0, a, b, (r + 1) * form.first_pitch, form.first_pitch) for r, a, b in cell.wrap_rows]
            items, done, _ = ts.flow([val], slots, indent=False)
            placed += items
            if not done:
                overflow.append('%s（%d 行写不下）' % (name, len(slots)))
        else:
            # 一律按原大写，不缩小字；写不下就记下来提示改短（09-28 用户定）。
            # 手写字库每个字宽窄不一，多出几个字按这一栏自己的平均字宽估
            need = sum(ts.measure(c, slot) for c in val) - ts.gap(slot)
            room = cell.x1 - cell.x0
            if need > room:
                extra = max(1, math.ceil((need - room) / (need / len(val))))
                overflow.append('%s（多出约 %d 个字）' % (name, extra))
                # 真要照写，写不下的字不写：不越过表格右边线，更不会写到纸外、让写字机走到头
                w, keep = -ts.gap(slot), 0
                for c in val:
                    w += ts.measure(c, slot)
                    if w > room:
                        break
                    keep += 1
                val = val[:keep]
            placed += ts.write_line(val, slot)
        missing |= ts.missing
    it = _body_slots(form, topic)
    last_page = 0
    complete = True
    if topic and form.topic_row >= 0:
        ts = Typesetter(style, seed=sub_seed(seed, 'topic'), line_base=500000, para_base=500000)
        items, done, lp = ts.flow([topic], it, indent=False)
        placed += items
        missing |= ts.missing
        last_page = max(last_page, lp)
        complete = complete and done
    if body:
        # 正文的随机数只取决于种子，和表头、议题写了什么无关
        ts = Typesetter(style, seed=sub_seed(seed, 'body'), line_base=1000000, para_base=1000000)
        items, done, lp = ts.flow(body, it, indent=True)
        placed += items
        missing |= ts.missing
        last_page = max(last_page, lp)
        complete = complete and done
    pages = max([p.page for p in placed] + [0]) + 1
    return Composition(form=form, placed=placed, pages=pages, missing=missing,
                       overflow=overflow, complete=complete)
