#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI-compatible TTS bridge for GPT-SoVITS.

把 OpenAI 的 /v1/audio/speech 请求翻译成 GPT-SoVITS api_v2 的 /tts，
让 QQ 机器人、ChatGPT 兼容客户端、各类朗读插件都能用本地音色说话。

零依赖：只用 Python 标准库。GPT-SoVITS 服务需要另行启动。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VERSION = "0.2.0"

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 9881,
    # GPT-SoVITS api_v2 地址
    "backend": "http://127.0.0.1:9880/tts",
    # 参考音频：相对 GPT-SoVITS 的工作目录，或绝对路径
    "ref_audio_path": "output/slicer_opt/ref.wav",
    # 参考音频对应的文字（必须逐字一致）
    "prompt_text": "",
    "prompt_lang": "zh",
    "text_lang": "zh",
    "text_split_method": "cut5",
    "speed": 1.0,
    "timeout": 600,
    # 单次请求最大字数，超出直接拒绝（避免调用方干等几分钟）
    "max_chars": 1000,
    # 后端返回 5xx / 超时时的重试次数
    "retry": 1,
    # 去掉英文单词：多数中文微调音色缺英文音素，遇到英文会报错或念成乱码
    "strip_ascii": True,
    # 返回 mp3 而不是 wav（需要 ffmpeg）
    "mp3": False,
    "ffmpeg": "ffmpeg",
}

# GPT-SoVITS 的推理不是并发安全的：多个请求同时打到 GPU 会串音或爆显存。
# 串行化是所有并发保护里最简单也最可靠的一种。
_BACKEND_LOCK = threading.Lock()


