"""写字机打印：界面后台（pywebview 调用）。

注意：pywebview 会把本类所有"公开属性"递归暴露给页面，内部状态一律用下划线开头。
"""

import datetime
import glob
import hashlib
import os
import random
import re
import threading
import time
import traceback

from . import config, notify
from .compose import compose
from .docparse import Record, parse
from .forms import FORMS
from .gcode import Machine, page_gcode, page_strokes, pen_up
from .gfont import GFont
from .grbl import Grbl, GrblError, find_machine_port, list_ports
from .layout import Style, Typesetter, Slot
from .normalize import normalize
from .verify import check

LOG_DIR = os.path.join(config.APP_DIR, '日志')
EXPORT_DIR = os.path.join(config.APP_DIR, '导出G代码')
TITLE = '写字机打印'          # 窗口标题（任务栏闪烁时按它找窗口）
REMIND_EVERY = 180            # 等换纸时没人理，隔几秒再提醒一次
REMIND_TIMES = 3              # 最多再提醒几次
# 用时估计：按真机实测，每分钟写约 3560 行指令（09-27/28 提速后 4 页：3505~3642 行/分，很稳定）。
# 写字机参数（加速度、速度）再改的话要重新量
LINES_PER_MIN = 3560

FIELD_NAMES = {
    'meeting': [('name', '会议名称'), ('time', '时间'), ('place', '地点'), ('expected', '应到人数'),
                ('actual', '实到人数'), ('host', '主持人'), ('recorder', '记录人')],
    'study': [('time', '学习时间'), ('place', '学习地点'), ('host', '主持人'), ('recorder', '记录人'),
              ('expected', '应到会人数'), ('actual', '实到会人数'), ('absent', '缺席人及原因')],
}
BOOK = {'meeting': '会议记录本', 'study': '政治理论学习记录本'}
# 文件夹里哪些文件是要手写的记录
_WANT = ('会议记录', '活动记录', '研讨记录')      # 研讨记录：正确政绩观等专项的"集中学习研讨记录"（09-28 补）
_SKIP = ('口播稿', '~$')


def _is_record_file(name):
    if not name.lower().endswith(('.docx', '.txt')) or any(s in name for s in _SKIP):
        return False
    if any(w in name for w in _WANT):
        return True
    return '政治理论学习' in name and ('讨论' in name or '记录' in name)


def _date_key(rec):
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', rec.fields.get('time', '') or '')
    if m:
        return tuple(int(x) for x in m.groups())
    return (9999, 99, 99)


def _name_date(path):
    """文件名里的日期（如"23.2026.09.09-……"）→"2026年9月9日"（和会议记录的时间栏同一写法）；没有就返回空。"""
    m = re.search(r'(20\d{2})[.\-_年](\d{1,2})[.\-_月](\d{1,2})', os.path.basename(path))
    if not m:
        return ''
    y, mo, d = (int(x) for x in m.groups())
    return '%d年%d月%d日' % (y, mo, d) if 1 <= mo <= 12 and 1 <= d <= 31 else ''


def _num_key(path):
    m = re.match(r'\s*(\d+)', os.path.basename(path))
    return int(m.group(1)) if m else 999


def _doc_key(rec):
    """一份记录的身份：文件名（不含路径、扩展名）。同一个文件放在哪、导入几次都算同一份。"""
    if rec.source:
        return os.path.splitext(os.path.basename(rec.source))[0]
    return 'manual:' + hashlib.md5('\n'.join(rec.body).encode('utf-8')).hexdigest()[:12]


def _key_seed(key):
    return int(hashlib.md5(key.encode('utf-8')).hexdigest()[:12], 16)


def _file_sig(path):
    """原稿文件的"指纹"（大小+修改时间）。队列里存着加入时的指纹，原稿之后被改了能认出来。"""
    try:
        st = os.stat(path)
        return '%d:%d' % (st.st_size, int(st.st_mtime))
    except OSError:
        return ''


class _Doc:
    def __init__(self, record, seed, hand):
        self.id = '%x' % random.getrandbits(40)
        self.record = record
        self.seed = seed
        self.hand = hand
        self.checked = True
        self.comp = None
        self.gcode = {}
        self.minutes = 0
        self.problems = []
        self.printed = set()            # 已写完的页（0 起）
        self.partial = None             # 开写了但没写完（终止）的页
        self.src_sig = _file_sig(record.source) if record.source else ''
        self.src_changed = False        # 原稿后来改过、但因为已经写过几页没自动换

    @property
    def next_page(self):
        """下一页该写的（第一个没写完的页，0 起）；全写完了回到 0。"""
        n = self.comp.pages if self.comp else 0
        return next((p for p in range(n) if p not in self.printed), 0)

    @property
    def name(self):
        return os.path.basename(self.record.source) if self.record.source else '手动输入'


