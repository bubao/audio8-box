# 模型

内置模型定义在 `models.py` 的 `MODEL_CATALOG`，每个模型可挂多个下载源：

| 名称 | 后端 | 默认源 | 源与仓库 | 语言 | 说明 |
|---|---|---|---|---|---|
| `preview` | MLX | hf | `mlx-community/Audio8-TTS-Preview-0.6b-bf16`（HF / ModelScope 同名） | 11 | 紧凑快速，中文表现稳定 |
| `realistic` | MLX | hf | `mlx-community/Audio8-TTS-Realistic-0.6b-bf16`（HF / ModelScope 同名） | 23 | 语言覆盖广，语速偏慢 |
| `realistic-air` | PyTorch | modelscope | `Edge0/Audio8-TTS-Realistic-0.6b` | 23 | Transformers/PyTorch 权重；已排除 `llama-cpp/` GGUF（约 3.7 GB） |

> `realistic-air` 是 ModelScope 上的 Transformers 版本（Realistic Air），与 MLX 版 `realistic` 权重格式不同，需要 PyTorch 后端：
>
> ```bash
> uv sync --extra pytorch
> ```

## CLI

`models.py` 可传任意 `owner/name` 仓库 ID，默认走 HF：

```bash
uv run models.py list                              # 列出每个模型在每个源的状态
uv run models.py install preview                   # 用默认源下载
uv run models.py install realistic --source modelscope
uv run models.py install realistic-air              # 仅支持 ModelScope
uv run models.py path preview                      # 打印快照路径
uv run models.py remove preview --source modelscope # 删除缓存（交互确认，-y 跳过）
```

## Web

打开页面 → 「模型管理」。多源模型带源选择器，可分别查看 / 安装 / 删除；卡片显示来源（Hugging Face / ModelScope）与后端（MLX / PyTorch）徽章。

## 下载源与缓存

| 源 | 环境变量 | 默认路径 |
|---|---|---|
| HF | `HF_HUB_CACHE` | `~/.cache/huggingface/hub` |
| ModelScope | `MODELSCOPE_CACHE` | `~/.cache/modelscope` |
| 音色库 | `AUDIO8_VOICES_DIR` | `~/.audio8/voices` |

页面与 CLI 的模型状态基于上述两个缓存目录判断；自定义缓存路径需同步设置对应环境变量。

## 备注

- `preview` / `realistic`（MLX）权重约 2.5 GB；`realistic-air`（PyTorch，排除 llama-cpp 后）约 2.5 GB，首次下载需预留时间与磁盘。
- 服务不会在启动时自动下载权重；`/healthz` 只加载已安装的本地模型，未安装会返回 503 并提示安装命令。
