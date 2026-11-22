"""Test doubles: a scriptable HTTP server and fake agent CLIs."""

from __future__ import annotations

import json
import stat
import sys
import textwrap
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# A route returns (status, json_body) or (status, json_body, headers).
Route = Callable[[dict], tuple]


@dataclass
class Request:
    method: str
    path: str
    body: dict


@dataclass
class FakeServer:
    """In-process HTTP server. Register routes as {"POST /path": handler}."""

    routes: dict[str, Route] = field(default_factory=dict)
    requests: list[Request] = field(default_factory=list)
    url: str = ""

    def start(self) -> FakeServer:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence test output
                pass

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else {}
                server.requests.append(Request(method, self.path, body))
                route = server.routes.get(f"{method} {self.path}")
                if route is None:
                    status, payload, headers = 404, {"error": {"message": "not found"}}, {}
                else:
                    result = route(body)
                    status, payload = result[0], result[1]
                    headers = result[2] if len(result) > 2 else {}
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def bodies(self, path: str) -> list[dict]:
        return [r.body for r in self.requests if r.path == path]


def make_agent(bin_dir: Path, name: str, stdout: str = "", exit_code: int = 0, stderr: str = "", sleep: float = 0) -> Path:
    """Write an executable fake agent CLI.

    It records its argv, stdin and working directory to <name>.calls.jsonl next to itself,
    then prints `stdout` and exits with `exit_code`.
    """
    script = bin_dir / name
    log = bin_dir / f"{name}.calls.jsonl"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""
            import json, os, sys, time
            stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
            with open({str(log)!r}, "a") as fh:
                fh.write(json.dumps({{"argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd()}}) + "\\n")
            time.sleep({sleep!r})
            sys.stdout.write({stdout!r})
            sys.stderr.write({stderr!r})
            sys.exit({exit_code!r})
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def agent_calls(bin_dir: Path, name: str) -> list[dict]:
    log = bin_dir / f"{name}.calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def claude_json(text: str, model: str = "claude-haiku-5-5", is_error: bool = False, errors: list[str] | None = None) -> str:
    """Output shaped like `claude -p --output-format json` (also Qoder and Grok)."""
    payload = {
        "type": "result",
        "is_error": is_error,
        "result": text,
        "total_cost_usd": 0.0003,
        "usage": {"input_tokens": 2, "output_tokens": 9},
        "modelUsage": {model: {"inputTokens": 1200, "outputTokens": 30, "cacheReadInputTokens": 600, "cacheCreationInputTokens": 900}},
    }
    if errors:
        payload["errors"] = errors
    return json.dumps(payload)
