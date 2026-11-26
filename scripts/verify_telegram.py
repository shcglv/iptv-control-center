"""Telegram drill: the bot must poll, authorise and answer in a running process.

The bot is unit-tested against a fake client. This runs the real application against a local HTTP
stub of the Bot API, so the wiring — start-up, long polling, allow-list, command dispatch, reply —
is exercised without a token or a network dependency.

    .\\.venv\\Scripts\\python.exe scripts\\verify_telegram.py
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_PORT = 18098
STUB_PORT = 18099
OWNER_ID = 4242
DATABASE = ROOT / "data" / "telegram-drill.db"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPLIES: list[dict[str, object]] = []
UPDATES: list[list[dict[str, object]]] = []


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            payload = {}
        method = self.path.rsplit("/", 1)[-1]
        if method == "getUpdates":
            result = UPDATES.pop(0) if UPDATES else []
            self._reply({"ok": True, "result": result})
        elif method == "sendMessage":
            REPLIES.append(payload)
            self._reply({"ok": True, "result": {"message_id": len(REPLIES)}})
        elif method == "getMe":
            self._reply({"ok": True, "result": {"id": 1, "is_bot": True, "username": "drill_bot"}})
        else:
            self._reply({"ok": True, "result": True})

    def _reply(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        return


def main() -> int:
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    DATABASE.unlink(missing_ok=True)
    UPDATES.append(
        [
            {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "date": int(time.time()),
                    "text": "/status",
                    "chat": {"id": OWNER_ID, "type": "private"},
                    "from": {"id": OWNER_ID, "is_bot": False, "first_name": "Drill"},
                },
            }
        ]
    )

    server_thread = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), Stub)
    threading.Thread(target=server_thread.serve_forever, daemon=True).start()

    env = {
        **os.environ,
        "IPTV_DATABASE_URL": f"sqlite+aiosqlite:///{DATABASE.as_posix()}",
        "IPTV_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
        "IPTV_APP_ENV": "test",
        "IPTV_HOST": "127.0.0.1",
        "IPTV_PORT": str(APP_PORT),
        "IPTV_PUBLIC_BASE_URL": f"http://127.0.0.1:{APP_PORT}",
        "IPTV_SCHEDULER_ENABLED": "false",
        "IPTV_TELEGRAM_ENABLED": "true",
        "IPTV_TELEGRAM_BOT_TOKEN": "1:drill",
        "IPTV_TELEGRAM_OWNER_ID": str(OWNER_ID),
        "IPTV_TELEGRAM_API_BASE": f"http://127.0.0.1:{STUB_PORT}",
        "IPTV_TELEGRAM_POLL_TIMEOUT": "1",
        "IPTV_TELEGRAM_NOTIFY_INTERVAL_SECONDS": "3600",
    }

    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if migrated.returncode != 0:
        print(migrated.stderr[-1200:])
        return 2

    log_path = ROOT / "data" / "telegram-drill.log"
    app = subprocess.Popen(
        [sys.executable, "-m", "iptvcc"],
        cwd=ROOT,
        env=env,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(120):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/healthz", timeout=2) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.5)

        deadline = time.time() + 30
        while time.time() < deadline and not REPLIES:
            time.sleep(0.5)

        if not REPLIES:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            print(log_text[-1200:])
            print("RESULT: FAILED - the bot never answered the /status update")
            return 3

        reply = REPLIES[0]
        chat_id = reply.get("chat_id")
        text = str(reply.get("text", ""))
        print(f"reply to chat {chat_id}, {len(text)} characters")
        print("  " + text[:120].replace("\n", " | "))

        if chat_id != OWNER_ID:
            print("RESULT: FAILED - the reply went to the wrong chat")
            return 4
        if not text:
            print("RESULT: FAILED - the reply was empty")
            return 5
        print("RESULT: TELEGRAM DRILL PASSED (polled, authorised, answered)")
        return 0
    finally:
        app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()
        server_thread.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
