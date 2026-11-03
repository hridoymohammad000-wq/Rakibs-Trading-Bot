"""Render entrypoint: Runs a lightweight HTTP health check while running scheduler in background."""
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from scheduler import run_scheduler


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "running", "service": "Rakibs Trading Bot"}')

    def log_message(self, format, *args):
        pass


def start_server():
    port = int(os.environ.get("PORT", 10000))
    print(f"Health check HTTP server listening on port {port}...")
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


if __name__ == "__main__":
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    start_server()
