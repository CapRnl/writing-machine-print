"""模拟 GRBL 控制板，用于在不接写字机时测试发送、暂停、终止流程。

行为：收到一行回 ok（缓冲超过 128 字节会报错，用来验证流控）；
按行里的坐标和速度模拟运动耗时（加速 speedup 倍）；'?' 回状态；'!' '~' 暂停继续；Ctrl-X 复位。
"""

import math
import re
import threading
import time

_W = {c: re.compile(c + r'(-?[\d.]+)') for c in 'XYZF'}


class MockSerial:
    def __init__(self, speedup=40.0, banner='Grbl 1.1f [\'$\' for help]'):
        self.is_open = True
        self.speedup = speedup
        self.banner = banner
        self._out = bytearray()
        self._rx = bytearray()           # 控制板接收缓冲（未解析）
        self._planner = []               # 待执行的运动（秒）
        self._cv = threading.Condition()
        self.pos = [0.0, 0.0, 0.0]
        self.wco = [0.0, 0.0, 0.0]
        self.feed = 1000.0
        self.hold = False
        self.state = 'Idle'
        self.lines = []                  # 收到的全部指令（测试核对用）
        # 参数照用户真机（09-27 日志）；$120/$121/$11 可被 "$n=v" 改写
        self.settings = {0: 3, 1: 25, 10: 1, 11: 0.01, 100: 80, 101: 80, 102: 80, 110: 12000, 111: 12000,
                         112: 10000, 120: 3000, 121: 3000, 122: 8000}
        self.overflow = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        self._emit(self.banner)

    def _emit(self, s):
        with self._cv:
            self._out += (s + '\r\n').encode()
            self._cv.notify_all()

    def write(self, b):
        with self._cv:
            for ch in bytes(b):
                if ch == ord('?'):
                    x, y, z = (self.pos[i] - self.wco[i] for i in range(3))
                    st = 'Hold:0' if self.hold and self._planner else ('Run' if self._planner else ('Hold:0' if self.hold else 'Idle'))
                    self._out += ('<%s|WPos:%.3f,%.3f,%.3f|FS:0,0>\r\n' % (st, x, y, z)).encode()
                elif ch == ord('!'):
                    self.hold = True
                elif ch == ord('~'):
                    self.hold = False
                elif ch == 0x18:
                    self._rx.clear()
                    self._planner.clear()
                    self.hold = False
                    self._out += ('\r\n' + self.banner + '\r\n').encode()
                else:
                    self._rx.append(ch)
                    if len(self._rx) > 128:
                        self.overflow = True
            self._cv.notify_all()
        return len(b)

    @property
    def in_waiting(self):
        with self._cv:
            return len(self._out)

    def read(self, n=1):
        deadline = time.time() + 0.1
        with self._cv:
            while not self._out and time.time() < deadline:
                self._cv.wait(0.02)
            out = bytes(self._out[:n])
            del self._out[:n]
            return out

    def close(self):
        self.is_open = False

    def _exec_line(self, line):
        self.lines.append(line)
        u = line.upper().replace(' ', '')
        if u.startswith('$$'):
            for k, v in sorted(self.settings.items()):
                self._out += ('$%d=%g\r\n' % (k, v)).encode()
            return 0.0
        m = re.match(r'^\$(\d+)=(-?[\d.]+)$', u)
        if m:
            self.settings[int(m.group(1))] = float(m.group(2))
            return 0.0
        if u.startswith('$I'):
            self._out += b'[VER:1.1f.20170801:]\r\n[OPT:V,15,128]\r\n'
            return 0.0
        rel = False
        if u.startswith('$J='):
            u = u[3:]
            rel = 'G91' in u
        elif u.startswith('$'):
            return 0.0
        if u.startswith('G10L20P0'):
            for i, c in enumerate('XYZ'):          # 只设写出来的轴
                m = _W[c].search(u[8:])
                if m:
                    self.wco[i] = self.pos[i] - float(m.group(1))
            return 0.0
        if 'G91' in u:
            self.relative = True
        if 'G90' in u:
            self.relative = False
        rel = rel or getattr(self, 'relative', False)
        m = _W['F'].search(u)
        if m:
            self.feed = float(m.group(1))
        tgt = list(self.pos)
        moved = False
        for i, c in enumerate('XYZ'):
            m = _W[c].search(u)
            if m:
                tgt[i] = (self.pos[i] + float(m.group(1))) if rel else (float(m.group(1)) + self.wco[i])
                moved = True
        if not moved:
            return 0.0
        dist = math.dist(tgt, self.pos)
        feed = 8000.0 if u.startswith('G0') else self.feed
        self.pos = tgt
        return dist / max(feed, 1.0) * 60.0

    def _run(self):
        remaining = None                 # 当前这段运动还剩多少秒（已按加速比折算）
        while self.is_open:
            with self._cv:
                # 边执行边解析：接收缓冲里的完整行放进规划缓冲（最多 15 条）并回 ok
                while b'\n' in self._rx and len(self._planner) < 15:
                    i = self._rx.index(b'\n')
                    line = self._rx[:i].decode().strip()
                    del self._rx[:i + 1]
                    if line:
                        self._planner.append(self._exec_line(line))
                    self._out += b'ok\r\n'
                    self._cv.notify_all()
                if not self._planner:
                    remaining = None
                elif remaining is None:
                    remaining = self._planner[0] / self.speedup
                running = remaining is not None and not self.hold
            if running:
                step = min(remaining, 0.01)
                time.sleep(step)
                remaining -= step
                if remaining <= 1e-9:
                    with self._cv:
                        if self._planner:
                            self._planner.pop(0)
                    remaining = None
            else:
                time.sleep(0.005)
