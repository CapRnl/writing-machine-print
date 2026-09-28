"""写字机打印 —— 双击运行（用本目录 .venv 里的 pythonw）。"""

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)


def main():
    import webview
    from webview.dom import DOMEventHandler
    from hwprint.app import Api, TITLE, LOG_DIR
    from hwprint import notify, watchdog

    api = Api()
    hidden = os.environ.get('HWPRINT_SMOKE') == '1'
    if not hidden:
        watchdog.started(LOG_DIR)      # 记"程序启动"；上次没正常关闭的补记一句
    win = webview.create_window(TITLE, 'hwprint/web/index.html', js_api=api,
                                width=1320, height=880, min_size=(1060, 700), background_color='#f4f2ec',
                                text_select=True, hidden=hidden)
    api._window = win

    def on_drop(e):
        try:
            files = e.get('dataTransfer', {}).get('files', [])
            paths = [f.get('pywebviewFullPath') for f in files if f.get('pywebviewFullPath')]
            if not paths:
                return
            folders = [p for p in paths if os.path.isdir(p)]
            docs = [p for p in paths if os.path.isfile(p)]
            r = None
            for fd in folders:
                r = api.add_folder(fd)
            if docs:
                r = api.add_files(docs)
            if r:
                win.evaluate_js('added(%s)' % json.dumps(r, ensure_ascii=False))
        except Exception:
            api.log('拖入文件出错：' + traceback.format_exc())

    def bind(window):
        # 界面卡住时自动把当时的情况记进日志（09-28 两次写完提示后界面没反应，原因没查实）
        watchdog.start(TITLE, LOG_DIR, lambda: '打印状态 %s：%s' % (api._job.get('state'), api._job.get('msg') or ''))
        noop = lambda e: None
        window.dom.document.events.dragenter += DOMEventHandler(noop, True, True)
        window.dom.document.events.dragover += DOMEventHandler(noop, True, True, debounce=300)
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        if hidden:
            # 冒烟测试：界面脚本和后台接口都通了就把结果写出来并退出
            import time
            time.sleep(3)
            res = window.evaluate_js("JSON.stringify({ready: !!(window.pywebview && window.pywebview.api),"
                                     " ports: document.getElementById('port').options.length,"
                                     " title: document.title,"
                                     " buttons: [...document.querySelectorAll('.bottom button')].map(b=>b.textContent.trim())})")
            found = bool(notify.main_window(TITLE))     # 任务栏闪烁、看门狗都靠它找窗口
            with open(os.path.join(HERE, '冒烟测试结果.txt'), 'w', encoding='utf-8') as f:
                f.write('%s  找得到窗口：%s' % (res, found))
            window.destroy()

    def on_closing():
        try:
            if api._job.get('state') in ('printing', 'paused', 'turn'):
                api.stop()
            api._grbl.close()
        except Exception:
            pass

    win.events.closing += on_closing
    webview.start(bind, win, private_mode=False, storage_path=os.path.join(HERE, '.webview'))
    if not hidden:
        watchdog.stopped(LOG_DIR)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        with open(os.path.join(HERE, '启动出错.txt'), 'w', encoding='utf-8') as f:
            f.write(traceback.format_exc())
        raise
