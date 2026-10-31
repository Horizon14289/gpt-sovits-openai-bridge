#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线冒烟测试：不需要真实的 GPT-SoVITS，用假后端验证协议与并发保护。

用法：python tests/smoke_test.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(ROOT, "bridge.py")

# 一段极小的合法 WAV 头（44 字节 + 少量静音），够验证字节透传
FAKE_WAV = (
    b"RIFF" + (36 + 1600).to_bytes(4, "little") + b"WAVEfmt " +
    (16).to_bytes(4, "little") + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") +
    (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little") +
    (2).to_bytes(2, "little") + (16).to_bytes(2, "little") +
    b"data" + (1600).to_bytes(4, "little") + b"\x00" * 1600
)

state = {"calls": 0, "max_parallel": 0, "now": 0, "lock": threading.Lock()}


class FakeBackend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        with state["lock"]:
            state["calls"] += 1
            state["now"] += 1
            state["max_parallel"] = max(state["max_parallel"], state["now"])
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if not body.get("text"):
                self.send_response(400)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            time.sleep(0.3)  # 模拟推理耗时，好观察是否串行
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(FAKE_WAV)))
            self.end_headers()
            self.wfile.write(FAKE_WAV)
        finally:
            with state["lock"]:
                state["now"] -= 1


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_port(port: int, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def post(url: str, payload: dict, timeout: float = 30) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"  ({detail})" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    backend_port = free_port()
    backend = ThreadingHTTPServer(("127.0.0.1", backend_port), FakeBackend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()

    bridge_port = free_port()
    cfg = {
        "port": bridge_port,
        "backend": f"http://127.0.0.1:{backend_port}/tts",
        "ref_audio_path": "ref.wav",
        "prompt_text": "参考文本",
        "max_chars": 50,
        "retry": 1,
    }
    cfg_path = os.path.join(ROOT, "_smoke_config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)

    proc = subprocess.Popen([sys.executable, BRIDGE, "-c", cfg_path],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not wait_port(bridge_port):
            print("  FAIL  桥没有启动\n" + (proc.stdout.read() if proc.stdout else ""))
            return 1

        base = f"http://127.0.0.1:{bridge_port}"
        print("桥已启动，开始测试")

        with urllib.request.urlopen(base + "/health", timeout=10) as r:
            health = json.loads(r.read().decode())
        check("health.ok", health.get("ok") is True)
        check("health.backend_reachable", health.get("backend_reachable") is True)

        with urllib.request.urlopen(base + "/v1/models", timeout=10) as r:
            models = json.loads(r.read().decode())
        check("models 列表", models.get("data", [{}])[0].get("id") == "tts-1")

        code, data = post(base + "/v1/audio/speech", {"input": "你好"})
        check("合成 200", code == 200, str(code))
        check("返回 WAV", data[:4] == b"RIFF", data[:4].decode("latin1"))

        code, _ = post(base + "/v1/audio/speech", {"input": ""})
        check("空文本 400", code == 400, str(code))

        code, body = post(base + "/v1/audio/speech", {"input": "字" * 80})
        check("超长 413", code == 413, str(code))
        check("超长有提示", "max_chars" in body.decode("utf-8", "ignore"))

        code, _ = post(base + "/v1/audio/speech", {"input": "Hello world only"})
        check("全英文被清洗后 400", code == 400, str(code))

        # 并发：3 个请求同时打，后端同时处理数必须为 1（说明被串行化了）
        state["max_parallel"] = 0
        results: list[int] = []

        def worker():
            c, _ = post(base + "/v1/audio/speech", {"input": "并发测试"})
            results.append(c)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        check("并发 3 个都成功", results == [200, 200, 200], str(results))
        check("后端串行执行", state["max_parallel"] == 1, f"max_parallel={state['max_parallel']}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        backend.shutdown()
        if os.path.exists(cfg_path):
            os.remove(cfg_path)

    print()
    if failures:
        print(f"FAILED: {len(failures)} 项 -> {', '.join(failures)}")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
