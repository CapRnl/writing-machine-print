"""把 Word 原稿里的文字整理成手写时的写法。

- 破折号（"——"，或夹在汉字之间的单个"—"）→ 一个长横（LONG_DASH，排版时写成一笔）
- 范围号（"～""~"，或两个数字之间的"—""–"）→ 一个短横"-"
- 全角数字字母 → 半角
- 夹在汉字旁边的半角标点 , ; : ? ! ( ) → 中文标点；直引号 " ' → 成对的中文引号
- 汉字之间的空格、段首空格去掉；"..." → "…"
"""

import re

LONG_DASH = ''          # 内部记号：一笔写成的破折号
DASHES = '—―─━－–‒﹣'          # 各种横线字符
TILDES = '～~〜∼'


def is_cjk(ch):
    if not ch:
        return False
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF or o == 0x2026 or o == 0x2014)


def _fullwidth_alnum(s):
    out = []
    for ch in s:
        o = ord(ch)
        if 0xFF10 <= o <= 0xFF19 or 0xFF21 <= o <= 0xFF3A or 0xFF41 <= o <= 0xFF5A:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    return ''.join(out)


def _dashes(s):
    # 数字之间的横线（不论几个）= 范围号，写成短横："385——400页" "1—34页"
    s = re.sub(r'(?<=\d)[%s]+(?=\d)' % re.escape(DASHES), '-', s)
    # 其余连续两个及以上的横线 = 破折号，写成一笔
    s = re.sub('[%s]{2,}' % re.escape(DASHES), LONG_DASH, s)
    # 单个横线：两边是数字（范围、编号）→ 短横；否则当破折号
    def one(m):
        a = s[m.start() - 1] if m.start() > 0 else ''
        b = s[m.end()] if m.end() < len(s) else ''
        if (a.isdigit() or a.isascii() and a.isalpha()) and (b.isdigit() or b.isascii() and b.isalpha()):
            return '-'
        if a.isdigit() or b.isdigit():
            return '-'
        return LONG_DASH
    s = re.sub('[%s]' % re.escape(DASHES), one, s)
    # 范围号 ～ 一律写成短横
    s = re.sub('[%s]' % re.escape(TILDES), '-', s)
    return s


_HALF2FULL = {',': '，', ';': '；', ':': '：', '?': '？', '!': '！', '(': '（', ')': '）'}


def _punct(s):
    out = list(s)
    n = len(out)
    for i, ch in enumerate(out):
        if ch not in _HALF2FULL:
            continue
        a = out[i - 1] if i > 0 else ''
        b = out[i + 1] if i + 1 < n else ''
        if ch == ':' and a.isdigit() and b.isdigit():
            continue            # 时间 8:30
        if ch == ',' and a.isdigit() and b.isdigit():
            continue            # 1,000
        if is_cjk(a) or is_cjk(b) or (ch in '()' and (is_cjk(a) or is_cjk(b))):
            out[i] = _HALF2FULL[ch]
        elif ch == '(' and i + 1 < n and any(is_cjk(c) for c in out[i + 1:i + 4]):
            out[i] = '（'
        elif ch == ')' and any(is_cjk(c) for c in out[max(0, i - 3):i]):
            out[i] = '）'
    s = ''.join(out)
    # 直引号成对换成中文引号
    res = []
    dq = sq = 0
    for i, ch in enumerate(s):
        if ch == '"':
            res.append('“' if dq % 2 == 0 else '”')
            dq += 1
        elif ch == "'" and (is_cjk(s[i - 1] if i else '') or is_cjk(s[i + 1] if i + 1 < len(s) else '')):
            res.append('‘' if sq % 2 == 0 else '’')
            sq += 1
        else:
            res.append(ch)
    return ''.join(res)


def _spaces(s):
    s = s.replace('\t', ' ').replace(' ', ' ').replace('　', ' ')
    s = s.strip(' ')
    # 汉字（含中文标点）两侧的空格去掉，英文单词之间保留一个
    s = re.sub(r'(?<=[^\x00-\x7f]) +', '', s)
    s = re.sub(r' +(?=[^\x00-\x7f])', '', s)
    s = re.sub(r' {2,}', ' ', s)
    return s


_CONTROL = re.compile('[\x00-\x08\x0a-\x1f\x7f​-‏  ⁠﻿￼]')


def normalize(s):
    if not s:
        return s
    s = _CONTROL.sub('', s)          # 换行、零宽空格等看不见的字符
    s = _fullwidth_alnum(s)
    s = _spaces(s)
    s = s.replace('......', '……').replace('...', '…').replace('。。。', '…')
    s = _dashes(s)
    s = _punct(s)
    return s


# 字库里缺某个符号时的替补（依次尝试）
FALLBACK = {
    '〔': '［(', '〕': '］)', '［': '(', '］': ')', '〈': '<', '〉': '>',
    '【': '［(', '】': '］)', '｛': '{', '｝': '}', '﹝': '(', '﹞': ')',
    '·': '•.', '・': '·.', '‘': '“', '’': '”', '＂': '"', '＇': "'",
}
