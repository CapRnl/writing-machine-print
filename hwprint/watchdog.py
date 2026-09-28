"""界面卡住时自动留证据。

09-28 早上两次"写完、语音提醒之后，点任务栏上的程序没反应，只能在任务管理器里结束"（06:19、06:27），
Windows 没留下记录，代码里也没找到一定会卡住的地方。所以在后台每 2 秒问一下主窗口还有没有反应：
连续 6 秒没反应，就在日志里记一笔（当时的打印状态、窗口是不是最小化、最近一次提醒、各线程停在哪一行、
开着哪些窗口）；恢复了再记一句卡了多久。下次再卡，看日志就知道卡在哪。
另外记"程序启动""程序关闭"；上次没有正常关闭的（被结束或崩溃），下次启动时补记一句。

只记录，不处理：写字在后台线程里，界面卡住不影响正在写的这一页。
这里的检查都不往窗口发需要等回复的消息（只有带超时的 WM_NULL），界面卡住时它自己不会跟着卡。
"""

import ctypes
import datetime
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes

from . import notify

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
_user32.SendMessageTimeoutW.restype = ctypes.c_size_t
for _f in ('IsIconic', 'IsWindowEnabled', 'IsHungAppWindow'):
    getattr(_user32, _f).argtypes = [wintypes.HWND]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

CHECK_EVERY = 2           # 秒
REPORT_AFTER = 6          # 连续这么多秒没反应才记
DISABLED_REPORT = 90      # 主窗口一直被对话框挡着（禁用）这么久也记一笔：看是不是对话框藏到了别的窗口后面


class _Log:
    """直接追加到当天的日志文件，不经过 Api.log 的锁（万一卡住和锁有关，这里也记得下来）。"""
    def __init__(self, log_dir):
        self.dir = log_dir

    def __call__(self, s):
        t = datetime.datetime.now()
        try:
            with open(os.path.join(self.dir, t.strftime('%Y-%m-%d') + '.log'), 'a', encoding='utf-8') as f:
                f.write('%s  %s\n' % (t.strftime('%H:%M:%S'), s))
        except Exception:
            pass


def responds(hwnd, ms=2000):
    """窗口 ms 毫秒内处理了一条空消息就算有反应；系统已判定"无响应"的立刻返回 False。"""
    res = ctypes.c_size_t()
    return bool(_user32.SendMessageTimeoutW(hwnd, 0, 0, 0, 0x0002, ms, ctypes.byref(res)))  # WM_NULL, SMTO_ABORTIFHUNG


def thread_stacks(depth=6):
    """各线程正在执行的 Python 代码位置（最里层在前）；停在同一处的合并成一条，界面线程排第一。
    界面线程是 pywebview 起的窗口消息循环（最外层在 winforms.py）；它若停在 winforms.py 里调 .NET 的那一行，
    说明卡在 .NET / WebView2 内部，不在本程序的 Python 代码里。"""
    names = {t.ident: t.name for t in threading.enumerate()}
    me = threading.get_ident()
    groups = {}
    for ident, frame in sys._current_frames().items():
        if ident == me:
            continue
        full = traceback.extract_stack(frame)
        who = '界面线程' if os.path.basename(full[0].filename) == 'winforms.py' else names.get(ident, str(ident))
        where = ' ← '.join('%s:%d %s' % (os.path.basename(f.filename), f.lineno, f.name) for f in reversed(full[-depth:]))
        groups.setdefault(where, []).append(who)
    out = []
    for where, who in sorted(groups.items(), key=lambda kv: ('界面线程' not in kv[1], -len(kv[1]))):
        tag = '、'.join(who[:3]) + (' 等 %d 个' % len(who) if len(who) > 3 else '')
        out.append('    [%s] %s' % (tag, where))
    return '\n'.join(out)


