# 配置与测试

## 环境变量

通过环境变量调整（启动 `server.py` 时生效）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AUDIO8_MODEL` | `preview` | 模型名（`preview` / `realistic` / `realistic-air`）或本地路径 / 仓库 ID |
| `AUDIO8_BACKEND` | `auto` | `auto` / `mlx` / `pytorch`；`auto` 时按模型目录选择 |
| `AUDIO8_MAX_CHARS` | `300` | 单段最大字符数 |
| `AUDIO8_MAX_CONCURRENCY` | `1` | 推理并发（MLX 建议 1） |
| `AUDIO8_TEMPERATURE` | `0.7` | 默认温度 |
| `AUDIO8_TOP_P` | `0.9` | 默认 top_p |
| `AUDIO8_TOP_K` | `50` | 默认 top_k |
| `AUDIO8_DEVICE` | 空 | PyTorch 设备（如 `cuda` / `cpu`） |
| `AUDIO8_VOICES_DIR` | `~/.audio8/voices` | 音色库目录（索引 `voices.json` + 参考音频文件） |
| `HOST` | `127.0.0.1` | 监听地址 |
| `PORT` | `8000` | 监听端口 |

缓存位置见[模型](models.md#下载源与缓存)。uv 与镜像配置见 `uv.toml`。

## 测试

```bash
uv run python -m unittest discover -s tests -v
```

## 目录结构

```
.
├── audio8_service.py   # 后端抽象：MLX / PyTorch 加载与合成
├── server.py           # FastAPI 应用与路由
├── models.py           # 模型目录 + HF/ModelScope 缓存与下载管理（CLI）
├── voices.py           # 音色库存储（索引 voices.json + 参考音频文件）
├── text_utils.py       # 长文本分句与批量句子拆分
├── static/             # Web 控制台（index.html / styles.css / app.js）
├── tests/              # 单元测试（模型目录、句子拆分、音色库）
├── docs/               # 文档
├── pyproject.toml      # 依赖声明
└── uv.toml             # uv 缓存与镜像配置
```

## 备注

- MLX 后端在 Apple Silicon 上建议单并发（`AUDIO8_MAX_CONCURRENCY=1`）。
- 批量任务与临时 ZIP 保存在单 worker 进程内，服务重启即丢失。
