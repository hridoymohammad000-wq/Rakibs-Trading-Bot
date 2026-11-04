"""Render entrypoint: Runs a lightweight HTTP health check while running scheduler in background."""
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ensure root directory is in sys.path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scheduler import scan_once, run_scheduler, check_and_send_hourly, check_and_send_daily

_last_scan_timestamp = 0
_scan_lock = threading.Lock()


def execute_scan_and_checks():
    """Execute scan and check hourly/daily boundaries safely."""
    global _last_scan_timestamp
    with _scan_lock:
        print("[HTTP TRIGGER] Executing market scan cycle...", flush=True)
        key = scan_once()
        _last_scan_timestamp = time.time()
        
        now = datetime.now(timezone.utc)
        if now.minute < 20:
            check_and_send_hourly(now, None)
        return key


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _last_scan_timestamp
        path = self.path.split("?")[0].rstrip("/")

        # /scan or /trigger manually forces an immediate scan + telegram notification
        if path in ("/scan", "/trigger"):
            try:
                execute_scan_and_checks()
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                resp = {
                    "status": "success",
                    "message": "15M Scan Cycle Completed and Telegram Alert Sent",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat()
                }
                self.wfile.write(json.dumps(resp).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                err_resp = {"status": "error", "message": str(e)}
                self.wfile.write(json.dumps(err_resp).encode("utf-8"))
                return

        # Default root endpoint "/" (Health check for cron-job.org and Render)
        # If cron-job.org pings "/", and it's been > 12 minutes since last scan, trigger scan automatically!
        auto_scanned = False
        if time.time() - _last_scan_timestamp > 720:  # 12 minutes
            try:
                threading.Thread(target=execute_scan_and_checks, daemon=True).start()
                auto_scanned = True
            except Exception as e:
                print(f"[AUTO SCAN TRIGGER ERROR]: {e}", flush=True)

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        res = {
            "status": "healthy",
            "service": "Rakibs Trading Bot",
            "auto_scan_triggered": auto_scanned,
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
        self.wfile.write(json.dumps(res).encode("utf-8"))

    def log_message(self, format, *args):
        pass


def start_scheduler_thread():
    try:
        print("Starting background scheduler loop thread...", flush=True)
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
