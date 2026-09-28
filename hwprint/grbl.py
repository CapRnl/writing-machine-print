"""GRBL 写字机串口控制。

发送采用 GRBL 官方推荐的"字符计数"流控：保证控制板接收缓冲里未确认的字节不超过
预算（连接时用 $I 问出缓冲大小，问不到按 128 字节算），缓冲里始终有后续指令，短笔画连写不断供。
确认信息必须一到就处理：写字机的规划缓冲一旦吃空、停够 $1 毫秒（用户的机器是 25），
电机就断电，抬落笔机构被弹簧顶回原位，之后落笔高度全乱——09-27 真机试写成段缺字就是这个原因。
暂停 = 写完当前这一笔、发出抬笔那一行后不再往下发，机器走完缓冲自然停住；继续 = 先重发抬笔再接着发；
终止 = 先 '!' 停稳，再软复位（Ctrl-X，停稳后复位不丢坐标），然后抬笔、回到这一页的左上角。
"""

import re
import threading
import time

import serial
import serial.tools.list_ports

BUDGET = 120          # 问不到缓冲大小时按 GRBL 默认的 128 字节，留一点余量
_STATUS = re.compile(r'<([A-Za-z]+)(?::\d+)?\|(?:MPos|WPos):([-\d.]+),([-\d.]+),([-\d.]+)')


class GrblError(Exception):
    pass


def list_ports():
    out = []
    for p in serial.tools.list_ports.comports():
        out.append({'port': p.device, 'desc': p.description or '',
                    'ch340': 'CH340' in (p.description or '').upper() or (p.vid == 0x1A86)})
    return out


def find_machine_port():
    ports = list_ports()
    for p in ports:
        if p['ch340']:
            return p['port']
    return ports[0]['port'] if ports else None