# ---------------------------------------------------------------- 配置加载
def load_config(path: str | None) -> dict:
    cfg = dict(DEFAULTS)
    if path:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
    elif os.path.exists("config.json"):
        with open("config.json", encoding="utf-8") as f:
            user = json.load(f)
    else:
        return cfg

    unknown = set(user) - set(DEFAULTS)
    if unknown:
        log(f"[warn] 未知配置项将被忽略: {', '.join(sorted(unknown))}")
    cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
    return cfg


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 文本处理
def sanitize(text: str, strip_ascii: bool) -> str:
    """去掉会让中文音色翻车的字符。

    GPT-SoVITS 的中文微调模型缺英文音素（需要 NLTK cmudict），
    混入英文时会整句失败或念成乱码，所以默认剥离。
    """
    if strip_ascii:
        text = re.sub(r"[A-Za-z]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def backend_port(cfg: dict) -> tuple[str, int]:
    u = urlparse(cfg["backend"])
    return (u.hostname or "127.0.0.1", u.port or 80)


def backend_alive(cfg: dict, timeout: float = 1.5) -> bool:
    """只探测 TCP 端口，不去猜后端的 HTTP 路径。"""
    host, port = backend_port(cfg)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- 后端调用
def _call_backend(cfg: dict, text: str, speed: float | None) -> bytes:
    body = {
        "text": text,
        "text_lang": cfg["text_lang"],
        "ref_audio_path": cfg["ref_audio_path"],
        "prompt_text": cfg["prompt_text"],
        "prompt_lang": cfg["prompt_lang"],
        "text_split_method": cfg["text_split_method"],
        "batch_size": 1,
        "speed_factor": max(0.5, min(2.0, float(speed or cfg["speed"]))),
        "media_type": "wav",
        "streaming_mode": False,
    }
    req = urllib.request.Request(
        cfg["backend"],
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
        return resp.read()


def synthesize(cfg: dict, text: str, speed: float | None) -> bytes:
    """串行调用后端；5xx 与超时按配置重试。"""
    attempts = max(0, int(cfg["retry"])) + 1
    last: Exception | None = None
    for i in range(attempts):
        try:
            with _BACKEND_LOCK:
                return _call_backend(cfg, text, speed)
        except urllib.error.HTTPError as e:
            if e.code < 500 or i == attempts - 1:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            if i == attempts - 1:
                raise
            last = e
        log(f"[retry] 第 {i + 1} 次失败，{0.5 * (i + 1):.1f}s 后重试: {last}")
        time.sleep(0.5 * (i + 1))
    raise last if last else RuntimeError("unreachable")


def to_mp3(cfg: dict, wav: bytes) -> bytes:
    ffmpeg = shutil.which(cfg["ffmpeg"])
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg，无法转 mp3（可在配置里设 ffmpeg 的绝对路径）")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "a.wav")
        dst = os.path.join(tmp, "a.mp3")
        with open(src, "wb") as f:
            f.write(wav)
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", src, "-b:a", "128k", dst],
                       check=True)
        with open(dst, "rb") as f:
            return f.read()


# ---------------------------------------------------------------- HTTP 层
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cfg: dict = DEFAULTS

    def log_message(self, fmt, *args):  # 覆盖默认的 stderr 输出
        log(f"{self.address_string()} {fmt % args}")

    def _send_json(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_audio(self, data: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send_json(200, {
                "ok": True,
                "version": VERSION,
                "backend": self.cfg["backend"],
                "backend_reachable": backend_alive(self.cfg),
            })
            return
        if self.path.startswith("/v1/models"):
            self._send_json(200, {"object": "list", "data": [
                {"id": "tts-1", "object": "model", "owned_by": "gpt-sovits"}]})
            return
        self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self.path.startswith("/v1/audio/speech"):
            self._send_json(404, {"error": {"message": "not found"}})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json(400, {"error": {"message": "invalid json"}})
            return

        text = (payload.get("input") or payload.get("text") or "").strip()
        if not text:
            self._send_json(400, {"error": {"message": "missing 'input'"}})
            return

        if len(text) > self.cfg["max_chars"]:
            self._send_json(413, {"error": {
                "message": f"文本 {len(text)} 字，超过上限 {self.cfg['max_chars']} 字；"
                           f"请自行分段，或调高配置里的 max_chars"}})
            return

        cleaned = sanitize(text, self.cfg["strip_ascii"])
        if not cleaned:
            self._send_json(400, {"error": {
                "message": "文本清洗后为空（可能全是英文单词，可关掉 strip_ascii 或给音色补英文音素）"}})
            return

        want_mp3 = (payload.get("response_format") or "").lower() == "mp3" or self.cfg["mp3"]
        t0 = time.time()
        try:
            audio = synthesize(self.cfg, cleaned, payload.get("speed"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            log(f"[error] 后端 {e.code}: {detail}")
            self._send_json(502, {"error": {"message": f"GPT-SoVITS 返回 {e.code}: {detail}"}})
            return
        except Exception as e:
            log(f"[error] {e}")
            self._send_json(502, {"error": {"message": f"无法连接 GPT-SoVITS: {e}"}})
            return

        ctype, out = "audio/wav", audio
        if want_mp3:
            try:
                out, ctype = to_mp3(self.cfg, audio), "audio/mpeg"
            except Exception as e:
                log(f"[warn] mp3 转换失败，回退 wav: {e}")

        log(f"[ok] {len(cleaned)} 字 → {len(out)/1024:.0f} KB / {time.time()-t0:.1f}s")
        self._send_audio(out, ctype)


# ---------------------------------------------------------------- 入口
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OpenAI-compatible TTS bridge for GPT-SoVITS")
    ap.add_argument("-c", "--config", help="配置文件路径（默认读取当前目录 config.json）")
    ap.add_argument("--host", help="监听地址，默认 127.0.0.1（只允许本机）")
    ap.add_argument("--port", type=int, help="监听端口，默认 9881")
    ap.add_argument("--backend", help="GPT-SoVITS api_v2 地址，默认 http://127.0.0.1:9880/tts")
    ap.add_argument("--ref-audio", help="参考音频路径")
    ap.add_argument("--prompt-text", help="参考音频对应文字")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    for key, val in (("host", args.host), ("port", args.port), ("backend", args.backend),
                     ("ref_audio_path", args.ref_audio), ("prompt_text", args.prompt_text)):
        if val is not None:
            cfg[key] = val

    if not cfg["prompt_text"]:
        log("[warn] 未设置 prompt_text：音色可能不准，建议填参考音频的逐字文本")

    Handler.cfg = cfg
    log(f"gpt-sovits-openai-bridge {VERSION}")
    log(f"  监听   http://{cfg['host']}:{cfg['port']}/v1")
    log(f"  后端   {cfg['backend']}（{'可达' if backend_alive(cfg) else '当前不可达'}）")
    log(f"  参考   {cfg['ref_audio_path']}")
    log(f"  上限   {cfg['max_chars']} 字/次，并发串行")
    server = ThreadingHTTPServer((cfg["host"], cfg["port"]), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
