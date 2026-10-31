# gpt-sovits-openai-bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

把 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 的 `/tts` 接口包装成 **OpenAI 兼容**的 `/v1/audio/speech`，
让任何支持 OpenAI TTS 的客户端（QQ 机器人、聊天前端、朗读插件、ChatGPT 兼容应用）都能用**你自己的本地音色**说话。

**优点：零依赖、不出网、不需要 API Key、不需要显卡以外的任何东西。**

> 本地 GPT-SoVITS + 这个桥 + 你的音色模型 = 一个完全离线的、任意音色的 TTS 服务。

## 你需要先有什么

在装这个桥之前，请确认你已经有：

- **【必须】能跑起来的 GPT-SoVITS**——`api_v2.py` 已在 `127.0.0.1:9880` 正常服务（[下载地址](https://github.com/RVC-Boss/GPT-SoVITS)）
- **【必须】一段参考人声**——3~10 秒、干净、无背景音乐，加上**它说的那句话的逐字文字**
- **【可选】自己微调的音色模型**——`*.ckpt` + `*.pth`，没有也不影响使用

**不需要：训练模型、标注数据、显卡之外的额外硬件、任何 API Key。**

> 没有自己训的模型也能用：GPT-SoVITS 加载官方底模 + 你的参考音频即为**零样本推理**，效果取决于参考音频的干净程度。微调模型只是让音色更稳、更像。

---

## 它解决什么问题

GPT-SoVITS 自带 `api_v2.py`，接口长这样：

```
POST /tts   {"text": "...", "ref_audio_path": "...", "prompt_text": "..."}
```

而绝大多数客户端只会说 OpenAI 的话：

```
POST /v1/audio/speech   {"model": "tts-1", "input": "...", "voice": "alloy"}
```

两边对不上，于是每个项目都要自己写一遍适配代码。这个桥就是那一层，写一次，到处能用。

```
客户端 ──/v1/audio/speech──▶ bridge.py ──/tts──▶ GPT-SoVITS api_v2 ──▶ 你的音色
         (OpenAI 协议)              (翻译)          (本地推理)
```

## 快速开始

### 1. 先跑起 GPT-SoVITS

```bash
# 在 GPT-SoVITS 目录里
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

能看到 `TTS Config` 和模型加载完成即可。**部署踩坑记录见 [docs/GPTSOVITS-NOTES.md](docs/GPTSOVITS-NOTES.md)**。

### 2. 复制配置并填写

```bash
cp config.example.json config.json
```

最少改两项：

| 字段 | 说明 |
|---|---|
| `ref_audio_path` | 参考音频（音色样本），相对 GPT-SoVITS 工作目录或绝对路径 |
| `prompt_text` | **参考音频里说的原话，逐字一致**（含标点） |

**没有微调模型也没关系。** 不填 `weights` 时，GPT-SoVITS 会用官方底模做零样本推理——
只要参考音频够干净，音色就已经能用。想更稳再去微调。

### 3. 启动

```bash
python bridge.py
# 或指定配置
python bridge.py -c my-config.json
```

看到这样就是好了：

```
gpt-sovits-openai-bridge 0.1.0
  监听   http://127.0.0.1:9881/v1
  后端   http://127.0.0.1:9880/tts
```

### 4. 30 秒验证

先确认桥活着：

```bash
curl http://127.0.0.1:9881/health
# {"ok": true, "backend_reachable": true, ...}
```

再合成一句话（Windows 可用 `python test_client.py "能听到吗"`）：

```bash
curl -X POST http://127.0.0.1:9881/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"input":"能听到吗","response_format":"wav"}' -o test.wav
```

**听到声音 = 成功。** 第一次可能等十几秒（模型加载进显存），之后每句 1–3 秒。

Windows 用 `test_client.py` 更方便：

```bash
python test_client.py "你好，这是测试"
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/v1/audio/speech` | 合成语音，返回 `audio/wav`（或 `audio/mpeg`） |
| `GET` | `/v1/models` | 返回一个虚拟模型 `tts-1` |
| `GET` | `/health` | 桥自身状态 + 后端可达性 |

支持字段：`input` / `text`、`speed`、`response_format`（`wav` 或 `mp3`）、`voice`（忽略，音色由 GPT-SoVITS 决定）。

## 配置项

| 键 | 默认 | 说明 |
|---|---|---|
| `host` | `127.0.0.1` | 监听地址。默认只允许本机，不要随意改 0.0.0.0 |
| `port` | `9881` | 监听端口 |
| `backend` | `http://127.0.0.1:9880/tts` | GPT-SoVITS api_v2 地址 |
| `ref_audio_path` | — | 参考音频 |
| `prompt_text` | — | 参考音频的逐字文本 |
| `prompt_lang` | `zh` | 参考音频语言 |
| `text_lang` | `zh` | 要合成的文本语言 |
| `text_split_method` | `cut5` | 长句切分策略 |
| `speed` | `1.0` | 默认语速（客户端可覆盖，限制在 0.5–2.0） |
| `timeout` | `600` | 单次请求超时（秒）。CPU 推理或首次加载要留足 |
| `strip_ascii` | `true` | 剥离英文单词（见下文） |
| `mp3` | `false` | 默认返回 mp3 而非 wav |
| `ffmpeg` | `ffmpeg` | ffmpeg 路径，转 mp3 时才需要 |

## 关于 `strip_ascii`

**默认开启，因为它能避免一整类报错。**

中文微调音色通常缺英文音素数据（依赖 NLTK 的 `cmudict`），一旦文本里混入英文单词，GPT-SoVITS 会直接报错：

```
Resource 'cmudict' not found
```

开启后，`我用 ops bash 跑完了` 会变成 `我用 跑完了` 再送去合成。

如果你的音色支持英文，把它设为 `false`。

## 常见用途

### QQ 机器人（Cortico / Coopanion 系）

在 QQ 插件里选 `custom` / `local` 供应商，语音模型网址填：

```
http://127.0.0.1:9881/v1
```

API Key 随意（本地不校验），模型名 `tts-1`。

### 其他

任何 OpenAI TTS 客户端都行：把 base URL 指向 `http://127.0.0.1:9881/v1` 即可。

## 开机自启（Windows）

`start-hidden.vbs` 是隐藏窗口启动脚本，把它（或快捷方式）放进：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

VBS 里用的是绝对路径，**请先按自己的安装位置改一下**。

## 排错

| 现象 | 原因 / 解决 |
|---|---|
| `502 无法连接 GPT-SoVITS` | 后端没起，或端口/路径不对。先 `curl http://127.0.0.1:9880/docs` |
| `502 GPT-SoVITS 返回 400: cmudict not found` | 文本含英文，开 `strip_ascii` |
| `502 ... TorchCodec is required` | GPT-SoVITS 端问题，见 [docs/GPTSOVITS-NOTES.md](docs/GPTSOVITS-NOTES.md) 第 4 条 |
| 声音不像参考音频 | `prompt_text` 与 `prompt_audio` 不一致，或权重与推理档版本不匹配 |
| 第一次请求很慢（几十秒） | 模型首次加载进显存，之后 1–3 秒 |

## 许可证

代码 MIT。**不包含任何模型、音色或参考音频**——包括 README 里的示例音色，都只是名字，不随仓库分发。

GPT-SoVITS 主体为 MIT；你使用的具体音色模型可能有自己的授权，**分发前请自行确认**。
