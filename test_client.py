# -*- coding: utf-8 -*-
"""最小测试客户端：合成一句并保存为 wav。"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:9881"
text = sys.argv[1] if len(sys.argv) > 1 else "你好，这是 gpt-sovits-openai-bridge 的测试。"
out = sys.argv[2] if len(sys.argv) > 2 else "test.wav"

body = {"model": "tts-1", "input": text, "response_format": "wav"}
req = urllib.request.Request(
    f"{BASE}/v1/audio/speech",
    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=600) as resp:
    data = resp.read()
with open(out, "wb") as f:
    f.write(data)
print(f"OK {len(data)/1024:.0f} KB -> {out}")
