"""Render entrypoint: Keeps HTTP connection active during scan so Render never freezes."""
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scheduler import SCAN_LOCK, scan_once, check_and_send_hourly, check_and_send_daily

# Initialize to current time so initial Render startup health check returns instantly (<10ms)
_last_scan_time = time.time()


def run_full_scan_sync():
    """Run full scan while keeping HTTP connection active."""
    global _last_scan_time
    with SCAN_LOCK:
        print("[HTTP REQUEST] Running market scan cycle synchronously to keep container awake...", flush=True)
        t0 = time.time()
        scan_once(block=True)
        _last_scan_time = time.time()
        
        now = datetime.now(timezone.utc)
        check_and_send_hourly(now)
        check_and_send_daily(now)
        
        duration = round(time.time() - t0, 2)
        print(f"[HTTP REQUEST] Scan and alerts completed in {duration}s!", flush=True)
        return duration


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _last_scan_time
        path = self.path.split("?")[0].rstrip("/")

        # Fast health check for Render internal port checks
        if path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "healthy", "service": "Rakibs Trading Bot"}')
            return

        # Diagnostics endpoint
        if path == "/status":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            import json
            diag = {
                "status": "online",
                "last_scan_time": _last_scan_time,
                "seconds_since_last_scan": round(time.time() - _last_scan_time, 1) if _last_scan_time else None,
                "utc_time": datetime.now(timezone.utc).isoformat(),
            }
            self.wfile.write(json.dumps(diag).encode("utf-8"))
            return

        # Trigger scan immediately in background without blocking HTTP connection
        if path in ("/scan", "/trigger"):
            if not SCAN_LOCK.locked():
                threading.Thread(target=scan_once, daemon=True).start()
                msg = "Scan started in background"
            else:
                msg = "Scan already in progress"
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            import json
            self.wfile.write(json.dumps({"status": "ok", "message": msg}).encode("utf-8"))
            return

        # Regular fast health check for cron-job.org and keep-alive probes
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "healthy", "service": "Rakibs Trading Bot"}')

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