class Api:
    def __init__(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        self._cfg = config.load()
        self._window = None
        self._fonts = {}
        self._docs = []
        self._cur = None
        self._grbl = Grbl(log=self.log)
        self._logs = []
        self._lock = threading.Lock()
        self._job = {'state': 'idle', 'page': 0, 'pages': 0, 'done': 0, 'total': 0, 'msg': '',
                     'doc': 0, 'docs': 0, 'doc_id': None, 'doc_name': ''}
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._next = threading.Event()
        self._thread = None
        self._zeroed = False            # 这次连接后是否已把起点、抬笔高度设好
        self._connecting = False        # 正在连接（这期间不许开始打印）
        self._restored = False          # 上次的队列是否已恢复（界面第一次 init 时恢复）

    # ---------------- 日志 ----------------
    def log(self, s):
        t = datetime.datetime.now()
        line = '%s  %s' % (t.strftime('%H:%M:%S'), s)
        with self._lock:
            self._logs.append(line)
            self._logs = self._logs[-200:]
        try:
            with open(os.path.join(LOG_DIR, t.strftime('%Y-%m-%d') + '.log'), 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass

    def log_js(self, s):
        """界面（网页）里出的错，记进日志。"""
        self.log(str(s)[:500])
        return True

    # ---------------- 初始化 ----------------
    def _font_problem(self, hand):
        names = '、'.join(os.path.splitext(f)[0] for f in (config.HANDS.get(hand) or {}).get('files', []))
        return ('笔迹"%s"的字库一个也没找到（%s）。它们在奎享雕刻的"在线字库"里免费下载，下载后放进本程序的 fonts 文件夹，'
                '或者留在奎享自己的字库目录里都行，不用改名。详见 fonts 文件夹里的"字库说明.txt"。' % (hand, names))

    def init(self):
        if not self._restored:
            self._restored = True
            for h, hd in config.HANDS.items():
                miss = config.missing_fonts(h)
                if miss and len(miss) < len(hd['files']):
                    self.log('笔迹"%s"缺 %s，只用其余版本轮换' % (h, '、'.join(os.path.splitext(f)[0] for f in miss)))
            try:
                self._restore_queue()
            except Exception as e:
                self.log('恢复上次的队列出错：%s\n%s' % (e, traceback.format_exc()))
        ports = list_ports()
        guess = self._cfg['machine'].get('port') or find_machine_port() or ''
        dh = self._cfg.get('hand_default', {}).get('meeting', '陈继世')
        dh = dh if dh in config.HANDS else '陈继世'
        return {'ports': ports, 'port': guess, 'field_names': FIELD_NAMES,
                'study_fields': self._cfg['study_fields'],
                'hands': [{'key': k, 'label': v['label']} for k, v in config.HANDS.items()],
                'style': self.get_style(), 'docs': self.docs(), 'current': self._current_dict(),
                'font_problem': self._font_problem(dh) if len(config.missing_fonts(dh)) == len(config.HANDS[dh]['files']) else ''}

    def _busy(self):
        return self._job['state'] in ('printing', 'paused', 'turn', 'turn_doc')

    def ports(self):
        return {'ports': list_ports(), 'port': self._cfg['machine'].get('port') or find_machine_port() or ''}

    # ---------------- 队列 ----------------
    def _doc(self, doc_id=None):
        doc_id = doc_id or self._cur
        for d in self._docs:
            if d.id == doc_id:
                return d
        return None

    def docs(self):
        out = []
        for i, d in enumerate(self._docs):
            r = d.record
            n = d.comp.pages if d.comp else 0
            left = [p for p in range(n) if p not in d.printed]
            out.append({'id': d.id, 'pos': i + 1, 'name': d.name, 'kind': r.kind, 'book': BOOK[r.kind],
                        'pages': n, 'minutes': d.minutes, 'checked': d.checked,
                        'hand': d.hand, 'recorder': r.fields.get('recorder', ''),
                        'date': r.fields.get('time', ''), 'problems': len(d.problems),
                        'overflow': bool(d.comp and d.comp.overflow),
                        'current': d.id == self._cur, 'status': self._status_text(d),
                        'left_pages': len(left), 'left_minutes': round(d.minutes * len(left) / n) if n else 0,
                        'next_page': d.next_page})
        return out

    def _status_text(self, d):
        """队列里每份下面那一行：打印情况。"""
        s = self._status_core(d)
        if d.src_changed:
            s += '（原稿后来改过，这里还是旧内容）'
        return s

    def _status_core(self, d):
        n = d.comp.pages if d.comp else 0
        k = sum(1 for p in d.printed if p < n)
        if self._job.get('doc_id') == d.id and self._job['state'] in ('printing', 'paused'):
            return '正在写第 %d 页（共 %d 页）' % (self._job['page'] + 1, n)
        if n and k >= n:
            return '已打完（共 %d 页）' % n
        if k == 0 and d.partial is None:
            return '未打印 · 共 %d 页 · 约 %d 分钟' % (n, d.minutes)
        if k == 0:
            return '第 %d 页写到一半停了 · 共 %d 页' % (d.partial + 1, n)
        s = '已打印 %d 页，还剩 %d 页' % (k, n - k)
        if d.partial is not None and d.partial not in d.printed:
            s += '（第 %d 页没写完）' % (d.partial + 1)
        return s

    # ---------------- 队列存盘（关掉程序再开还在，直到手动删除）----------------
    def _save_queue(self):
        cur = self._doc()
        data = {'current': _doc_key(cur.record) if cur else None, 'docs': [{
            'key': _doc_key(d.record), 'source': d.record.source,
            'record': {'kind': d.record.kind, 'fields': d.record.fields, 'topic': d.record.topic,
                       'body': d.record.body, 'title': d.record.title},
            'hand': d.hand, 'seed': d.seed, 'checked': d.checked,
            'printed': sorted(d.printed), 'partial': d.partial,
            'src_sig': d.src_sig, 'src_changed': d.src_changed} for d in self._docs]}
        try:
            config.save_queue(data)
        except Exception as e:
            self.log('保存队列没成功：%s' % e)

    def _restore_queue(self):
        """按上次存下的内容恢复（不重新读 Word，程序里改过的也保留）；排版种子一并恢复，排出来和上次一样。"""
        data = config.load_queue() or {}
        for e in data.get('docs') or []:
            try:
                r = e['record']
                rec = Record(kind=r['kind'], fields=dict(r.get('fields') or {}), topic=r.get('topic', ''),
                             body=list(r.get('body') or []), source=e.get('source', ''), title=r.get('title', ''))
                hand = e.get('hand') if e.get('hand') in config.HANDS else self._hand_for(rec)
                d = _Doc(rec, int(e['seed']), hand)
                d.checked = bool(e.get('checked', True))
                d.printed = set(int(p) for p in e.get('printed') or [])
                d.partial = e.get('partial')
                d.src_sig = e.get('src_sig', '')
                d.src_changed = bool(e.get('src_changed'))
                now = _file_sig(rec.source) if rec.source else ''
                if now and d.src_sig and now != d.src_sig:
                    # 原稿在加入队列之后改过（比如单位那边改了规范）：一页没写过的直接换成新原稿；写过的不动、在状态里注明
                    if not d.printed and d.partial is None:
                        try:
                            self._reload(d, self._parse_file(rec.source))
                        except Exception as ex:
                            self.log('读新原稿没成功，先用队列里存的：%s（%s）' % (rec.source, ex))
                            self._compose(d)
                    else:
                        d.src_changed = True
                        self._compose(d)
                else:
                    self._compose(d)
                self._docs.append(d)
            except Exception as ex:
                self.log('恢复队列里的一份没成功：%s（%s）' % (e.get('key'), ex))
        if not self._docs:
            return
        # 选中上次选的；上次选的已写完的话，选第一份勾选着、还没写完的
        cur = next((d for d in self._docs if _doc_key(d.record) == data.get('current')), None)
        if cur is None or (cur.comp and len(cur.printed) >= cur.comp.pages):
            cur = next((d for d in self._docs if d.checked and d.comp and len(d.printed) < d.comp.pages), None) or cur
        self._cur = (cur or self._docs[0]).id
        self.log('已恢复上次的队列：%d 份' % len(self._docs))

    def _current_dict(self):
        d = self._doc()
        if d is None:
            return None
        r = d.record
        out = {'id': d.id, 'path': r.source, 'name': d.name, 'kind': r.kind, 'fields': r.fields,
               'topic': r.topic, 'body': '\n'.join(r.body), 'title': r.title, 'hand': d.hand,
               'next_page': d.next_page}
        out.update(self._summary(d))
        return out

    def _summary(self, d):
        c = d.comp
        empty = [name for k, name in FIELD_NAMES[d.record.kind] if not (d.record.fields.get(k) or '').strip()]
        return {'pages': c.pages if c else 0, 'missing': ''.join(sorted(c.missing)) if c else '',
                'overflow': c.overflow if c else [], 'complete': c.complete if c else True,
                'minutes': d.minutes, 'kind': d.record.kind, 'empty': empty, 'problems': d.problems[:8],
                'hand': d.hand}

    def _hand_for(self, rec):
        rh = self._cfg.get('recorder_hands', {})
        who = (rec.fields.get('recorder') or '').strip()
        if who and rh.get(who) in config.HANDS:
            return rh[who]
        h = self._cfg.get('hand_default', {}).get(rec.kind, '陈继世')
        return h if h in config.HANDS else '陈继世'

    def _add_paths(self, paths):
        if self._busy():
            return {'error': '正在打印，写完或终止后再添加'}
        new, errors, touched = [], [], []
        replaced = 0
        for p in paths:
            if not p:
                continue
            try:
                rec = self._parse_file(p)
            except Exception as e:
                errors.append('%s：%s' % (os.path.basename(p), e))
                self.log('读取失败：%s\n%s' % (p, traceback.format_exc()))
                continue
            key = _doc_key(rec)
            old = next((d for d in self._docs if _doc_key(d.record) == key), None)
            if old is not None:
                # 同一份（按文件名）已在队列里：一页没写过就换成这次读到的内容；写过的不动，免得和已写的页对不上
                if old.printed or old.partial is not None:
                    errors.append('%s：队列里已经有这一份，而且写过几页，没有替换。要用新原稿，先把旧的删掉再加入。' % os.path.basename(p))
                    continue
                self._reload(old, rec)
                replaced += 1
                touched.append(old)
                continue
            # 排版固定：同一份（按文件名）每次导入都用同一个种子；点过"换一种写法"的用记下来的那个
            seed = self._cfg.get('doc_seeds', {}).get(key) or _key_seed(key)
            d = _Doc(rec, seed, self._hand_for(rec))
            new.append(d)
        new.sort(key=lambda d: (0 if d.record.kind == 'meeting' else 1, _date_key(d.record),
                                _num_key(d.record.source), d.name))
        for d in list(new):
            try:
                self._compose(d)
            except Exception as e:                    # 比如字库没准备好：这一份不加，告诉用户原因
                errors.append('%s：%s' % (d.name, e))
                self.log('排版出错：%s\n%s' % (d.name, traceback.format_exc()))
                new.remove(d)
                continue
            self._docs.append(d)
            self.log('加入队列：%s（%s，%d 页）' % (d.name, BOOK[d.record.kind], d.comp.pages))
        if new:
            self._cur = new[0].id
            self._cfg['last_dir'] = os.path.dirname(new[0].record.source)
            config.save(self._cfg)
        if new or replaced:
            self._save_queue()
        # 表头写不下的当场提示（不缩小字，要改短）
        too_long = ['《%s》：%s' % (os.path.splitext(d.name)[0], '、'.join(d.comp.overflow))
                    for d in new + touched if d.comp and d.comp.overflow]
        return {'added': len(new), 'replaced': replaced, 'errors': errors, 'too_long': too_long, 'docs': self.docs(),
                'current': self._current_dict()}

    def _parse_file(self, p):
        rec = parse(p)
        if rec.kind == 'study':
            # 学习时间：单位放进手抄队列的文件名带着正式日期（"序号.YYYY.MM.DD-名称"，当月第二个周三，09-28 起），
            # 取出来填上；文件名里没有日期的留空，开印核对单会提示。日期不沿用上个月的。其余栏按上次的值带出。
            if not rec.fields.get('time'):
                rec.fields['time'] = _name_date(p)
            for k, v in self._cfg['study_fields'].items():
                if k != 'time' and not rec.fields.get(k):
                    rec.fields[k] = v
        return rec

    def _reload(self, d, rec):
        """把队列里的一份换成原稿的新内容（种子、笔迹、勾选不变），重排。"""
        d.record = rec
        d.src_sig = _file_sig(rec.source)
        d.src_changed = False
        self._compose(d)
        self.log('《%s》按新原稿重排：%d 页' % (os.path.splitext(d.name)[0], d.comp.pages))

    def _start_dir(self, folder=False):
        """选文件、选文件夹的对话框从哪里开始。每月要手抄的定稿放在"写字机手抄队列"下、按月分文件夹
        （如"9月份会议"）：选文件夹从手抄队列开始，单击这个月的文件夹就行；
        选文件从上次在手抄队列里选过的那个月开始。没有手抄队列文件夹的，从上次的位置开始。"""
        root = self._cfg.get('handcopy_dir') or config.HANDCOPY_DIR
        last = self._cfg.get('last_dir') or ''
        if os.path.isdir(root):
            inside = os.path.normcase(last).startswith(os.path.normcase(root) + os.sep)
            return last if (not folder and inside and os.path.isdir(last)) else root
        return last if os.path.isdir(last) else os.path.expanduser('~')

    def choose_files(self):
        import webview
        start = self._start_dir()
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, directory=start, allow_multiple=True,
                                            file_types=('Word 文档 (*.docx)', '文本文件 (*.txt)', '所有文件 (*.*)'))
        if not r:
            return None
        return self._add_paths(list(r) if isinstance(r, (list, tuple)) else [r])

    def choose_folder(self):
        import webview
        start = self._start_dir(folder=True)
        r = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        if not r:
            return None
        folder = r[0] if isinstance(r, (list, tuple)) else r
        return self.add_folder(folder)

    def add_folder(self, folder):
        names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        paths = [os.path.join(folder, n) for n in names if _is_record_file(n)]
        if not paths:
            if any(os.path.isdir(os.path.join(folder, n)) for n in names):
                msg = ('这个文件夹里是按月份分的文件夹，没有直接放记录。请在对话框里单击选中某个月的文件夹'
                       '（如"9月份会议"），再点"选择文件夹"')
            else:
                msg = '这个文件夹里没找到会议记录、活动记录或政治理论学习（含讨论）的 Word 文件'
            return {'added': 0, 'errors': [msg], 'docs': self.docs(), 'current': self._current_dict()}
        out = self._add_paths(paths)
        out['folder'] = folder
        return out

    def add_files(self, paths):
        return self._add_paths(paths)

    def select_doc(self, doc_id):
        if self._doc(doc_id) and doc_id != self._cur:
            self._cur = doc_id
            self._save_queue()
        return {'docs': self.docs(), 'current': self._current_dict()}

    def remove_doc(self, doc_id):
        if self._busy():
            return {'error': '正在打印'}
        self._docs = [d for d in self._docs if d.id != doc_id]
        if self._cur == doc_id:
            self._cur = self._docs[0].id if self._docs else None
        self._save_queue()
        return {'docs': self.docs(), 'current': self._current_dict()}

    def clear_docs(self):
        if self._busy():
            return {'error': '正在打印'}
        self._docs, self._cur = [], None
        self._save_queue()
        return {'docs': [], 'current': None}

    def move_doc(self, doc_id, delta):
        if self._busy():
            return {'error': '正在打印'}
        i = next((k for k, d in enumerate(self._docs) if d.id == doc_id), None)
        if i is not None:
            j = max(0, min(len(self._docs) - 1, i + int(delta)))
            self._docs.insert(j, self._docs.pop(i))
            self._save_queue()
        return {'docs': self.docs()}

    def toggle_doc(self, doc_id, checked):
        d = self._doc(doc_id)
        if d and not self._busy():
            d.checked = bool(checked)
            self._save_queue()
        return {'docs': self.docs()}

    # ---------------- 编辑与排版 ----------------
    def update_record(self, kind, fields, topic, body):
        d = self._doc()
        if d is None:
            return None
        if self._busy():
            out = self._summary(d)
            out['busy'] = True
            return out
        r = d.record
        r.kind = kind
        r.fields = {k: (v or '').strip() for k, v in fields.items()}
        r.topic = (topic or '').strip() if kind == 'meeting' else ''
        r.body = [p.strip() for p in (body or '').split('\n') if p.strip()]
        self._compose(d)
        self._save_queue()
        return self._summary(d)

    def set_hand(self, hand):
        d = self._doc()
        if d is None or hand not in config.HANDS:
            return None
        if self._busy():
            out = self._summary(d)
            out['busy'] = True
            return out
        d.hand = hand
        who = (d.record.fields.get('recorder') or '').strip()
        if who:
            self._cfg.setdefault('recorder_hands', {})[who] = hand
            config.save(self._cfg)
        self._compose(d)
        self._save_queue()
        return self._summary(d)

    def reseed(self):
        d = self._doc()
        if d is None or self._busy():
            return self._summary(d) if d else None
        d.seed = random.randint(1, 10 ** 12)
        # 记住这份换过的写法，下次导入还是这个样子（最多记 500 份，旧的先丢）
        seeds = self._cfg.setdefault('doc_seeds', {})
        seeds.pop(_doc_key(d.record), None)
        seeds[_doc_key(d.record)] = d.seed
        while len(seeds) > 500:
            seeds.pop(next(iter(seeds)))
        config.save(self._cfg)
        self._compose(d)
        self._save_queue()
        return self._summary(d)

    def _font(self, name):
        if name not in self._fonts:
            self._fonts[name] = GFont.open(config.font_path(name))
        return self._fonts[name]

    def _style(self, kind, hand, override=None):
        sc = dict(self._cfg['style'])
        if override:
            sc.update(override)
        fc = self._cfg['forms'][kind]
        hd = config.HANDS.get(hand) or config.HANDS['陈继世']
        have = [f for f in hd['files'] if f not in config.missing_fonts(hand)]   # 缺了几个版本就用其余的轮换
        if not have:
            raise RuntimeError(self._font_problem(hand))
        fonts = [(self._font(f), 1) for f in have]
        punct_ok = tuple(i for i, f in enumerate(have) if f in hd['punct']) if hd.get('punct') else None
        return Style(fonts=fonts, font_pct=float(fc['font_pct']), gap=float(fc['gap']),
                     indent_half_spaces=int(sc.get('indent_half_spaces', 2)),
                     amount=float(sc.get('amount', 1.0)), base_line=float(sc.get('base_line', 1.0)),
                     punct_ok=punct_ok)

    def _compose(self, d):
        r = d.record
        t0 = time.time()
        d.comp = compose(r, FORMS[r.kind], self._style(r.kind, d.hand), seed=d.seed)
        d.gcode = {}
        n_lines = sum(len(self._page_gcode(d, p)[0]) for p in range(d.comp.pages))
        d.minutes = round(n_lines / LINES_PER_MIN)
        d.problems = check(d.comp)
        self.log('排版 %s：%d 页，用时 %.1f 秒%s' % (d.name, d.comp.pages, time.time() - t0,
                                             ('；自检提示 %d 条' % len(d.problems)) if d.problems else ''))

    def page_svg(self, page, width_px=0):
        d = self._doc()
        c = d.comp if d else None
        if c is None or page >= c.pages:
            return ''
        w, h = c.page_size(page)
        parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 %.1f %.1f" preserveAspectRatio="xMidYMin meet">' % (w + 4, h + 4),
                 '<rect x="0" y="0" width="%.2f" height="%.2f" fill="#fffdf6" stroke="#b9b3a3" stroke-width="0.3"/>' % (w, h)]
        for x0, x1, y in c.ruled_lines(page):
            parts.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#c9c2b0" stroke-width="0.25"/>' % (x0, y, x1, y))
        for x, y, w_, t in c.labels(page):
            parts.append('<text x="%.2f" y="%.2f" font-size="4.3" fill="#9c9483" font-family="SimSun,serif" '
                         'textLength="%.2f" lengthAdjust="spacingAndGlyphs">%s</text>' % (x, y, w_, t))
        parts.append(_ink_path(c.page_items(page)))
        parts.append('</svg>')
        return ''.join(parts)

    # ---------------- 笔迹设置 ----------------
    def get_style(self):
        sc = self._cfg['style']
        return {'amount': float(sc.get('amount', 1.0)),
                'hand_default': self._cfg.get('hand_default', {}),
                'recorder_hands': self._cfg.get('recorder_hands', {})}

    def set_style(self, amount, hand_meeting, hand_study):
        if self._busy():
            return {'error': '正在打印，写完再改'}
        sc = self._cfg['style']
        sc['amount'] = max(0.3, min(2.0, float(amount)))
        hd = self._cfg.setdefault('hand_default', {})
        if hand_meeting in config.HANDS:
            hd['meeting'] = hand_meeting
        if hand_study in config.HANDS:
            hd['study'] = hand_study
        config.save(self._cfg)
        for d in self._docs:
            self._compose(d)
        return {'ok': True, 'docs': self.docs(), 'current': self._current_dict()}

    def preview_style(self, amount, hand, text=''):
        """笔迹设置对话框里的样张：两行字。"""
        text = text or '张三：今天召开党员大会，应到16人，实到16人，符合人数要求。下面进行第一项议题，传达学习上级党组织文件精神，党员同志要把学习成果落实到岗位上。'
        st = self._style('meeting', hand, {'amount': float(amount)})
        ts = Typesetter(st, seed=7)
        lh = 215 / 21
        W = 149.0
        slots = iter([Slot(0, 0, W, lh * (i + 1), lh) for i in range(4)])
        items, _, _ = ts.flow([normalize(text)], slots, indent=True)
        rows = max([p.line for p in items] + [0]) - min([p.line for p in items] + [0]) + 1
        H = lh * max(rows, 2) + 3
        parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 %.1f %.1f">' % (W + 2, H + 2)]
        for i in range(max(rows, 2)):
            parts.append('<line x1="0" y1="%.2f" x2="%.2f" y2="%.2f" stroke="#d6cfbe" stroke-width="0.25"/>' % (lh * (i + 1), W, lh * (i + 1)))
        parts.append(_ink_path(items))
        parts.append('</svg>')
        return ''.join(parts)

    # ---------------- 写字机 ----------------
    def _machine(self, kind='meeting'):
        mc = self._cfg['machine']
        fc = self._cfg['forms'][kind]
        return Machine(feed=float(mc['feed']), travel=float(mc['travel']), pen_type=mc['pen_type'],
                       z_down=float(mc['z_down']), z_up=float(mc['z_up']), z_speed=float(mc['z_speed']),
                       z_hover=None if mc.get('z_hover') is None else float(mc['z_hover']),
                       servo_down=int(mc['servo_down']), pen_down_delay=float(mc['pen_down_delay']),
                       pen_up_delay=float(mc['pen_up_delay']), near=float(mc['near']),
                       flip_x=bool(mc['flip_x']), flip_y=bool(mc['flip_y']), swap_xy=bool(mc['swap_xy']),
                       offset_x=float(mc['offset_x']) + float(fc.get('offset_x', 0)),
                       offset_y=float(mc['offset_y']) + float(fc.get('offset_y', 0)))

    def _page_gcode(self, d, page):
        if page not in d.gcode:
            d.gcode[page] = page_gcode(page_strokes(d.comp.placed, page), self._machine(d.record.kind),
                                       origin_z=False, compact=True)
        return d.gcode[page]

    def connect(self, port):
        if self._busy():
            return {'ok': False, 'msg': '正在打印，先终止再连接'}
        if self._connecting:
            return {'ok': False, 'msg': '正在连接，请稍等'}
        # 连接的整个过程（打开串口、问参数、设起点、调参数）做完之前，界面上算"没连上"，开始打印会被挡回
        self._connecting = True
        try:
            try:
                v = self._grbl.connect(port, int(self._cfg['machine'].get('baud', 115200)))
            except GrblError as e:
                self.log(str(e))
                return {'ok': False, 'msg': str(e)}
            if not port.lower().startswith('mock'):
                self._cfg['machine']['port'] = port
                config.save(self._cfg)
            st = self._grbl.settings
            self.log('已连接 %s：%s；%s；每次在途 ≤%d 字节；参数 %s' % (
                port, v, self._grbl.build, self._grbl.budget, ', '.join('$%d=%g' % kv for kv in sorted(st.items()))))
            self._zeroed = False
            self._zero_here()
            self._apply_tune()
            return {'ok': True, 'msg': v}
        finally:
            self._connecting = False

    def _apply_tune(self):
        """把写字机里的加速度、拐角参数调成设置里 machine.tune 的值（09-27 用户同意试的提速），
        只在和写字机现有值不同时才写；第一次改之前的原值记进 machine.tune_original，要改回就把 tune 改成原值。
        这些参数存在写字机里，奎享写字也会跟着用。"""
        mc = self._cfg['machine']
        tune = mc.get('tune') or {}
        orig = mc.setdefault('tune_original', {})
        cur_all = self._grbl.settings
        changed = []
        for k, v in tune.items():
            n, v = int(k), float(v)
            cur = cur_all.get(n)
            if cur is None or abs(cur - v) < 1e-9:
                continue                      # 写字机没报这项，或者已经是这个值
            try:
                self._grbl.command('$%d=%g' % (n, v))
            except GrblError as e:
                self.log('写字机参数 $%d 没改成（%s）' % (n, e))
                continue
            orig.setdefault(k, cur)
            cur_all[n] = v
            changed.append('$%d %g→%g' % (n, cur, v))
        if changed:
            config.save(self._cfg)
            self.log('已调整写字机参数：%s（原值记在设置里，可改回）' % '，'.join(changed))

    def _zero_here(self):
        """连上时笔所在的位置当起点、这时笔的高度当抬笔高度（奎享"设置原点"的做法；
        写字机停着时笔本来就是抬着的，用户不用管）。控制板里存着的旧起点（可能是奎享留下的）就此作废。
        控制板报警时设不了，解除报警后再设。"""
        try:
            self._grbl.set_origin()
            self._zeroed = True
            self.log('已把连接时笔的位置当作起点、这时笔的高度当作抬笔高度')
        except GrblError as e:
            self.log('暂时没能设起点（%s），解除报警后会再设' % e)

    def disconnect(self):
        if self._busy():
            return {'ok': False, 'msg': '正在打印，先终止再断开'}
        self._grbl.close()
        self.log('已断开写字机')
        return {'ok': True}

    def _machine_cmd(self, fn, *a):
        if self._connecting:
            return {'ok': False, 'msg': '正在连接写字机，请稍等'}
        if not self._grbl.connected:
            return {'ok': False, 'msg': '写字机没有连接'}
        if self._job['state'] == 'printing':
            return {'ok': False, 'msg': '正在写字，先暂停'}
        try:
            fn(*a)
            return {'ok': True}
        except GrblError as e:
            self.log(str(e))
            return {'ok': False, 'msg': str(e)}

    # 用户的做法（09-27 确认）：固定好纸和笔，用手把笔推到左上角，需要时按一下笔看落点，然后开写。
    # 所以不设方向键、抬落笔、走边框这些按钮：开写时笔尖在哪，哪就是这一页的左上角。

    def unlock(self):
        r = self._machine_cmd(self._grbl.unlock)
        if r['ok'] and not self._zeroed:
            self._zero_here()
        return r

    def test_sound(self):
        """"试听提醒"按钮：听一下音量。"""
        self._remind('换页')
        return {'ok': True}

    def _remind(self, sound):
        notify.say(sound, self.log)
        notify.flash(TITLE)

    # ---------------- 打印 ----------------
    def status(self):
        g = self._grbl
        with self._lock:
            logs = self._logs[-12:]
        return {'connected': g.connected and not self._connecting, 'connecting': self._connecting,
                'state': g.state, 'alarm': g.alarm, 'pos': g.wpos,
                'version': g.version, 'job': dict(self._job), 'logs': logs}

    def plan(self, from_pos=1, from_page=1):
        """开印前核对：要写哪些份、空栏目、自检提示。"""
        todo = [d for d in self._docs if d.checked]
        from_pos = max(1, min(int(from_pos), max(1, len(todo))))
        todo = todo[from_pos - 1:]
        # warn：会把纸写坏的问题（空栏、写不下、缺字），有这些才拦一下；info：只记进日志、不拦（09-28 用户要"点开始打印直接打印"）
        warn, info = [], []
        for i, d in enumerate(todo):
            s = self._summary(d)
            tag = '第%d份《%s》' % (from_pos + i, os.path.splitext(d.name)[0][:24])
            if s['empty'] and not (i == 0 and int(from_page) > 1):
                warn.append('%s：%s 是空的' % (tag, '、'.join(s['empty'])))
            if s['overflow'] and not (i == 0 and int(from_page) > 1):
                warn.append('%s：%s，按原大写不下（程序不缩小字，多出的字不写），请先改短' % (tag, '、'.join(s['overflow'])))
            if d.record.kind == 'study' and not s['empty'] and not (i == 0 and int(from_page) > 1):
                # 学习记录的人数、缺席原稿里没有，是按上次的值带出的，每月可能不同
                f = d.record.fields
                info.append('%s：学习表头——学习时间 %s，应到 %s 人、实到 %s 人，缺席 %s（人数和缺席是按上次的值带出的）' % (
                    tag, f.get('time'), f.get('expected'), f.get('actual'), f.get('absent') or '无'))
            if s['missing']:
                warn.append('%s：字库里没有"%s"，会空着' % (tag, s['missing']))
            for p in d.problems[:2]:
                info.append('%s：版式自检 %s' % (tag, p))
        pages = sum(d.comp.pages for d in todo) - (int(from_page) - 1 if todo else 0)
        minutes = sum(d.minutes for d in todo)
        return {'docs': len(todo), 'pages': max(0, pages), 'minutes': minutes, 'warn': warn, 'info': info,
                'first': todo[0].name if todo else ''}

    def start_print(self, from_pos=1, from_page=1):
        if not self._docs:
            return {'ok': False, 'msg': '先选要写的原稿'}
        if self._connecting:
            return {'ok': False, 'msg': '写字机还在连接，等左下角显示"断开"（连好了）再开始'}
        if not self._grbl.connected:
            return {'ok': False, 'msg': '写字机没有连接'}
        if self._busy():
            return {'ok': False, 'msg': '已经在打印'}
        if self._grbl.alarm:
            return {'ok': False, 'msg': '写字机处于报警状态，先点"解除报警"'}
        todo = [d for d in self._docs if d.checked]
        if not todo:
            return {'ok': False, 'msg': '队列里没有勾选要写的原稿'}
        for note in self.plan(from_pos, from_page)['info']:     # 不弹窗，记进日志备查
            self.log('开印前留意：' + note)
        from_pos = max(1, min(int(from_pos), len(todo)))
        todo = todo[from_pos - 1:]
        first_page = max(0, min(int(from_page) - 1, todo[0].comp.pages - 1))
        for d in todo:
            if d.record.kind == 'study':
                for k in self._cfg['study_fields']:
                    if k != 'time' and d.record.fields.get(k):      # 学习时间每月不同，不当下次的默认值
                        self._cfg['study_fields'][k] = d.record.fields.get(k, '')
        config.save(self._cfg)
        # 开印时把所有要写的页一次生成好冻结下来，打印期间不受界面改动影响
        jobs = []
        for i, d in enumerate(todo):
            start = first_page if i == 0 else 0
            jobs.append((d.id, d.name, d.record.kind, [(p, self._page_gcode(d, p)[0]) for p in range(start, d.comp.pages)],
                         d.comp.pages))
        self._pause.clear(); self._cancel.clear(); self._next.clear()
        self._job = {'state': 'printing', 'page': first_page, 'pages': todo[0].comp.pages, 'done': 0, 'total': 0,
                     'msg': '', 'doc': from_pos, 'docs': from_pos - 1 + len(todo), 'doc_id': todo[0].id,
                     'doc_name': todo[0].name, 'from_pos': from_pos}
        self._cur = todo[0].id
        self._thread = threading.Thread(target=self._print_worker, args=(jobs, from_pos), daemon=True)
        self._thread.start()
        return {'ok': True}

    def _wait_next(self, sound):
        """等用户换好纸点"继续"。先语音提醒一次；没人理就每隔 REMIND_EVERY 秒再提醒，最多 REMIND_TIMES 次。"""
        self._next.clear()
        self._remind(sound)
        t0, n = time.time(), 0
        while not self._next.is_set():
            if self._cancel.is_set():
                raise _Cancelled()
            if n < REMIND_TIMES and time.time() - t0 >= REMIND_EVERY * (n + 1):
                n += 1
                self._remind(sound)
            time.sleep(0.1)

    def _print_worker(self, jobs, from_pos):
        m = self._machine('meeting')
        pu = pen_up(m)[0]                               # 抬满
        pens = {pu, pen_up(m, hover=True)[0]}           # 两笔之间只抬一点的那种也算抬笔
        total_docs = from_pos - 1 + len(jobs)
        try:
            for j, (doc_id, name, kind, pages, npages) in enumerate(jobs):
                pos = from_pos + j
                self._cur = doc_id
                for k, (page, lines) in enumerate(pages):
                    self._job.update(state='printing', page=page, pages=npages, done=0, total=len(lines),
                                     doc=pos, docs=total_docs, doc_id=doc_id, doc_name=name,
                                     msg='正在写第 %d/%d 份《%s》第 %d 页（共 %d 页）' % (
                                         pos, total_docs, os.path.splitext(name)[0][:20], page + 1, npages))
                    self.log('开始写 %s 第 %d/%d 页，%d 行指令' % (name, page + 1, npages, len(lines)))
                    t0 = time.time()
                    dd = self._doc(doc_id)
                    if dd:
                        dd.partial = page               # 写完前被终止，就记着"这页没写完"
                        self._save_queue()

                    def prog(a, t):
                        self._job['done'] = a

                    def on_paused():
                        # 停下时笔可能只抬了一点，再抬满，免得挡手；继续时会先回到原来的高度
                        self._grbl.command(pu)
                        self._job.update(state='paused', msg='已暂停（笔已抬起）。点"继续"接着写')

                    ok = self._grbl.stream(lines, progress=prog, cancel=self._cancel, pause=self._pause,
                                           is_pen_up=lambda l: l in pens, on_paused=on_paused)
                    if not ok:
                        raise _Cancelled()
                    self._job.update(state='printing', msg='第 %d 页收尾中…' % (page + 1))
                    self._grbl.wait_idle(timeout=900, cancel=self._cancel)
                    if self._cancel.is_set():
                        raise _Cancelled()
                    idle = self._grbl.idle_in_stream
                    self.log('第 %d 页写完，用时 %.1f 分钟；%s' % (
                        page + 1, (time.time() - t0) / 60,
                        ('中途停顿 %d 次（指令没跟上，可能缺笔画）' % idle) if idle else '中途没有停顿'))
                    if dd:
                        dd.printed.add(page)
                        dd.partial = None
                        if dd.comp and all(p in dd.printed for p in range(dd.comp.pages)):
                            # 整份写完自动取消勾选，免得下次点开始打印又从头重写；要重写就重新勾上
                            dd.checked = False
                            self.log('《%s》已全部写完，已自动取消勾选' % os.path.splitext(name)[0])
                        self._save_queue()
                    if k + 1 < len(pages):
                        nxt = pages[k + 1][0]
                        # 纸两面都写：下一页是偶数页就写在这一张的背面，提醒"翻页"；奇数页才换空白页（09-28 用户定）
                        flip = (nxt + 1) % 2 == 0
                        self._job.update(state='turn', page=nxt, flip=flip,
                                         msg=('本页已打完，请您翻页（接着写第 %d 页），翻好后点"继续"' if flip else
                                              '本页已打完，请您更换空白页（接着写第 %d 页），换好后点"继续"') % (nxt + 1))
                        self._wait_next('翻页' if flip else '换页')
                if j + 1 < len(jobs):
                    nid, nname, nkind, _, _ = jobs[j + 1]
                    change = nkind != kind
                    self._job.update(state='turn_doc', page=0, doc=pos + 1, doc_id=nid, doc_name=nname,
                                     next_kind=nkind, book_change=change,
                                     msg='本份已打完（第 %d 份）。下一份《%s》：%s' % (
                                         pos, os.path.splitext(nname)[0][:20],
                                         ('请您换成%s的首页' % BOOK[nkind]) if change else '请您更换带表格的首页'))
                    self._cur = nid
                    self._wait_next(('换' + BOOK[nkind]) if change else '换首页')
            self._job.update(state='done', msg='全部已打完（%d 份）' % len(jobs))
            self.log('队列全部写完：%d 份' % len(jobs))
            self._remind('全部完成')
        except _Cancelled:
            self._job.update(state='stopped', msg='已终止')
            self.log('打印已终止')
        except GrblError as e:
            self._job.update(state='error', msg='出错停止：%s' % e)
            self.log('打印出错：%s' % e)
            self._remind('出错')
            try:
                self._grbl.abort(m.z_up, m.z_speed)
            except Exception:
                pass
        except Exception as e:
            self._job.update(state='error', msg='程序出错：%s' % e)
            self.log('打印程序出错：%s\n%s' % (e, traceback.format_exc()))
            self._remind('出错')

    def pause(self):
        if self._job['state'] == 'printing':
            self._pause.set()
            self._job['msg'] = '正在暂停：写完当前这一笔就停…'
        return {'ok': True}

    def resume(self):
        if self._pause.is_set():
            self._pause.clear()
            self._job.update(state='printing', msg='继续写')
        return {'ok': True}

    def next_page(self):
        if self._job['state'] in ('turn', 'turn_doc'):
            self._next.set()
        return {'ok': True}

    def stop(self):
        m = self._machine('meeting')
        if self._busy():
            st = self._job['state']
            self._cancel.set()
            self._pause.clear()
            if st not in ('turn', 'turn_doc'):
                threading.Thread(target=self._grbl.abort, args=(m.z_up, m.z_speed), daemon=True).start()
        elif self._grbl.connected:
            # 不在写字时按终止（比如正在回左上角）：就地停住抬笔，不再回去
            self.log('手动急停')
            threading.Thread(target=self._grbl.abort, args=(m.z_up, m.z_speed, False), daemon=True).start()
        return {'ok': True}

    # ---------------- 导出 ----------------
    def export_gcode(self):
        d = self._doc()
        if d is None:
            return {'ok': False, 'msg': '先选原稿'}
        base = os.path.splitext(d.name)[0]
        out = os.path.join(EXPORT_DIR, base)
        os.makedirs(out, exist_ok=True)
        for p in range(d.comp.pages):
            # 给奎享用的文件照奎享的习惯：开头连抬笔高度一起设（发送时笔要抬着）
            lines, _, _ = page_gcode(page_strokes(d.comp.placed, p), self._machine(d.record.kind), origin_z=True)
            with open(os.path.join(out, '第%d页.nc' % (p + 1)), 'w', encoding='ascii') as f:
                f.write('\n'.join(lines) + '\n')
        os.startfile(out)
        return {'ok': True, 'msg': out}

    def open_logs(self):
        os.startfile(LOG_DIR)
        return {'ok': True}


def _ink_path(items):
    ink = []
    for p in items:
        for s in p.strokes:
            if len(s) == 1:
                s = [s[0], (s[0][0] + 0.05, s[0][1])]
            ink.append('M' + ' L'.join('%.2f %.2f' % pt for pt in s))
    return ('<path d="%s" fill="none" stroke="#1f2a44" stroke-width="0.42" stroke-linecap="round" '
            'stroke-linejoin="round"/>' % ' '.join(ink))


class _Cancelled(Exception):
    pass
