from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from huggingface_hub import HfApi
from pydantic import BaseModel, Field

from audio8_service import MEDIA_TYPES, Audio8Service, ServiceConfig, encode_audio
from models import (
    MODEL_CATALOG,
    SOURCES,
    cache_dir,
    download_model,
    find_installed,
    human_size,
    is_installed,
    resolve_model_ref,
    scan_cache,
)
from text_utils import split_sentences
import voices as voice_store


class SpeechRequest(BaseModel):
    input: str = Field(..., max_length=20000)
    voice: str = "default"
    ref_audio: str | None = None
    ref_text: str | None = None
    voice_id: str | None = None
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 50
    response_format: str = "wav"


class ModelInstallRequest(BaseModel):
    model: str
    source: str | None = None


# 后台下载任务：task_id -> 状态。仅在单 worker 进程内有效，服务重启即丢失。
_model_tasks: dict[str, dict] = {}
_active_downloads: dict[tuple[str, str], str] = {}  # (source, model_id) -> task_id

# 批量合成任务：task_id -> 状态。
_batch_tasks: dict[str, dict] = {}
_batch_lock = threading.Lock()  # MLX 推理串行，批量任务同一时刻只跑一个

BATCH_MAX_CHARS = 100_000
BATCH_MAX_SENTENCES = 500


def _resolve_or_400(name: str, source: str | None = None) -> tuple[str, str]:
    try:
        return resolve_model_ref(name, source)
    except SystemExit as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _ignore_patterns_for(name: str, source: str) -> list[str] | None:
    if source != "modelscope":
        return None
    spec = MODEL_CATALOG.get(name) or {}
    return spec.get("ignore_patterns")


def _remote_size(source: str, model_id: str) -> int | None:
    if source != "hf":
        return None
    try:
        return getattr(HfApi().model_info(model_id), "size", None)
    except Exception:
        return None


def _download_worker(
    task_id: str, source: str, model_id: str, ignore_patterns: list[str] | None
) -> None:
    try:
        download_model(source, model_id, ignore_patterns=ignore_patterns)
        _model_tasks[task_id].update(status="done", message="安装完成")
    except Exception as exc:
        _model_tasks[task_id].update(status="error", message=str(exc))
    finally:
        _active_downloads.pop((source, model_id), None)


def _model_status(
    source: str, model_id: str, entry: dict | None, installed: bool, snap: Path | None
) -> str:
    if (source, model_id) in _active_downloads:
        return "downloading"
    if installed:
        return "installed"
    if entry and snap:
        return "partial"
    return "not_installed"


