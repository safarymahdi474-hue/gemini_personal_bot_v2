import json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

class State:
    delay = 0.3
    hang = False
    connections = 0
    requests = 0

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive capable, like Google's servers
    def setup(self):
        super().setup(); State.connections += 1
    def log_message(self, *a): pass
    def do_GET(self):
        State.requests += 1
        if State.hang:
            time.sleep(60); return
        time.sleep(State.delay)
        q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
        body = json.dumps([[[f"T({len(q)})", q, None, None, 1]], None, "fa"]).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

def start():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"
