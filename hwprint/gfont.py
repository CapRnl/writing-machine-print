"""读取奎享 .gfont 单线字库。

文件结构（大端）：
  int 版本(5) + int n + n 字节元数据 + int k + k 个预览字形 + ZIP 包
ZIP 里每个条目名是十进制码点，内容是一个字形：
  u16 码点 + int 坐标数 + float[坐标数] + int 点数 + byte[点数]（0=起笔，1=连线）
坐标单位为"字库单位"，y 轴向下，y=0 大致是基线，x 以 0 为字框中线。

只读 ZIP 包里的字形，不碰前面的元数据（它是加密的，里面只有字库名、字号基准等，本程序用不上）：
不同字库的大小按常用字的中位高度统一（见 norm），字号基准在换算里正好约掉，所以取名义值 600 即可。
"""

import io
import os
import struct
import zipfile
from dataclasses import dataclass, field


@dataclass
class Glyph:
    strokes: list            # [[(x, y), ...], ...] 每笔一条折线，字库单位
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self):
        return self.x1 - self.x0


# 用来测字库"字在字框里占多大"的常用字
_PROBE = '的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经'
# 统一以后常用字的中位高度占字框的比例（按用户一直在用的陈继世2 实测，约 56%）
TARGET_OCCUPANCY = 0.562


@dataclass
class GFont:
    path: str
    name: str = ''
    size: int = 600          # 名义字号基准：和 norm 相乘时约掉（layout 里都是 / size * norm），不必读真实值
    _zip: zipfile.ZipFile = None
    _names: set = field(default_factory=set)
    _cache: dict = field(default_factory=dict)
    _occ: float = 0.0

    @property
    def occupancy(self):
        """常用字的中位高度 / 字号基准。不同字库差别很大（52%~70%），混用时据此统一大小。"""
        if not self._occ:
            hs = sorted(g.y1 - g.y0 for g in (self.glyph(c) for c in _PROBE) if g)
            self._occ = (hs[len(hs) // 2] / self.size) if hs else TARGET_OCCUPANCY
        return self._occ

    @property
    def norm(self):
        return TARGET_OCCUPANCY / self.occupancy

    @classmethod
    def open(cls, path):
        raw = open(path, 'rb').read()
        n = struct.unpack('>i', raw[4:8])[0]
        font = cls(path=path, name=os.path.splitext(os.path.basename(path))[0])
        try:
            # zip 前面有元数据和预览字形，zipfile 能自己算出偏移
            font._zip = zipfile.ZipFile(io.BytesIO(raw))
        except Exception:
            pk = raw.find(b'PK\x03\x04', 8 + n)
            font._zip = zipfile.ZipFile(io.BytesIO(raw[pk:]))
        font._names = set(font._zip.namelist())
        return font

    def has(self, ch):
        return str(ord(ch)) in self._names

    def glyph(self, ch):
        cp = ord(ch)
        if cp in self._cache:
            return self._cache[cp]
        g = None
        name = str(cp)
        if name in self._names:
            g = _parse_glyph(self._zip.read(name))
        self._cache[cp] = g
        return g


def _parse_glyph(d):
    nf = struct.unpack('>i', d[2:6])[0]
    fl = struct.unpack('>%df' % nf, d[6:6 + 4 * nf])
    o = 6 + 4 * nf
    npt = struct.unpack('>i', d[o:o + 4])[0]
    cmds = d[o + 4:o + 4 + npt]
    strokes = []
    cur = None
    for i in range(nf // 2):
        p = (fl[2 * i], fl[2 * i + 1])
        c = cmds[i] if i < len(cmds) else 1
        if c == 0 or cur is None:
            cur = [p]
            strokes.append(cur)
        else:
            cur.append(p)
    strokes = [s for s in strokes if s]
    if not strokes:
        return None
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    return Glyph(strokes, min(xs), min(ys), max(xs), max(ys))