def _materialize_ref(ref_audio: str | None) -> tuple[str | None, bool]:
    """把本地路径或 base64/data URI 参考音频变成后端可读路径。"""
    if not ref_audio:
        return None, False
    if os.path.exists(ref_audio):
        return ref_audio, False

    try:
        if ref_audio.startswith("data:"):
            header, payload = ref_audio.split(",", 1)
            suffix = ".wav"
            if "audio/mpeg" in header or "mp3" in header:
                suffix = ".mp3"
            elif "audio/ogg" in header:
                suffix = ".ogg"
            elif "audio/flac" in header:
                suffix = ".flac"
            data = base64.b64decode(payload)
        else:
            suffix = ".wav"
            data = base64.b64decode(ref_audio)
    except Exception as exc:
        raise ValueError("ref_audio must be a local path, base64, or data URI") from exc

    fd, path = tempfile.mkstemp(prefix="audio8-ref-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        os.unlink(path)
        raise
    return path, True


def _resolve_clone_ref(
    voice_id: str | None, ref_audio: str | None, ref_text: str | None
) -> tuple[str | None, str | None, bool, dict | None]:
    """解析克隆来源（已保存音色 / 上传或内联参考音频）。

    返回 ``(path, text, should_cleanup, reference_meta)``；``reference_meta``
    用于 manifest / 任务元数据（``{"voice":...}`` 或 ``{"audio":...}``）。
    """
    if voice_id:
        item = voice_store.get_voice(voice_id)
        if not item or not item.get("exists"):
            raise HTTPException(status_code=400, detail="音色不存在或参考音频缺失")
        if ref_audio:
            raise HTTPException(status_code=400, detail="不能同时使用已保存音色与参考音频")
        if (ref_text or "").strip():
            raise HTTPException(status_code=400, detail="不能同时使用已保存音色与参考文本")
        text = str(item.get("ref_text") or "")
        meta = {"voice": item.get("id"), "name": item.get("name"), "text": text}
        return str(voice_store.voices_dir() / str(item["file"])), text, False, meta

    path, cleanup = _materialize_ref(ref_audio)
    text = (ref_text or "").strip()
    if path and not text:
        raise HTTPException(status_code=400, detail="使用参考音频时必须填写参考文本")
    if text and not path:
        raise HTTPException(status_code=400, detail="参考文本需要配套参考音频")
    meta = None
    if path:
        name = "reference"
        if ref_audio and os.path.exists(ref_audio):
            name = Path(ref_audio).name
        meta = {"audio": name, "text": text}
    return path, text or None, cleanup, meta


def _clean_ref_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


async def _read_batch_ref(upload: object) -> tuple[str | None, bool, str | None]:
    """读取批量任务的参考音频上传，落盘为临时文件。"""
    if upload is None or not getattr(upload, "filename", None):
        return None, False, None
    filename = upload.filename or "reference.wav"
    suffix = Path(filename).suffix or ".wav"
    fd, path = tempfile.mkstemp(prefix="audio8-batch-ref-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(await upload.read())
    except Exception:
        os.unlink(path)
        raise
    return path, True, filename


def create_app(config: ServiceConfig | None = None) -> FastAPI:
    service = Audio8Service(config)
    app = FastAPI(title="Audio8Box", version="0.1.0")
    app.state.service = service

    @app.get("/healthz")
    async def healthz():
        try:
            backend = await service._get_backend()
            return {
                "ok": True,
                "backend": service.backend_name,
                "model": service.model_id,
                "sample_rate": backend.sample_rate,
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @app.get("/v1/models")
    async def list_models():
        return {
            "data": [{"id": name, "object": "model"} for name in MODEL_CATALOG]
        }

    @app.get("/api/models")
    async def list_model_status():
        caches = {source: scan_cache(source) for source in SOURCES}
        items = []
        for name, spec in MODEL_CATALOG.items():
            sources = []
            for source, model_id in spec["sources"].items():
                entry = caches[source].get(model_id)
                installed, snap = (False, None)
                size = 0
                if entry:
                    installed, snap = is_installed(entry)
                    size = entry["size"]
                sources.append(
                    {
                        "source": source,
                        "model_id": model_id,
                        "status": _model_status(
                            source, model_id, entry, installed, snap
                        ),
                        "size": size,
                        "size_human": human_size(size),
                        "path": str(entry["path"]) if entry else None,
                        "snapshot": str(snap) if snap else None,
                    }
                )
            items.append(
                {
                    "name": name,
                    "default_source": spec["default_source"],
                    "backend": spec.get("backend"),
                    "languages": spec.get("languages"),
                    "note": spec.get("note", ""),
                    "sources": sources,
                }
            )
        return {
            "cache_dirs": {source: str(cache_dir(source)) for source in SOURCES},
            "models": items,
        }

    @app.post("/api/models/install")
    async def install_model(request: ModelInstallRequest):
        source, model_id = _resolve_or_400(request.model, request.source)
        installed, _, _ = find_installed(source, model_id)
        if installed:
            raise HTTPException(status_code=409, detail=f"模型已安装：{model_id}")
        if (source, model_id) in _active_downloads:
            raise HTTPException(status_code=409, detail="该模型正在下载中")

        total_size = _remote_size(source, model_id)
        task_id = uuid.uuid4().hex
        _model_tasks[task_id] = {
            "task_id": task_id,
            "model_name": request.model,
            "source": source,
            "model_id": model_id,
            "status": "downloading",
            "message": "开始下载…",
            "total_size": total_size,
            "started_at": time.time(),
        }
        _active_downloads[(source, model_id)] = task_id
        threading.Thread(
            target=_download_worker,
            args=(
                task_id,
                source,
                model_id,
                _ignore_patterns_for(request.model, source),
            ),
            daemon=True,
        ).start()
        return {"task_id": task_id, "status": "downloading", "total_size": total_size}

    @app.get("/api/models/tasks/{task_id}")
    async def get_model_task(task_id: str):
        task = _model_tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        downloaded = 0
        entry = scan_cache(task["source"]).get(task["model_id"])
        if entry:
            downloaded = entry["size"]
        total = task.get("total_size")
        return {
            "task_id": task_id,
            "model_name": task.get("model_name", task["model_id"]),
            "source": task["source"],
            "model_id": task["model_id"],
            "status": task["status"],
            "message": task["message"],
            "total_size": total,
            "downloaded_size": downloaded,
            "progress": (downloaded / total) if total else None,
        }

    @app.delete("/api/models/{name}")
    async def remove_model(name: str, source: str | None = None, yes: bool = False):
        source, model_id = _resolve_or_400(name, source)
        if (source, model_id) in _active_downloads:
            raise HTTPException(status_code=409, detail="模型正在下载中，无法删除")
        entry = scan_cache(source).get(model_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"未安装：{model_id}")
        if not yes:
            raise HTTPException(status_code=400, detail="需要确认：添加 ?yes=true")
        shutil.rmtree(entry["path"])
        return {"removed": True, "source": source, "model_id": model_id}

    @app.post("/v1/audio/speech")
    async def speech(request: SpeechRequest):
        ref_path, ref_text, should_cleanup, _ = _resolve_clone_ref(
            request.voice_id, request.ref_audio, request.ref_text
        )
        try:
            audio, sample_rate = await service.synthesize(
                text=request.input,
                ref_audio=ref_path,
                ref_text=ref_text,
                temperature=request.temperature,
                top_p=request.top_p,
                top_k=request.top_k,
            )
            payload, audio_format = encode_audio(audio, sample_rate, request.response_format)
            return Response(content=payload, media_type=MEDIA_TYPES[audio_format])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        finally:
            if should_cleanup and ref_path:
                os.unlink(ref_path)

    @app.post("/v1/audio/speech/clone")
    async def clone(
        input: str = Form(..., max_length=20000),
        ref_audio: UploadFile | None = File(None),
        ref_text: str = Form(""),
        voice_id: str = Form(""),
        temperature: float = Form(0.7),
        top_p: float = Form(0.9),
        top_k: int = Form(50),
        response_format: str = Form("wav"),
    ):
        should_cleanup = False
        if voice_id:
            ref_path, ref_text, should_cleanup, _ = _resolve_clone_ref(voice_id, None, None)
        else:
            if ref_audio is None:
                raise HTTPException(status_code=400, detail="需要参考音频或已保存音色")
            suffix = Path(ref_audio.filename or "reference.wav").suffix or ".wav"
            fd, ref_path = tempfile.mkstemp(prefix="audio8-ref-", suffix=suffix)
            should_cleanup = True
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(await ref_audio.read())
                ref_text = (ref_text or "").strip()
                if not ref_text:
                    raise HTTPException(status_code=400, detail="使用参考音频时必须填写参考文本")
            except HTTPException:
                os.unlink(ref_path)
                raise
            except Exception:
                os.unlink(ref_path)
                raise
        try:
            audio, sample_rate = await service.synthesize(
                text=input,
                ref_audio=ref_path,
                ref_text=ref_text,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
            )
            payload, audio_format = encode_audio(audio, sample_rate, response_format)
            return Response(content=payload, media_type=MEDIA_TYPES[audio_format])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        finally:
            if should_cleanup and ref_path:
                os.unlink(ref_path)

    # ---------- 批量合成（按句拆分 -> 逐句合成 -> 打包 zip + manifest） ----------

    def _safe_zip_name(filename: str | None) -> str:
        stem = Path(filename or "batch").stem or "batch"
        stem = re.sub(r"[^\w.-]+", "_", stem).strip("._") or "batch"
        return f"{stem}.zip"

    def _batch_worker(
        task_id: str,
        text: str,
        filename: str,
        temperature: float,
        top_p: float,
        top_k: int,
        response_format: str,
        ref_audio_path: str | None = None,
        ref_text: str | None = None,
        reference_meta: dict | None = None,
        ref_temp: bool = False,
    ) -> None:
        task = _batch_tasks[task_id]
        tmp_dir: str | None = None
        try:
            sentences = split_sentences(text, service.config.max_chars)
            if not sentences:
                raise ValueError("文本为空或没有可合成的句子")
            task.update(status="running", total=len(sentences), done=0, error=None)

            loop = asyncio.new_event_loop()
            try:
                backend = loop.run_until_complete(service._get_backend())
            finally:
                loop.close()

            fmt = response_format.lower()
            if fmt not in MEDIA_TYPES:
                raise ValueError(f"不支持的音频格式：{response_format}")

            tmp_dir = tempfile.mkdtemp(prefix="audio8-batch-")
            zip_name = _safe_zip_name(filename)
            zip_path = os.path.join(tmp_dir, zip_name)
            items: list[dict] = []
            sample_rate = backend.sample_rate
            actual_format = fmt  # mp3 编码不可用时会回退 wav

            # MLX/Metal 推理串行；批量任务同一时刻只跑一个。
            with _batch_lock:
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for index, sentence in enumerate(sentences, 1):
                        task.update(done=index - 1, current=index, current_text=sentence)
                        audio, sample_rate = backend.generate(
                            sentence, ref_audio_path, ref_text, temperature, top_p, top_k
                        )
                        payload, actual_format = encode_audio(audio, sample_rate, fmt)
                        audio_name = f"{index:03d}.{actual_format}"
                        zf.writestr(audio_name, payload)
                        items.append({"index": index, "file": audio_name, "text": sentence})
                    manifest = {
                        "source": filename,
                        "count": len(items),
                        "format": actual_format,
                        "sample_rate": sample_rate,
                        "model": service.model_id,
                        "backend": service.backend_name,
                        "reference": reference_meta,
                        "sentences": items,
                    }
                    zf.writestr(
                        "manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                    )

            task.update(
                status="done",
                done=len(items),
                current=len(items),
                current_text="",
                zip_path=zip_path,
                zip_name=zip_name,
                count=len(items),
                format=actual_format,
                sample_rate=sample_rate,
                manifest=manifest,
            )
        except Exception as exc:
            task.update(status="error", error=str(exc))
        finally:
            if tmp_dir and task.get("status") != "done":
                shutil.rmtree(tmp_dir, ignore_errors=True)
            if ref_temp and ref_audio_path and os.path.exists(ref_audio_path):
                os.unlink(ref_audio_path)

    def _parse_batch_params(
        text: str,
        filename: str | None,
        raw_temperature: object,
        raw_top_p: object,
        raw_top_k: object,
        raw_format: object,
    ) -> tuple[str, str, float, float, int, str]:
        try:
            temperature = float(raw_temperature)
            top_p = float(raw_top_p)
            top_k = int(raw_top_k)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="采样参数不合法")
        fmt = str(raw_format or "wav").lower()
        if fmt not in MEDIA_TYPES:
            raise HTTPException(status_code=400, detail=f"不支持的音频格式：{raw_format}")
        return text, filename or "input.txt", temperature, top_p, top_k, fmt

    @app.post("/api/batch")
    async def create_batch(request: Request):
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            text = ""
            filename = "input.txt"
            if upload is not None and getattr(upload, "filename", None):
                raw = await upload.read()
                text = raw.decode("utf-8", errors="replace")
                filename = upload.filename or "input.txt"
            if form.get("input"):
                text = str(form.get("input"))
            if form.get("filename"):
                filename = str(form.get("filename"))
            text, filename, temperature, top_p, top_k, fmt = _parse_batch_params(
                text,
                filename,
                form.get("temperature", 0.7),
                form.get("top_p", 0.9),
                form.get("top_k", 50),
                form.get("response_format", "wav"),
            )
            voice_id = _clean_ref_text(form.get("voice"))
            ref_text = _clean_ref_text(form.get("ref_text"))
            reference_meta: dict | None = None
            if voice_id:
                ref_audio_path, ref_text, ref_temp, reference_meta = _resolve_clone_ref(
                    voice_id, None, ref_text
                )
            else:
                ref_audio_path, ref_temp, ref_audio_name = await _read_batch_ref(
                    form.get("ref_audio")
                )
                if ref_audio_path and not ref_text:
                    raise HTTPException(status_code=400, detail="使用参考音频时必须填写参考文本")
                if ref_text and not ref_audio_path:
                    raise HTTPException(status_code=400, detail="参考文本需要配套参考音频")
                if ref_audio_path:
                    reference_meta = {"audio": ref_audio_name, "text": ref_text}
        else:
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="需要 multipart 表单或 JSON 请求体")
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="请求体必须是对象")
            text, filename, temperature, top_p, top_k, fmt = _parse_batch_params(
                str(body.get("input", "")),
                body.get("filename"),
                body.get("temperature", 0.7),
                body.get("top_p", 0.9),
                body.get("top_k", 50),
                body.get("response_format", "wav"),
            )
            voice_id = _clean_ref_text(body.get("voice"))
            ref_text = _clean_ref_text(body.get("ref_text"))
            ref_audio_path, ref_temp = None, False
            reference_meta: dict | None = None
            if voice_id:
                ref_audio_path, ref_text, ref_temp, reference_meta = _resolve_clone_ref(
                    voice_id, None, ref_text
                )
            else:
                if body.get("ref_audio"):
                    try:
                        ref_audio_path, ref_temp = _materialize_ref(str(body["ref_audio"]))
                    except ValueError as exc:
                        raise HTTPException(status_code=400, detail=str(exc))
                    reference_meta = {"audio": "reference", "text": ref_text}
                if ref_audio_path and not ref_text:
                    raise HTTPException(status_code=400, detail="使用参考音频时必须填写参考文本")
                if ref_text and not ref_audio_path:
                    raise HTTPException(status_code=400, detail="参考文本需要配套参考音频")

        text = text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="文本为空")
        if ref_audio_path and not ref_text:
            raise HTTPException(status_code=400, detail="使用参考音频时必须填写参考文本")
        if ref_text and not ref_audio_path:
            raise HTTPException(status_code=400, detail="参考文本需要配套参考音频")
        if len(text) > BATCH_MAX_CHARS:
            raise HTTPException(status_code=400, detail=f"文本过长（上限 {BATCH_MAX_CHARS} 字）")
        sentences = split_sentences(text)
        if len(sentences) > BATCH_MAX_SENTENCES:
            raise HTTPException(
                status_code=400,
                detail=f"句子过多（{len(sentences)}，上限 {BATCH_MAX_SENTENCES}）",
            )

        task_id = uuid.uuid4().hex
        _batch_tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "source": filename,
            "total": len(sentences),
            "done": 0,
            "current": 0,
            "current_text": "",
            "error": None,
            "zip_path": None,
            "zip_name": None,
            "count": None,
            "format": None,
            "sample_rate": None,
            "manifest": None,
            "reference": reference_meta,
            "created_at": time.time(),
        }
        threading.Thread(
            target=_batch_worker,
            args=(
                task_id,
                text,
                filename,
                temperature,
                top_p,
                top_k,
                fmt,
                ref_audio_path,
                ref_text,
                reference_meta,
                ref_temp,
            ),
            daemon=True,
        ).start()
        return {
            "task_id": task_id,
            "status": "pending",
            "total": len(sentences),
            "sentences": sentences,
        }

    @app.get("/api/batch/tasks/{task_id}")
    async def get_batch_task(task_id: str):
        task = _batch_tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        total = task.get("total") or 0
        done = task.get("done") or 0
        return {
            "task_id": task_id,
            "status": task["status"],
            "source": task.get("source"),
            "total": total,
            "done": done,
            "current": task.get("current") or 0,
            "current_text": task.get("current_text") or "",
            "error": task.get("error"),
            "zip_name": task.get("zip_name"),
            "count": task.get("count"),
            "format": task.get("format"),
            "reference": task.get("reference"),
            "progress": (done / total) if total else None,
        }

    @app.get("/api/batch/tasks/{task_id}/download")
    async def download_batch(task_id: str):
        task = _batch_tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if task["status"] != "done":
            raise HTTPException(status_code=409, detail="任务尚未完成")
        path = task.get("zip_path")
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="文件已清理")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=task.get("zip_name") or "audio8-batch.zip",
        )

    @app.delete("/api/batch/tasks/{task_id}")
    async def delete_batch_task(task_id: str):
        task = _batch_tasks.pop(task_id, None)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        path = task.get("zip_path")
        if path:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        return {"removed": True}

    # ---------- 已保存音色（参考音频 + 参考文本） ----------

    @app.get("/api/voices")
    async def list_voices():
        return {"voices": voice_store.list_voices()}

    @app.post("/api/voices")
    async def create_voice(
        name: str = Form(...),
        ref_text: str = Form(...),
        file: UploadFile = File(...),
    ):
        suffix = Path(file.filename or "reference.wav").suffix or ".wav"
        data = await file.read()
        try:
            return voice_store.save_voice(name, ref_text, data, suffix)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/voices/{voice_id}")
    async def remove_voice(voice_id: str):
        if not voice_store.delete_voice(voice_id):
            raise HTTPException(status_code=404, detail="音色不存在")
        return {"removed": True}

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


def config_from_env() -> ServiceConfig:
    return ServiceConfig(
        model=os.getenv("AUDIO8_MODEL", "preview"),
        backend=os.getenv("AUDIO8_BACKEND", "auto"),
        max_chars=int(os.getenv("AUDIO8_MAX_CHARS", "300")),
        max_concurrency=int(os.getenv("AUDIO8_MAX_CONCURRENCY", "1")),
        temperature=float(os.getenv("AUDIO8_TEMPERATURE", "0.7")),
        top_p=float(os.getenv("AUDIO8_TOP_P", "0.9")),
        top_k=int(os.getenv("AUDIO8_TOP_K", "50")),
        device=os.getenv("AUDIO8_DEVICE"),
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        create_app(config_from_env()),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        workers=1,
    )


if __name__ == "__main__":
    main()
