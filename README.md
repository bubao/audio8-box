# Audio8Box

基于 Audio8-TTS 0.6B（arktts 架构）的本地语音合成服务。Apple Silicon 走 MLX，其他平台自动回退 PyTorch。提供 FastAPI 接口和一个零依赖的 Web 控制台（合成 + 批量 + 音色库 + 模型管理），模型支持从 **Hugging Face** 或 **ModelScope** 下载。

## 特性

- **双后端**：Apple Silicon 用 MLX（`mlx-audio`），其余平台用 PyTorch（Transformers 自定义代码）。
- **双下载源**：`hf`（Hugging Face）与 `modelscope`（ModelScope），同一模型可任选源。
- **44.1 kHz 输出**，内置神经 codec，支持零样本音色克隆。
- **长文本自动分句**，逐段合成后拼接。
- **批量合成**：上传 `.txt` 或粘贴文本，按句拆分逐句合成，打包 ZIP 并附 `manifest.json`（句子 ↔ 文件映射）。
- **音色库**：把参考音频 + 参考文本保存为命名音色，合成 / 批量通过 ID 直接引用，无需每次重新上传。
- **OpenAI 风格接口**：`POST /v1/audio/speech`。
- **Web 控制台**：合成、批量合成、参数调节、音色克隆、模型安装/删除/进度查看、按源切换。
- **CLI 模型管理**：`models.py list / install / path / remove`。

## 快速开始

```bash
uv sync
uv run models.py install preview      # 先安装模型（也可只在 Web 控制台安装）
uv run server.py
# 打开 http://127.0.0.1:8000/
```

服务不会在启动时自动下载权重；`/healthz` 只加载已安装的本地模型，未安装会返回 503 并提示安装命令。

## 文档

- [模型](docs/models.md)：模型目录、CLI、下载源与缓存
- [Web 控制台](docs/web-console.md)：页面功能与音色库操作
- [API](docs/api.md)：接口参考与批量 manifest
- [配置与测试](docs/configuration.md)：环境变量、测试、目录结构

## 环境要求

- Python >= 3.10
- Apple Silicon（推荐，MLX 后端）或带 CUDA 的 NVIDIA GPU / CPU（PyTorch 后端）
- [uv](https://docs.astral.sh/uv/) >= 0.13
