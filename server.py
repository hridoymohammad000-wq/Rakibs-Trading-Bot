"""Render entrypoint: Runs a lightweight HTTP health check while running scheduler in background."""
import os
import sys
import threading
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ensure root directory is in sys.path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "healthy", "service": "Rakibs Trading Bot"}')

    def log_message(self, format, *args):
        pass


def start_scheduler_thread():
    try:
        from scheduler import run_scheduler
        print("Starting background scheduler thread...", flush=True)
        run_scheduler()
    except Exception as e:
        print(f"[FATAL SCHEDULER ERROR]: {e}", flush=True)
        traceback.print_exc()


def start_server():
    port_str = os.environ.get("PORT", "10000")
    try:
        port = int(port_str)
    except ValueError:
        port = 10000

    print(f"Health check HTTP server starting on 0.0.0.0:{port}...", flush=True)
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        print(f"Health check server listening on 0.0.0.0:{port} successfully!", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"[FATAL SERVER ERROR]: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    print("=== INITIALIZING RAKIBS TRADING BOT (RENDER) ===", flush=True)
    t = threading.Thread(target=start_scheduler_thread, daemon=True)
    t.start()
    start_server()
