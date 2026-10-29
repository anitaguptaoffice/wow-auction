"""Local, read-only progress page. No game control or file upload endpoints."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

PAGE = r"""<!doctype html><meta charset="utf-8"><title>拍卖快照接收器</title>
<style>body{background:#101820;color:#eef4f8;font:18px system-ui;max-width:850px;margin:70px auto;padding:24px}h1{font-size:28px}section{padding:24px;background:#1b2835;border-radius:12px;margin:20px 0}progress{width:100%;height:28px;accent-color:#42c7a0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px system-ui;color:#b5c7d6}small{color:#b5c7d6}</style>
<h1>拍卖快照 · 只读内存接收器</h1><small>游戏中扫描完成后，点击“导出到电脑”。无需小退。</small>
<section><div id="connection">正在连接…</div></section>
<section><h2 id="title">最近一份导出</h2><progress id="progress" max="1" value="0"></progress>
<p id="count">等待数据</p><pre id="details"></pre></section>
<small>这里的进度表示电脑已校验接收的数据块。游戏面板显示的是发布进度。传输校验通过后，拍卖数据本身仍须通过入库检查。</small>
<script>
async function refresh(){try{
const r=await fetch('/api/status',{cache:'no-store'});const d=await r.json();
document.getElementById('connection').textContent=d.connection?.message||'等待接收器';
const s=d.snapshot;if(s){
document.getElementById('title').textContent=s.transportComplete?'校验通过 · 已保存':'最近一份导出 · 接收中';
document.getElementById('progress').max=s.total||1;
document.getElementById('progress').value=s.received||0;
document.getElementById('count').textContent=(s.received||0)+' / '+s.total+' 块 · '+((s.bytes||0)/1048576).toFixed(2)+' MiB';
document.getElementById('details').textContent='会话：'+s.session+'\n'+(s.file?'文件：'+s.file+'\nSHA-256：'+s.sha256:'缺块：'+(s.missing||[]).slice(0,80).join(', ')+(s.missing?.length>80?' …':''));
}}catch(e){document.getElementById('connection').textContent='接收器未运行或已关闭';}}
refresh();setInterval(refresh,1000);
</script>"""


def start(root, port=8766):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                payload, content_type = PAGE.encode(), "text/html; charset=utf-8"
            elif self.path == "/api/status":
                result = {}
                for key, name in (("connection", "connection.json"), ("snapshot", "latest-status.json")):
                    try:
                        result[key] = json.loads((root / name).read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        result[key] = None
                payload, content_type = json.dumps(result, ensure_ascii=False).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
