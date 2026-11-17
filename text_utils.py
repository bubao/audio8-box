from __future__ import annotations

import re

_BOUNDARY_RE = re.compile(r"[。！？!?；;]|\.(?=\s|$)|\n+")


def split_text(text: str, max_chars: int = 300) -> list[str]:
    """把长文本切成适合 TTS 生成的短句，尽量不在句子中间断开。"""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    start = 0
    for match in _BOUNDARY_RE.finditer(text):
        piece = text[start : match.end()]
        if piece.strip():
            pieces.append(piece)
        start = match.end()
    if start < len(text):
        piece = text[start:]
        if piece.strip():
            pieces.append(piece)

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if len(piece) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            for offset in range(0, len(piece), max_chars):
                chunks.append(piece[offset : offset + max_chars])
        elif buf and len(buf) + len(piece) > max_chars:
            chunks.append(buf)
            buf = piece
        else:
            buf += piece

    if buf:
        chunks.append(buf)
    return [chunk for chunk in chunks if chunk.strip()]


def split_sentences(text: str, max_chars: int = 300) -> list[str]:
    """按句子边界拆分，用于批量逐句合成（一句一个音频文件）。

    与 split_text 不同，这里不做短句合并：返回的每一项对应一个自然句，
    只有超过 max_chars 的超长句才会被硬切，避免单条音频过长。
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    text = text.strip()
    if not text:
        return []

    pieces: list[str] = []
    start = 0
    for match in _BOUNDARY_RE.finditer(text):
        piece = text[start : match.end()].strip()
        if piece:
            pieces.append(piece)
        start = match.end()
    if start < len(text):
        piece = text[start:].strip()
        if piece:
            pieces.append(piece)

    chunks: list[str] = []
    for piece in pieces:
        if len(piece) <= max_chars:
            chunks.append(piece)
        else:
            for offset in range(0, len(piece), max_chars):
                chunk = piece[offset : offset + max_chars].strip()
                if chunk:
                    chunks.append(chunk)
    return chunks
