"""把一页笔画转成 GRBL 的 G 代码。

坐标约定与奎享雕刻一致（说明书 4.4 节）：原点在左上角，文字在 X 正方向、Y 负方向。
抬落笔按用户机器的设置：步进电机笔控，落笔 Z=zOn，抬笔 Z=zOff。
"""

import math
from dataclasses import dataclass


@dataclass
class Machine:
    feed: float = 4000.0        # 写字速度 毫米/分钟（奎享 settings.json feedRate）
    travel: float = 8000.0      # 抬笔移动速度（jogSpeed）
    pen_type: str = 'Stepper'   # Stepper（Z 轴步进）/ Servo（M3/M5 舵机）
    z_down: float = 6.0         # 落笔 Z（Grbl.json zOn）
    z_up: float = 0.0           # 抬笔 Z（zOff）：抬满，写完一页、暂停、终止时用
    z_hover: float = None       # 写字过程中两笔之间只抬到这里（离纸一点就行，省时间）；None = 抬满
    z_speed: float = 10000.0
    servo_down: int = 1000      # 舵机落笔 M3 S 值
    pen_down_delay: float = 0.0  # 落笔后暂停（秒）
    pen_up_delay: float = 0.0
    near: float = 0.3           # 两笔首尾相距小于此值时不抬笔
    flip_x: bool = False        # 坐标系反转（与奎享"坐标系"设置对应）
    flip_y: bool = False
    swap_xy: bool = False
    offset_x: float = 0.0       # 整体平移（毫米，页面坐标，右/下为正）
    offset_y: float = 0.0

    def to_machine(self, x, y):
        x += self.offset_x
        y += self.offset_y
        mx, my = x, -y                      # 左上角原点：往右 X+，往下 Y-
        if self.flip_x:
            mx = -mx
        if self.flip_y:
            my = -my
        if self.swap_xy:
            mx, my = my, mx
        return mx, my


def pen_up(m, hover=False):
    """hover=True：两笔之间的抬笔，只抬到 z_hover。"""
    z = m.z_hover if (hover and m.z_hover is not None) else m.z_up
    out = ['G1 Z%.2f F%.0f' % (z, m.z_speed)] if m.pen_type == 'Stepper' else ['M5']
    if m.pen_up_delay > 0:
        out.append('G4 P%.2f' % m.pen_up_delay)
    return out


def pen_down(m):
    out = ['G1 Z%.2f F%.0f' % (m.z_down, m.z_speed)] if m.pen_type == 'Stepper' else ['M3 S%d' % m.servo_down]
    if m.pen_down_delay > 0:
        out.append('G4 P%.2f' % m.pen_down_delay)
    return out


def page_strokes(placed, page):
    """按书写顺序取出一页的全部笔画（页面坐标，毫米）。"""
    out = []
    for p in placed:
        if p.page == page:
            out.extend(s for s in p.strokes if s)
    return out


def _n(v):
    """坐标写成最短形式：12.30→12.3，5.00→5，-0.00→0。"""
    s = ('%.2f' % v).rstrip('0').rstrip('.')
    return '0' if s in ('-0', '', '-') else s


def page_gcode(strokes, m, set_origin=True, origin_z=True, compact=False):
    """返回 (G代码行列表, 落笔书写总长毫米, 抬笔移动总长毫米)。
    origin_z：开头连抬笔高度一起重设（奎享"自动设置原点"的做法，要求这时笔是抬着的，导出给奎享用）。
    本程序直接写字时不重设高度：抬笔高度在连接时记下，每页开头先抬笔再走。
    compact：走线指令去掉空格和多余的 0（每行约少 4 字节），同样的接收缓冲能多排几条，程序直接写字时用；
    导出给奎享的文件保持原来的写法。"""
    g = ['G21', 'G90']
    if set_origin:
        # 以当前位置为这一页的左上角
        g.append('G10 L20 P0 X0 Y0 Z0' if origin_z else 'G10 L20 P0 X0 Y0')
    g += pen_up(m)
    cur_f = None
    pos = (0.0, 0.0)
    down = False
    draw_len = travel_len = 0.0

    def mv(cmd, x, y, f):
        nonlocal cur_f
        s = ('%sX%sY%s' % (cmd, _n(x), _n(y))) if compact else ('%s X%.2f Y%.2f' % (cmd, x, y))
        if f != cur_f:
            s += ('F%.0f' if compact else ' F%.0f') % f
            cur_f = f
        g.append(s)

    for st in strokes:
        pts = [m.to_machine(x, y) for x, y in st]
        sx, sy = pts[0]
        d = math.hypot(sx - pos[0], sy - pos[1])
        if down and d > m.near:
            g.extend(pen_up(m, hover=True)); cur_f = None
            down = False
        if not down:
            if d > 0.005:
                mv('G1', sx, sy, m.travel)
                travel_len += d
            g.extend(pen_down(m)); cur_f = None
            down = True
        elif d > 0.005:
            mv('G1', sx, sy, m.feed)
            draw_len += d
        pos = (sx, sy)
        if len(pts) == 1:
            # 单点（句号里的点之类）：原地落一下笔
            continue
        for x, y in pts[1:]:
            dd = math.hypot(x - pos[0], y - pos[1])
            if dd < 0.005:
                continue
            mv('G1', x, y, m.feed)
            draw_len += dd
            pos = (x, y)
    if down:
        g.extend(pen_up(m))
    g.append('G0 X0 Y0')   # 回到原点，方便翻页
    travel_len += math.hypot(pos[0], pos[1])
    return g, draw_len, travel_len


def count_pen_lifts(lines):
    return sum(1 for l in lines if l.startswith('G1 Z') and 'Z%.2f' % 0 in l) or sum(1 for l in lines if l == 'M5')