class Grbl:
    def __init__(self, log=None):
        self.ser = None
        self.log = log or (lambda s: None)
        self.version = ''
        self.settings = {}
        self.state = 'Unknown'
        self.wpos = (0.0, 0.0, 0.0)
        self.wco = None
        self.alarm = None
        self._lock = threading.Lock()          # 写串口
        self._acks = []                        # 已收到的 ok/error，按顺序
        self._ack_cv = threading.Condition()
        self._reader = None
        self._poller = None
        self._stop = threading.Event()
        self._banner = threading.Event()
        self._last_status = 0.0
        self._connect_trace = None
        self.streaming = False
        self._ready = False                    # 连接全部做完才为真，见 connected
        self.budget = BUDGET
        self.build = ''                        # $I 报出的版本和缓冲信息
        self._ver = self._opt = ''
        self.idle_in_stream = 0                # 写一页的中途机器停下（断供）的次数，只作记录
        self._stream_t0 = 0.0

    # ---------------- 连接 ----------------
    @property
    def connected(self):
        """连接全部做完（问过参数、设好起点）才算连上。串口一打开就算的话，连接还在等回话的那几秒里
        界面就能点"开始打印"，两件事撞在一起（09-27 22:38 真机上发生过：确认信号被连接过程清掉、参数没读到）。"""
        return self._ready and self._port_open()

    def _port_open(self):
        return self.ser is not None and self.ser.is_open

    def connect(self, port, baud=115200, timeout=6.0):
        self._ready = False
        if self._port_open():
            self.close()
        try:
            if port.lower().startswith('mock'):
                from .mockgrbl import MockSerial
                self.ser = MockSerial(speedup=float(port.split(':')[1]) if ':' in port else 40.0)
            else:
                self.ser = serial.Serial(port, baud, timeout=0.1, write_timeout=2)
        except serial.SerialException as e:
            msg = str(e)
            if 'PermissionError' in msg or 'Access is denied' in msg or '拒绝访问' in msg:
                raise GrblError('%s 正被别的程序占用（多半是奎享雕刻）。请在奎享雕刻右边点"断开"或把它关掉，再连接。' % port)
            raise GrblError('打不开 %s：%s' % (port, msg))
        self._stop.clear()
        self._banner.clear()
        self._connect_trace = []
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        # 打开串口一般会让控制板复位，等它报出启动信息（"Grbl x.x ..."）。
        # 用户的 ESP32 板打开串口不复位、不报启动信息：0.5 秒没动静就问一下状态，有 <...> 回报也算连上
        # （原来干等 6 秒才问，连接要 6 秒多）
        self.ser.write(b'\r\n\r\n')
        if not self._banner.wait(0.5):
            self._write_raw(b'?')
            if not self._banner.wait(timeout):
                self._write_raw(b'\x18')
                if not self._banner.wait(3.0):
                    trace = ' | '.join(self._connect_trace[-8:]) or '（什么也没收到）'
                    self.close()
                    raise GrblError('%s 没有回应写字机的启动信息，可能写字机没通电或波特率不对。收到的内容：%s' % (port, trace))
        time.sleep(0.3)
        self.log('连接时收到：' + (' | '.join(self._connect_trace[-10:]) or '无'))
        self._connect_trace = None
        with self._ack_cv:
            self._acks.clear()
        self.settings = {}
        try:
            self.command('$$', timeout=3)
        except GrblError:
            pass
        self._ask_build()
        self._poller = threading.Thread(target=self._poll_loop, daemon=True)
        self._poller.start()
        self._ready = True
        return self.version

    def _ask_build(self):
        """$I：固件版本和 [OPT:选项,规划块数,接收缓冲字节]。按接收缓冲定发送预算。"""
        self._ver = self._opt = ''
        self.budget = BUDGET
        try:
            self.command('$I', timeout=3)
        except GrblError:
            pass
        m = re.match(r'\[OPT:[^,\]]*,(\d+),(\d+)', self._opt or '')
        if m and int(m.group(2)) >= 64:
            self.budget = int(m.group(2)) - 8
        if not self.version and self._ver:
            self.version = 'Grbl ' + self._ver.split(':')[1] if ':' in self._ver else self._ver
        self.build = ' '.join(x for x in (self._ver, self._opt) if x) or '（未报）'

    def close(self):
        self._ready = False
        self._stop.set()
        s, self.ser = self.ser, None
        if s:
            try:
                s.close()
            except Exception:
                pass

    # ---------------- 读写 ----------------
    def _write_raw(self, b):
        with self._lock:
            if not self._port_open():             # 连接过程中也要能发，所以只看串口开没开
                raise GrblError('写字机没有连接')
            self.ser.write(b)

    def _read_loop(self):
        buf = b''
        while not self._stop.is_set():
            try:
                # 有多少读多少，一个字节都没有才最多等 0.1 秒。原来固定 read(256)：Windows 下要凑满
                # 256 字节或等满 0.1 秒才返回，"ok"被攒成一批，写到笔画密的地方写字机就断供
                ser = self.ser
                chunk = ser.read(ser.in_waiting or 1) if ser else b''
            except Exception as e:
                self.log('串口读取出错：%s' % e)
                self.state = 'Disconnected'
                with self._ack_cv:
                    self._acks.append('error:disconnected')
                    self._ack_cv.notify_all()
                break
            if not chunk:
                continue
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                self._on_line(line.decode('ascii', 'replace').strip())

    def _on_line(self, line):
        if not line:
            return
        if self._connect_trace is not None:
            self._connect_trace.append(line)
        if line.startswith('<'):
            self._parse_status(line)
            if not self._banner.is_set():
                self._banner.set()       # 有状态回报，说明是 GRBL 类控制板
            return
        low = line.lower()
        if low == 'ok' or low.startswith('error'):
            with self._ack_cv:
                self._acks.append(line)
                self._ack_cv.notify_all()
            if low.startswith('error'):
                self.log('写字机报错：%s' % line)
            return
        if line.startswith('[VER:'):
            self._ver = line.strip('[]')
            return
        if line.startswith('[OPT:'):
            self._opt = line
            return
        if 'grbl' in line.lower() and not line.startswith('['):
            self.version = line
            self.alarm = None
            self._banner.set()
            self.log('写字机：%s' % line)
            return
        if line.upper().startswith('ALARM'):
            self.alarm = line
            self.state = 'Alarm'
            self.log('写字机报警：%s' % line)
            with self._ack_cv:
                self._acks.append('error:' + line)
                self._ack_cv.notify_all()
            return
        m = re.match(r'^\$(\d+)=([-\d.]+)', line)
        if m:
            self.settings[int(m.group(1))] = float(m.group(2))
            return
        self.log('写字机：%s' % line)

    def _parse_status(self, line):
        self._last_status = time.time()
        body = line.strip('<>')
        parts = body.split('|') if '|' in body else body.split(',')
        prev, self.state = self.state, parts[0].split(':')[0]
        if (self.streaming and self.state == 'Idle' and prev != 'Idle'
                and time.time() - self._stream_t0 > 1.0):
            self.idle_in_stream += 1          # 还有指令没发完机器却停了：断供
        wpos = mpos = None
        for p in parts[1:]:
            if p.startswith('WPos:'):
                wpos = [float(v) for v in p[5:].split(',')[:3]]
            elif p.startswith('MPos:'):
                mpos = [float(v) for v in p[5:].split(',')[:3]]
            elif p.startswith('WCO:'):
                self.wco = [float(v) for v in p[4:].split(',')[:3]]
        if wpos is None and mpos is not None:
            wco = self.wco or [0, 0, 0]
            wpos = [mpos[i] - wco[i] for i in range(3)]
        if wpos:
            self.wpos = tuple(wpos)
        if self.state != 'Alarm':
            self.alarm = None

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                self._write_raw(b'?')
            except Exception:
                break
            time.sleep(0.25)

    # ---------------- 单条指令 ----------------
    def command(self, line, timeout=10.0):
        """发一条指令并等它的 ok；出错抛 GrblError。"""
        if self.streaming:
            raise GrblError('正在写字，稍后再操作')
        with self._ack_cv:
            start = len(self._acks)
        self._write_raw((line.strip() + '\n').encode('ascii'))
        deadline = time.time() + timeout
        with self._ack_cv:
            while len(self._acks) <= start:
                left = deadline - time.time()
                if left <= 0:
                    raise GrblError('写字机没有回应：%s' % line)
                self._ack_cv.wait(left)
            r = self._acks[start]
            del self._acks[:start + 1]
        if r.lower() != 'ok':
            raise GrblError('%s → %s' % (line, r))
        return r

    def realtime(self, ch):
        self._write_raw(ch if isinstance(ch, bytes) else ch.encode())

    def wait_idle(self, timeout=600.0, cancel=None):
        t0 = time.time()
        time.sleep(0.3)
        while time.time() - t0 < timeout:
            if cancel is not None and cancel.is_set():
                return False
            if self.state in ('Idle',) and time.time() - self._last_status < 1.0:
                return True
            if self.state == 'Alarm':
                raise GrblError(self.alarm or '写字机报警')
            time.sleep(0.1)
        raise GrblError('等写字机停下超时')

    # ---------------- 常用动作 ----------------
    def unlock(self):
        return self.command('$X')

    def set_origin(self):
        """当前位置作起点、当前高度作抬笔高度（笔要抬着）。"""
        return self.command('G10 L20 P0 X0 Y0 Z0')

    def pen(self, z, z_speed=10000):
        self.command('G90 G1 Z%.2f F%.0f' % (z, z_speed))

    # ---------------- 连续发送 ----------------
    def stream(self, lines, progress=None, cancel=None, pause=None, is_pen_up=None, on_paused=None):
        """按字符计数流控发送一整页。
        progress(已确认行数, 总行数)；cancel 置位后尽快返回 False（调用方负责停机）；
        pause 置位后：写完当前一笔、发出抬笔那一行就不再往下发，等机器停稳后调用
        on_paused()，直到 pause 清除再接着发。is_pen_up(行) 判断某行是不是抬笔。"""
        lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith(';')]
        total = len(lines)
        with self._ack_cv:
            self._acks.clear()
        inflight = []           # 已发未确认的每行字节数
        sent = acked = 0
        errors = []
        hold_after = False      # 暂停请求已生效：抬笔行发出后停止送行
        budget = self.budget
        self.idle_in_stream = 0
        self._stream_t0 = time.time()
        self.streaming = True
        try:
            while acked < total:
                if cancel is not None and cancel.is_set():
                    return False
                paused_now = pause is not None and pause.is_set()
                # 缓冲有空就继续塞
                while sent < total and not hold_after:
                    n = len(lines[sent]) + 1
                    if inflight and sum(inflight) + n > budget:
                        break
                    self._write_raw((lines[sent] + '\n').encode('ascii'))
                    inflight.append(n)
                    sent += 1
                    if paused_now and is_pen_up and is_pen_up(lines[sent - 1]):
                        hold_after = True
                if hold_after and not inflight:
                    # 已抬笔、缓冲清空：等机器停稳后原地等候
                    self.streaming = False
                    try:
                        self.wait_idle(timeout=120, cancel=cancel)
                        if on_paused:
                            on_paused()
                        while pause.is_set():
                            if cancel is not None and cancel.is_set():
                                return False
                            time.sleep(0.1)
                        # 暂停时可能点过"落笔"：把停下时那条抬笔再发一遍，确保抬着笔去下一笔
                        self.command(lines[sent - 1])
                    finally:
                        self.streaming = True
                    with self._ack_cv:
                        self._acks.clear()
                    hold_after = False
                    continue
                with self._ack_cv:
                    if not self._acks:
                        self._ack_cv.wait(0.5)
                    got = self._acks[:]
                    self._acks.clear()
                for r in got:
                    if not inflight:
                        continue
                    inflight.pop(0)
                    acked += 1
                    if r.lower() != 'ok':
                        errors.append((acked, lines[acked - 1], r))
                        self.log('第 %d 行 %s 被拒：%s' % (acked, lines[acked - 1], r))
                        if 'ALARM' in r.upper() or 'disconnected' in r or len(errors) >= 5:
                            raise GrblError('写字机停止执行：%s' % r)
                if progress and got:
                    progress(acked, total)
            return True
        finally:
            self.streaming = False

    def abort(self, z_up=0.0, z_speed=10000, home=True):
        """急停：先减速停稳（'!'），停稳后复位清空缓冲（停稳后复位不丢坐标），复位完成立刻抬笔。
        全程约 0.3~1 秒；这期间笔可能还压在纸上，所以每一步都只等到条件满足为止。
        home：抬笔后回到这一页的左上角（和奎享"停止"一样），接着从这一页重写时不用再对笔。"""
        try:
            self.realtime(b'!')
            t0 = time.time()
            self._last_status = 0
            while time.time() - t0 < 1.0:
                time.sleep(0.03)
                if self._last_status and self.state in ('Hold', 'Idle', 'Alarm'):
                    if self.state != 'Hold' or time.time() - t0 > 0.12:
                        break
            self._banner.clear()
            self.realtime(b'\x18')
            self._banner.wait(2.5)
            time.sleep(0.05)
            deadline = time.time() + 3
            while self.streaming and time.time() < deadline:
                time.sleep(0.02)           # 等发送线程退出
            with self._ack_cv:
                self._acks.clear()
            if self.state == 'Alarm' or self.alarm:
                try:
                    self.unlock()
                except GrblError:
                    pass
            self.pen(z_up, z_speed)
            if home:
                self.command('G90 G0 X0 Y0')
        except GrblError as e:
            self.log('停机时出错：%s' % e)
