"""版式自检：逐行检查避头尾、数字拆行、破折号、出界、缺字。返回问题清单（空 = 通过）。"""

from collections import defaultdict

from .layout import NO_LINE_START, NO_LINE_END, LONG_DASH
from .normalize import DASHES, TILDES


def check(comp, hang_mm=6.5):
    problems = []
    lines = defaultdict(list)
    for p in comp.placed:
        if p.para >= 0:
            lines[p.line].append(p)
    order = sorted(lines)
    for idx, ln in enumerate(order):
        items = sorted(lines[ln], key=lambda p: p.x0)
        first, last = items[0], items[-1]
        text = ''.join(p.ch for p in items).replace(LONG_DASH, '⸺')
        where = '第%d页 “%s”' % (first.page + 1, text[:14])
        # 行首标点：只查同一段里换行后的行首（段落本身以破折号、引号开头是原文如此）
        prev_same_para = idx > 0 and any(p.para == first.para for p in lines[order[idx - 1]])
        if first.ch in NO_LINE_START and prev_same_para:
            problems.append('%s：行首是标点"%s"' % (where, first.ch))
        if last.ch in NO_LINE_END:
            problems.append('%s：行尾是开括号/开引号"%s"' % (where, last.ch))
        # 数字字母被拆到两行（同一段内）
        if idx + 1 < len(order):
            nxt = sorted(lines[order[idx + 1]], key=lambda p: p.x0)
            if nxt and nxt[0].para == last.para and last.ch.isascii() and last.ch.isalnum() \
                    and nxt[0].ch.isascii() and nxt[0].ch.isalnum():
                problems.append('%s：数字/字母"%s|%s"被拆到两行' % (where, last.ch, nxt[0].ch))
        # 出界：超出本页宽度太多
        w = comp.page_size(first.page)[0]
        over = max(p.x1 for p in items) - w
        if over > hang_mm:
            problems.append('%s：超出右边 %.1f 毫米' % (where, over))
        for p in items:
            if p.ch in DASHES or p.ch in TILDES:
                problems.append('%s：还有未转换的横线/波浪号"%s"' % (where, p.ch))
                break
    # 墨迹出界（整页）
    for p in comp.placed:
        w, h = comp.page_size(p.page)
        for s in p.strokes:
            for x, y in s:
                if x < -1.5 or y < -1.5 or y > h + 4:
                    problems.append('第%d页 "%s" 的笔画出了页面（x=%.1f, y=%.1f）' % (p.page + 1, p.ch, x, y))
                    break
            else:
                continue
            break
    if comp.missing:
        problems.append('字库里没有：%s' % ''.join(sorted(comp.missing)))
    return problems
