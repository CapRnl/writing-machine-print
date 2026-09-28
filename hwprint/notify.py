"""写完一页、等人换纸时的提醒：播一段"叮咚 + 语音"，同时让任务栏上的程序按钮闪烁。

语音在 sounds 文件夹里，是用微软"云希"的声音预先录好的 WAV（生成办法见 sounds\说明.txt），
运行时不联网、不装东西。都在后台线程里做，不耽误写字；电脑静音时只剩任务栏闪烁。
"""

import ctypes
import os
import threading
import time
import winsound
from ctypes import wintypes

SOUND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds')
_lock = threading.Lock()          # 同一时间只播一段，后来的排队
last = {'name': '', 'time': 0.0}  # 最近一次提醒（界面卡住时记进日志，看是不是提醒之后卡的）

_user32 = ctypes.WinDLL('user32', use_last_error=True)   # 自己的一份，不改全局 windll.user32 的设置
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
# 取标题用 InternalGetWindowText：GetWindowText 对本进程的窗口是发消息去问，界面卡住时会跟着卡住
_user32.InternalGetWindowText.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.FlashWindowEx.argtypes = [ctypes.c_void_p]


class _FLASHWINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('hwnd', wintypes.HWND), ('dwFlags', wintypes.DWORD),
                ('uCount', wintypes.UINT), ('dwTimeout', wintypes.DWORD)]


def own_windows(title=None):
    """本程序自己的顶层窗口 [(hwnd, 标题, 是否可见)]；给了 title 就只要标题完全相同的。
    只按标题找会找错：资源管理器打开本程序的文件夹时，那个窗口的标题也是"写字机打印"。"""
    pid = os.getpid()
    out = []

    def cb(hwnd, _):
        p = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            _user32.InternalGetWindowText(hwnd, buf, 256)
            if title is None or buf.value == title:
                out.append((hwnd, buf.value, bool(_user32.IsWindowVisible(hwnd))))
        return True

    _user32.EnumWindows(_EnumProc(cb), 0)
    out.sort(key=lambda w: not w[2])       # 看得见的排前面
    return out


def main_window(title):
    w = own_windows(title)
    return w[0][0] if w else None


def say(name, log=None):
    """播 sounds\\<name>.wav。"""
    path = os.path.join(SOUND_DIR, name + '.wav')
    last.update(name=name, time=time.time())

    def run():
        with _lock:
            try:
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
            except Exception as e:
                if log:
                    log('提示音没播出来（%s），改用系统提示音' % e)
                try:
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                except Exception:
                    pass

    threading.Thread(target=run, daemon=True, name='提示音').start()


def flash(title):
    """任务栏上的程序按钮闪烁，直到用户点开窗口。"""
    try:
        hwnd = main_window(title)
        if hwnd:
            info = _FLASHWINFO(ctypes.sizeof(_FLASHWINFO), hwnd, 3 | 12, 0, 0)   # FLASHW_ALL | FLASHW_TIMERNOFG
            _user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass
