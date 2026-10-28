"""Serve the built React SPA and same-origin API proxy using only stdlib.

The upstream is deployment configuration, never a URL supplied by the client.
No credentials or request bodies are written to logs.
"""
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

ROOT = Path(os.getenv("FRONTEND_DIST", "frontend/dist")).resolve()
UPSTREAM = os.getenv("FRONTEND_API", "http://127.0.0.1:18100").rstrip("/")
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


HTTP = build_opener(ProxyHandler({}), NoRedirect())


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.proxy()
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if not (ROOT / self.path.partition("?")[0].lstrip("/")).is_file():
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self.proxy()
        self.send_error(404)

    def proxy(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if size < 0 or size > 1024 * 1024:
            self.send_error(413)
            return
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() in {"authorization", "content-type", "accept"}}
        req = Request(UPSTREAM + self.path[4:], data=self.rfile.read(size) if size else None,
                      headers=headers, method=self.command)
        try:
            response = HTTP.open(req, timeout=150)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError):
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail":{"code":"UPSTREAM_UNAVAILABLE","message":"API unavailable; read saved state before retrying."}}')
            return
        with response:
            body = response.read()
            self.send_response(response.status)
            for key in ("Content-Type", "X-Request-ID"):
                if response.headers.get(key):
                    self.send_header(key, response.headers[key])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))),
                        partial(Handler, directory=str(ROOT))).serve_forever()
