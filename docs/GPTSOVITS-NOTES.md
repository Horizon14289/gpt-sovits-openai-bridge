# GPT-SoVITS 部署踩坑记录

我们在 Windows + RTX 50 系显卡上完整部署过一次 GPT-SoVITS（v2ProPlus 微调音色），
以下问题**全部实际遇到并解决**。按出现频率排序。

---

## 1. 安装器把 Python 路径当元组用

**现象**：创建虚拟环境时报 `C : -m venv ... 失败`（`C` 和 `:` 之间有个空格）。

**原因**：某些安装脚本在环境变量分支返回字符串，调用处却按 `[命令, 参数]` 元组取值：

```js
if (process.env.PYTHON) return process.env.PYTHON;   // 返回字符串
run(python[0], [...python[1], '-m', 'venv']);        // python[0] === "C"
```

**解决**：让它返回 `[路径, []]`，或不要用环境变量、直接让 `python` 在 PATH 里。

---

## 2. PyTorch 从官方源下载卡死

**现象**：`pip install torch` 十几分钟零进度，CPU 和内存都很低。

**解决**：换国内镜像。

```bash
pip install torch torchaudio --index-url https://mirrors.aliyun.com/pytorch-wheels/cu128
```

其他可用镜像：`https://mirror.sjtu.edu.cn/pytorch-wheels/cu128`。

---

## 3. `numpy<2` 与新版 scipy 冲突导致 pip 无限回溯

**现象**：pip 在 `scipy` 的十几个版本之间来回试，日志刷屏但不动。

**原因**：GPT-SoVITS 要求 `numpy<2`，而新版 scipy 要求 `numpy>=2`，pip 永远找不到解。

**解决**：显式锁版本。

```bash
pip install "numpy==1.26.4" "scipy==1.13.1"
```

---

## 4. `torchaudio` 依赖 torchcodec，加载不到 FFmpeg

**现象**：

```
Failed to create AudioDecoder for xxx.wav: Could not load libtorchcodec.
```

**原因**：torchaudio 2.x 用 torchcodec 解码音频，而 torchcodec 需要 FFmpeg **共享库**（`.dll`），
静态编译的 ffmpeg 不够。

**方案 A**：装 torchcodec 并确保 PATH 里有共享版 FFmpeg（`ffmpeg-master-latest-win64-gpl-shared`）。

**方案 B（我们采用的，更省事）**：直接改源码，用 soundfile 读音频，绕开 torchcodec。

在 `GPT_SoVITS/TTS_infer_pack/TTS.py` 里加一个辅助函数，替换 `torchaudio.load(...)`：

```python
def _load_audio_sf(path, target_sr=None):
    import soundfile as sf, numpy as np
    data, sr = sf.read(path, always_2d=True, dtype="float32")
    wav = torch.from_numpy(np.ascontiguousarray(data.T))
    if target_sr and sr != target_sr:
        wav = torchaudio.functional.resample(wav, sr, target_sr)
    return wav, sr
```

---

## 5. Python 3.12 上没有 `jieba_fast` 的 wheel

**现象**：`ModuleNotFoundError: No module named 'jieba_fast.posseg'` 或编译失败。

**解决**：写一个转发垫片，把 `jieba_fast` 指到 `jieba`。在
`venv/Lib/site-packages/jieba_fast/` 下建两个文件：

```python
# __init__.py
from jieba import *
from . import posseg
```

```python
# posseg.py
from jieba.posseg import *
from jieba.posseg import cut, lcut
```

注意**必须有 `posseg.py` 这个子模块**，只写 `__init__.py` 会报找不到 `jieba_fast.posseg`。

---

## 6. 缺 BERT 分词器文件 / 语言检测模型

**现象 A**：

```
OSError: Can't load tokenizer for '.../chinese-roberta-wwm-ext-large'
```

**原因**：只下了 `config.json` + `tokenizer.json` + `pytorch_model.bin`，缺 `tokenizer_config.json`。

**解决**：补一个文件即可：

```json
{"tokenizer_class": "RobertaTokenizerFast", "model_max_length": 512}
```

**现象 B**：

```
fast-langdetect: Cache directory not found: .../pretrained_models/fast_langdetect
```

**解决**：建目录并下载 `lid.176.bin`（约 131 MB）：

```bash
curl -L -o GPT_SoVITS/pretrained_models/fast_langdetect/lid.176.bin \
  https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin
```

---

## 7. NLTK 语料下载被代理拦截

**现象**：

```
[nltk_data] [pathsec.urlopen]: SSRF attempt to restricted IP 127.0.0.1
```

**原因**：`nltk.download()` 走了系统代理，被安全策略判定为 SSRF。

**影响**：中文合成不受影响；只有文本里混入英文时才会触发（需要 `cmudict`）。

**规避**：
- 让桥剥离英文（`strip_ascii: true`，本项目默认）
- 或手动放置 `cmudict` 语料到 `%APPDATA%\nltk_data`

---

## 关于推理档（重要）

GPT-SoVITS 的权重**分版本**，而且必须和推理档匹配：

| 推理档 | 需要的权重版本 |
|---|---|
| GPU | `v5turbo` |
| CPU | `v2ProPlus` |

**用错版本不会报错**，只会静默回落到基础音色——听起来像“换了个陌生人”。

如果你微调的是 v2ProPlus（大多数中文社区模型），只能用 CPU 档，或者自己转换权重。