def windows_text():
    """本程序开着的（看得见的）窗口：类名、标题、是否被禁用。"""
    out = []
    for hwnd, title, vis in notify.own_windows():
        if not vis:
            continue
        cls = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, cls, 64)
        out.append('%s「%s」%s' % (cls.value[:28], title, '' if _user32.IsWindowEnabled(hwnd) else '（禁用）'))
    return '；'.join(out) or '没有看得见的窗口'


def _report(hwnd, secs, context):
    last = notify.last
    ago = ('最近一次提醒"%s"在 %d 秒前' % (last['name'], time.time() - last['time'])) if last['time'] else '本次还没提醒过'
    return ('界面没有反应（约 %d 秒了；窗口%s、%s；系统%s判定为"未响应"）。%s；%s\n  各线程停在：\n%s\n  本程序的窗口：%s' % (
        secs, '最小化着' if _user32.IsIconic(hwnd) else '没有最小化',
        '在最前面' if _user32.GetForegroundWindow() == hwnd else '不在最前面',
        '已' if _user32.IsHungAppWindow(hwnd) else '还没',
        context(), ago, thread_stacks(), windows_text()))


def _loop(title, log, context):
    hung_at, reported = None, False
    dis_at, dis_reported = None, False
    while True:
        time.sleep(CHECK_EVERY)
        try:
            hwnd = notify.main_window(title)
            if not hwnd:
                continue
            now = time.time()
            if responds(hwnd):
                if reported:
                    log('界面恢复了反应，前后卡了约 %d 秒' % (now - hung_at))
                hung_at, reported = None, False
                if _user32.IsWindowEnabled(hwnd):
                    dis_at, dis_reported = None, False
                else:
                    dis_at = dis_at or now
                    if not dis_reported and now - dis_at >= DISABLED_REPORT:
                        dis_reported = True
                        log('主窗口被对话框挡着已经 %d 秒了（对话框可能藏在别的窗口后面）。%s；本程序的窗口：%s' % (
                            now - dis_at, context(), windows_text()))
                continue
            hung_at = hung_at or now - CHECK_EVERY
            if not reported and now - hung_at >= REPORT_AFTER:
                reported = True
                log(_report(hwnd, now - hung_at, context))
        except Exception:
            log('看门狗出错：' + traceback.format_exc())


def start(title, log_dir, context=lambda: ''):
    """context：返回当时打印状态的一句话。"""
    threading.Thread(target=_loop, args=(title, _Log(log_dir), context), daemon=True, name='看门狗').start()


# ---------------- 程序启动、关闭 ----------------
def _flag(log_dir):
    return os.path.join(log_dir, '运行中.txt')


def _python_alive(pid):
    """这个进程号还活着、而且是 python（进程号会被别的程序重用）。"""
    h = _kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
            return False
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return 'python' in os.path.basename(buf.value).lower()
    finally:
        _kernel32.CloseHandle(h)


def started(log_dir):
    log = _Log(log_dir)
    note = ''
    try:
        flag = _flag(log_dir)
        if os.path.exists(flag):
            with open(flag, encoding='utf-8') as f:
                pid, since = (f.read().split('|') + [''])[:2]
            if pid.isdigit() and _python_alive(int(pid)) and int(pid) != os.getpid():
                note = '（%s 打开的另一个窗口还开着）' % since
            else:
                log('上次（%s 打开的）没有正常关闭：多半是卡住后在任务管理器里结束的，或者崩溃了' % since)
        with open(flag, 'w', encoding='utf-8') as f:
            f.write('%d|%s' % (os.getpid(), datetime.datetime.now().strftime('%m-%d %H:%M:%S')))
    except Exception:
        pass
    log('程序启动' + note)


def stopped(log_dir):
    _Log(log_dir)('程序关闭')
    try:
        flag = _flag(log_dir)
        with open(flag, encoding='utf-8') as f:
            mine = f.read().startswith('%d|' % os.getpid())
        if mine:
            os.remove(flag)
    except Exception:
        pass
