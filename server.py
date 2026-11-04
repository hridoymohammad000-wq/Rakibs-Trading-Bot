"""Render entrypoint: Fast HTTP binding with background scheduler and non-blocking scan trigger."""
import os
import sys
import threading
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

_last_scan_time = 0
_scan_lock = threading.Lock()


def trigger_background_scan():
    def _run():
        global _last_scan_time
        if not _scan_lock.acquire(blocking=False):
            print("[SCAN TRIGGER] Scan already in progress, skipping duplicate trigger", flush=True)
            return
        try:
            print("[SCAN TRIGGER] Starting market scan cycle...", flush=True)
            from scheduler import scan_once, check_and_send_hourly, check_and_send_daily
            from datetime import datetime, timezone
            
            scan_once()
            _last_scan_time = time.time()
            
            # Check hourly and daily digests
            now = datetime.now(timezone.utc)
            check_and_send_hourly(now)
            check_and_send_daily(now)
            print("[SCAN TRIGGER] Market scan completed and alerts evaluated!", flush=True)
        except Exception as e:
            print(f"[SCAN TRIGGER ERROR]: {e}", flush=True)
            traceback.print_exc()
        finally:
            _scan_lock.release()

    threading.Thread(target=_run, daemon=True).start()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _last_scan_time
        path = self.path.split("?")[0].rstrip("/")

        # Trigger scan on /scan, /trigger, OR on root "/" if > 4 minutes have elapsed
        scan_started = False
        if path in ("/scan", "/trigger") or (time.time() - _last_scan_time > 240):
            trigger_background_scan()
            scan_started = True

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        res = b'{"status": "healthy", "service": "Rakibs Trading Bot", "scan_triggered": true}' if scan_started else b'{"status": "healthy", "service": "Rakibs Trading Bot"}'
        self.wfile.write(res)

    def log_message(self, format, *args):
        pass


def run_loop():
    try:
        from scheduler import run_scheduler
        run_scheduler()
    except Exception as e:
        print(f"[FATAL SCHEDULER]: {e}", flush=True)
        traceback.print_exc()


def start_server():
    port = int(os.environ.get("PORT", 10000))
    print(f"Health server starting on 0.0.0.0:{port}...", flush=True)
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server listening on 0.0.0.0:{port} successfully!", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=run_loop, daemon=True).start()
    start_server()
