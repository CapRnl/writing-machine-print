"""开发测试用：在普通浏览器里跑同一个界面（不弹 pywebview 窗口）。
python devserver.py  →  http://127.0.0.1:8765/
界面里的 pywebview.api 调用经 /api/<方法名> 转给同一个 Api 对象。"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bottle import Bottle, request, response, static_file, run  # pywebview 自带 bottle
from hwprint.app import Api

api = Api()


class FakeWindow:
    def create_file_dialog(self, dialog_type=10, *a, **k):
        if dialog_type == 20:                       # 选文件夹
            p = os.environ.get('DEV_PICK_FOLDER')
            return (p,) if p else None
        p = os.environ.get('DEV_PICK_FILE')
        return tuple(x for x in p.split('|') if x) if p else None

    def evaluate_js(self, s):
        pass


api._window = FakeWindow()
app = Bottle()
SHIM = """<script>
window.pywebview={api:new Proxy({},{get:(t,name)=>(...args)=>fetch('/api/'+name,{method:'POST',
 headers:{'Content-Type':'application/json'},body:JSON.stringify(args)}).then(r=>r.json())})};
window.addEventListener('DOMContentLoaded',()=>setTimeout(()=>window.dispatchEvent(new Event('pywebviewready')),50));
window.addEventListener('pywebviewready',()=>{const q=new URLSearchParams(location.search);
 if(q.has('autoload')) setTimeout(async()=>{await (q.has('folder')?chooseFolder():chooseFiles()); if(q.has('page')) await gotoPage(parseInt(q.get('page')));
   if(q.has('logs')) toggleLogs(); if(q.has('style')) openStyle();},300);});
</script>"""


@app.route('/')
def index():
    html = open(os.path.join(HERE, 'hwprint', 'web', 'index.html'), encoding='utf-8').read()
    return html.replace('<head>', '<head>' + SHIM, 1)


@app.post('/api/<name>')
def call(name):
    args = request.json or []
    r = getattr(api, name)(*args)
    response.content_type = 'application/json'
    return json.dumps(r, ensure_ascii=False)


@app.route('/load')
def load():
    r = api.add_files([request.query.getunicode('path')])
    response.content_type = 'application/json'
    return json.dumps(r, ensure_ascii=False)


@app.route('/folder')
def folder():
    r = api.add_folder(request.query.getunicode('path'))
    response.content_type = 'application/json'
    return json.dumps(r, ensure_ascii=False)


if __name__ == '__main__':
    import socket
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    class Threaded(ThreadingMixIn, WSGIServer):
        daemon_threads = True
        address_family = socket.AF_INET6

        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)   # 同时接 IPv4/IPv6
            super().server_bind()

    make_server('::', int(os.environ.get('DEV_PORT', 8765)), app, server_class=Threaded,
                handler_class=Quiet).serve_forever()
