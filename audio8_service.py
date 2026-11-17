from __future__ import annotations

import asyncio
import io
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from models import MODEL_CATALOG, MODEL_IDS, resolve_model_path
from text_utils import split_text

MEDIA_TYPES = {
    "wav": "audio/wav",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "mp3": "audio/mpeg",
}


class TTSBackend(Protocol):
    sample_rate: int

    def generate(
        self,
        text: str,
        ref_audio: str | Path | None = None,
        ref_text: str | None = None,
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 50,
    ) -> tuple[np.ndarray, int]:
        ...


class MLXBackend:
    """Apple Silicon 上的默认后端，使用 mlx-audio 的 arktts 支持。"""

    def __init__(self, model_ref: str):
        try:
            from mlx_audio.tts.utils import load
        except ImportError as exc:
            raise RuntimeError(
                "MLX backend requires mlx-audio; install with: pip install 'mlx-audio>=0.4.7'"
            ) from exc

        self._model = load(model_ref)
        self.sample_rate = 44100

    def generate(
        self,
        text: str,
        ref_audio: str | Path | None = None,
        ref_text: str | None = None,
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 50,
    ) -> tuple[np.ndarray, int]:
        kwargs: dict[str, object] = {
            "text": text,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
        }
        if ref_audio is not None:
            if not ref_text:
                raise ValueError("ref_text is required when ref_audio is provided")
            kwargs["ref_audio"] = str(ref_audio)
            kwargs["ref_text"] = ref_text

        for result in self._model.generate(**kwargs):
            audio = np.asarray(result.audio, dtype=np.float32).squeeze()
            if audio.ndim != 1:
                audio = audio.reshape(-1)
            return audio, int(result.sample_rate)

        raise RuntimeError("model.generate returned no audio")


class PyTorchBackend:
    """非 Apple Silicon 或需要 Transformers 生态时的回退后端。"""

    def __init__(self, model_ref: str, device: str | None = None):
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise RuntimeError(
                "PyTorch backend requires torch and transformers; "
                "install with: pip install torch torchaudio 'transformers>=4.57,<5'"
            ) from exc

        self._torch = torch
        self._processor = AutoProcessor.from_pretrained(model_ref, trust_remote_code=True)
        self._model = AutoModel.from_pretrained(model_ref, trust_remote_code=True).eval()
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = device
        self._model.to(device)
        self.sample_rate = 44100

    def generate(
        self,
        text: str,
        ref_audio: str | Path | None = None,
        ref_text: str | None = None,
        temperature: float = 0.7,
        top_p: float = 0.9,
        top_k: int = 50,
    ) -> tuple[np.ndarray, int]:
        torch = self._torch
        processor_kwargs: dict[str, object] = {
            "text": [text],
            "return_tensors": "pt",
        }
        if ref_audio is not None:
            if not ref_text:
                raise ValueError("ref_text is required when ref_audio is provided")
            processor_kwargs["reference_audio"] = [str(ref_audio)]
            processor_kwargs["reference_text"] = [ref_text]

        inputs = self._processor(**processor_kwargs)
        inputs = {
            key: value.to(self._device) if hasattr(value, "to") else value
            for key, value in inputs.items()
        }

        with torch.inference_mode():
            output = self._model.generate(
                **inputs,
                max_new_tokens=512,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
            )
            waveforms, _ = self._model.decode_audio(output.codes)

        audio = (
            waveforms.detach().cpu().numpy()
            if hasattr(waveforms, "detach")
            else np.asarray(waveforms)
        )
        audio = audio.astype(np.float32).squeeze()
        if audio.ndim != 1:
            audio = audio.reshape(-1)
        return audio, self.sample_rate


@dataclass
class ServiceConfig:
    model: str = "preview"
    backend: str = "auto"
    max_chars: int = 300
    max_concurrency: int = 1
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 50
    device: str | None = None


class Audio8Service:
    """串行化推理的 TTS 服务核心；FastAPI 只负责 IO。"""

    def __init__(self, config: ServiceConfig | None = None, backend: TTSBackend | None = None):
        self.config = config or ServiceConfig()
        self._backend = backend
        self._backend_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.config.max_concurrency)

    @property
    def model_id(self) -> str:
        return MODEL_IDS.get(self.config.model, self.config.model)

    @property
    def backend_name(self) -> str:
        if self.config.backend != "auto":
            return self.config.backend
        spec = MODEL_CATALOG.get(self.config.model)
        if spec and spec.get("backend"):
            return spec["backend"]
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            return "mlx"
        return "pytorch"

    def _load_backend(self) -> TTSBackend:
        name = self.config.model
        model_ref = resolve_model_path(name)
        if model_ref is None:
            spec = MODEL_CATALOG.get(name)
            if spec:
                raise RuntimeError(
                    f"模型未安装：{name}（默认源 {spec['default_source']}）。请先运行 "
                    f"`uv run models.py install {name}` 或在 Web 控制台安装。"
                )
            # 未收录在目录中的名称：视为本地路径或远端仓库 ID。
            model_ref = self.model_id

        if self.backend_name == "mlx":
            return MLXBackend(str(model_ref))
        if self.backend_name == "pytorch":
            return PyTorchBackend(str(model_ref), device=self.config.device)
        raise ValueError(f"unknown backend: {self.backend_name}")

    async def _get_backend(self) -> TTSBackend:
        if self._backend is not None:
            return self._backend
        async with self._backend_lock:
            if self._backend is None:
                self._backend = await asyncio.to_thread(self._load_backend)
        return self._backend

    async def synthesize(
        self,
        text: str,
        ref_audio: str | Path | None = None,
        ref_text: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
    ) -> tuple[np.ndarray, int]:
        if not text or not text.strip():
            raise ValueError("text is empty")
        if ref_audio is not None and not ref_text:
            raise ValueError("ref_text is required when ref_audio is provided")

        backend = await self._get_backend()
        chunks = split_text(text, self.config.max_chars)
        temperature = self.config.temperature if temperature is None else temperature
        top_p = self.config.top_p if top_p is None else top_p
        top_k = self.config.top_k if top_k is None else top_k

        # MLX/Metal 推理不适合并发抢 GPU；默认 max_concurrency=1。
        async with self._semaphore:
            parts: list[np.ndarray] = []
            for chunk in chunks:
                audio, sample_rate = await asyncio.to_thread(
                    backend.generate,
                    chunk,
                    ref_audio,
                    ref_text,
                    temperature,
                    top_p,
                    top_k,
                )
                parts.append(audio)

        if not parts:
            return np.empty(0, dtype=np.float32), backend.sample_rate
        return np.concatenate(parts), backend.sample_rate


def encode_audio(audio: np.ndarray, sample_rate: int, audio_format: str = "wav") -> tuple[bytes, str]:
    """编码为 WAV/FLAC/OGG；MP3 不支持时回退 WAV。"""
    import soundfile as sf

    fmt = audio_format.lower()
    if fmt not in MEDIA_TYPES:
        raise ValueError(f"unsupported audio format: {audio_format}")

    buffer = io.BytesIO()
    try:
        sf.write(
            buffer,
            audio,
            sample_rate,
            format=fmt.upper(),
            subtype="PCM_16" if fmt == "wav" else None,
        )
        return buffer.getvalue(), fmt
    except Exception:
        if fmt != "mp3":
            raise
        buffer = io.BytesIO()
        sf.write(buffer, audio, sample_rate, format="WAV", subtype="PCM_16")
        return buffer.getvalue(), "wav"
