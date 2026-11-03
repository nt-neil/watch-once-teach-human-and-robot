#!/usr/bin/env python3
"""Serve only the local todo demo: python3 serve.py --port 18765."""

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import sys


CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'none'; font-src 'none'; img-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'; object-src 'none'"
)


class DemoHandler(BaseHTTPRequestHandler):
    page = b""

    def end_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        self.respond(send_body=True)

    def do_HEAD(self):
        self.respond(send_body=False)

    def respond(self, send_body):
        request_target = self.requestline.split()[1]
        allowed = self.path == "/todo-demo.html" and request_target == "/todo-demo.html"
        content = self.page if allowed else b"Not found\n"
        self.send_response(200 if allowed else 404)
        self.send_header(
            "Content-Type", "text/html; charset=utf-8" if allowed else "text/plain; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if send_body:
            self.wfile.write(content)


def main():
    parser = argparse.ArgumentParser(description="Local-only todo demo server")
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    script = Path(__file__).resolve()
    DemoHandler.page = script.with_name("todo-demo.html").read_bytes()
    host = "127.0.0.1"
    with HTTPServer((host, args.port), DemoHandler) as server:
        port = server.server_address[1]
        url = f"http://{host}:{port}/todo-demo.html"
        state = {
            "pid": os.getpid(),
            "port": port,
            "host": host,
            "url": url,
            "command": [str(Path(sys.executable).resolve()), str(script), *sys.argv[1:]],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        script.with_name("server-state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(url, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
