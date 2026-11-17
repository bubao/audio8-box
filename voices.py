"""已保存音色（参考音频 + 参考文本）的本地存储。

音色库位于 ``AUDIO8_VOICES_DIR``（默认 ``~/.audio8/voices``）：每个音色由
``<id>.<ext>`` 参考音频文件与 ``voices.json`` 索引组成，供合成 / 克隆 / 批量
任务通过 ``voice`` 字段直接引用，无需每次重新上传。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

INDEX_NAME = "voices.json"
ALLOWED_SUFFIXES = (".wav", ".mp3", ".ogg", ".flac", ".m4a")
MAX_NAME_CHARS = 60
MAX_REF_TEXT_CHARS = 2000


def voices_dir() -> Path:
    """音色库目录（不存在时由保存操作创建）。"""
    return Path(os.getenv("AUDIO8_VOICES_DIR", str(Path.home() / ".audio8" / "voices")))


def _index_path() -> Path:
    return voices_dir() / INDEX_NAME


def _load_index() -> list[dict]:
    path = _index_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _save_index(items: list[dict]) -> None:
    directory = voices_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _index_path().write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())[:MAX_NAME_CHARS]


def normalize_suffix(suffix: str | None) -> str:
    """校验并返回参考音频后缀（白名单）。"""
    suffix = (suffix or "").lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"不支持的参考音频格式：{suffix or '未知'}")
    return suffix


def list_voices() -> list[dict]:
    """列出全部音色；``exists`` 标记参考音频文件是否仍在。"""
    voices = []
    for item in _load_index():
        voice_id = str(item.get("id") or "")
        file_name = str(item.get("file") or "")
        if not voice_id or not file_name:
            continue
        voices.append({**item, "exists": (voices_dir() / file_name).exists()})
    return voices


def get_voice(voice_id: str) -> dict | None:
    for item in list_voices():
        if item.get("id") == voice_id:
            return item
    return None


def save_voice(name: str, ref_text: str, data: bytes, suffix: str | None) -> dict:
    """保存音色：写入参考音频并更新索引，返回音色记录。"""
    cleaned = _clean_name(name)
    if not cleaned:
        raise ValueError("音色名称不能为空")
    text = (ref_text or "").strip()
    if not text:
        raise ValueError("参考文本不能为空")
    if len(text) > MAX_REF_TEXT_CHARS:
        raise ValueError(f"参考文本过长（上限 {MAX_REF_TEXT_CHARS} 字）")
    if not data:
        raise ValueError("参考音频为空")
    ext = normalize_suffix(suffix)

    voice_id = uuid.uuid4().hex[:12]
    file_name = f"{voice_id}{ext}"
    directory = voices_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / file_name).write_bytes(data)

    item = {
        "id": voice_id,
        "name": cleaned,
        "ref_text": text,
        "file": file_name,
        "size": len(data),
        "created_at": int(time.time()),
    }
    items = [x for x in _load_index() if x.get("id") != voice_id]
    items.append(item)
    _save_index(items)
    return item


def delete_voice(voice_id: str) -> bool:
    """删除音色及其参考音频；不存在时返回 False。"""
    items = _load_index()
    target = next((x for x in items if x.get("id") == voice_id), None)
    if target is None:
        return False
    file_name = str(target.get("file") or "")
    if file_name:
        try:
            (voices_dir() / file_name).unlink()
        except OSError:
            pass
    _save_index([x for x in items if x.get("id") != voice_id])
    return True
