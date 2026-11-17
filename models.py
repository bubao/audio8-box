"""Audio8Box 模型目录与缓存管理。

支持两个下载源：

- ``hf``：Hugging Face，使用 ``huggingface_hub``，缓存位于 ``HF_HUB_CACHE``
  （默认 ``~/.cache/huggingface/hub``）。
- ``modelscope``：ModelScope，使用 ``modelscope`` SDK，缓存位于 ``MODELSCOPE_CACHE``
  （默认 ``~/.cache/modelscope``）。

用法：

  uv run models.py list                      # 列出内置模型及每个源的安装状态
  uv run models.py install preview           # 用默认源下载
  uv run models.py install realistic --source modelscope
  uv run models.py path preview              # 打印模型快照路径
  uv run models.py remove preview            # 删除模型缓存（交互确认，-y 跳过）

install / path / remove 也接受任意 ``owner/name`` 仓库 ID（默认走 HF）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import huggingface_hub.constants as hf_constants
from huggingface_hub import scan_cache_dir, snapshot_download

SOURCES = ("hf", "modelscope")
SOURCE_LABELS = {"hf": "Hugging Face", "modelscope": "ModelScope"}

# 内置模型目录。每个模型可挂多个下载源；default_source 为 CLI/Web 的默认选择。
# backend 用于服务选择推理后端；ignore_patterns 仅在 ModelScope 下载时生效。
MODEL_CATALOG: dict[str, dict] = {
    "preview": {
        "sources": {
            "hf": "mlx-community/Audio8-TTS-Preview-0.6b-bf16",
            "modelscope": "mlx-community/Audio8-TTS-Preview-0.6b-bf16",
        },
        "default_source": "hf",
        "backend": "mlx",
        "languages": 11,
        "note": "紧凑快速，中文表现稳定",
    },
    "realistic": {
        "sources": {
            "hf": "mlx-community/Audio8-TTS-Realistic-0.6b-bf16",
            "modelscope": "mlx-community/Audio8-TTS-Realistic-0.6b-bf16",
        },
        "default_source": "hf",
        "backend": "mlx",
        "languages": 23,
        "note": "语言覆盖广，语速偏慢",
    },
    "realistic-air": {
        "sources": {
            "modelscope": "Edge0/Audio8-TTS-Realistic-0.6b",
        },
        "default_source": "modelscope",
        "backend": "pytorch",
        "languages": 23,
        "note": "Transformers/PyTorch 权重，需 pytorch 后端；已排除 llama.cpp GGUF",
        "ignore_patterns": ["llama-cpp/*"],
    },
}

MODEL_INFO = {
    name: {"languages": spec["languages"], "note": spec["note"]}
    for name, spec in MODEL_CATALOG.items()
}

# 兼容旧调用：name -> 默认源的仓库 ID。
MODEL_IDS = {
    name: spec["sources"][spec["default_source"]]
    for name, spec in MODEL_CATALOG.items()
}


def hf_cache_dir() -> Path:
    return Path(hf_constants.HF_HUB_CACHE)


def modelscope_cache_dir() -> Path:
    from modelscope_hub.config import get_cache_root

    return Path(get_cache_root())


def cache_dir(source: str) -> Path:
    if source == "hf":
        return hf_cache_dir()
    if source == "modelscope":
        return modelscope_cache_dir()
    raise ValueError(f"unknown source: {source}")


def resolve_model_ref(name: str, source: str | None = None) -> tuple[str, str]:
    """把模型名或 ``owner/name`` 解析为 ``(source, model_id)``。"""
    if source is not None and source not in SOURCES:
        raise SystemExit(f"未知下载源：{source}（可选：{', '.join(SOURCES)}）")

    if name in MODEL_CATALOG:
        spec = MODEL_CATALOG[name]
        chosen = source or spec["default_source"]
        if chosen not in spec["sources"]:
            raise SystemExit(
                f"模型 {name} 不支持源 {chosen}，可选：{', '.join(spec['sources'])}"
            )
        return chosen, spec["sources"][chosen]

    if "/" in name:
        return source or "hf", name

    raise SystemExit(
        f"未知模型：{name}（内置：{', '.join(MODEL_CATALOG)}，或传入 owner/name 仓库 ID）"
    )


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def scan_hf_cache() -> dict[str, dict]:
    """扫描 HF 缓存，返回 {repo_id: {path, size, files}}。"""
    result: dict[str, dict] = {}
    try:
        info = scan_cache_dir()
    except Exception as exc:  # 缓存损坏或不可读时不应阻断管理操作
        print(f"警告：扫描 HF 缓存失败：{exc}", file=sys.stderr)
        return result
    for repo in info.repos:
        result[repo.repo_id] = {
            "path": Path(repo.repo_path),
            "size": repo.size_on_disk,
            "files": repo.nb_files,
        }
    return result


def scan_modelscope_cache() -> dict[str, dict]:
    """扫描 ModelScope 缓存，返回与 HF 相同的结构。"""
    result: dict[str, dict] = {}
    try:
        from modelscope_hub._cache_manager import scan_cache

        info = scan_cache()
    except Exception as exc:
        print(f"警告：扫描 ModelScope 缓存失败：{exc}", file=sys.stderr)
        return result
    for repo in info.repos:
        repo_type = getattr(repo, "repo_type", None)
        type_value = getattr(repo_type, "value", repo_type)
        if type_value not in (None, "model"):
            continue
        if not repo.local_path:
            continue
        result[repo.repo_id] = {
            "path": Path(repo.local_path),
            "size": repo.size_on_disk,
            "files": repo.nb_files,
        }
    return result


def scan_cache(source: str) -> dict[str, dict]:
    if source == "hf":
        return scan_hf_cache()
    if source == "modelscope":
        return scan_modelscope_cache()
    raise ValueError(f"unknown source: {source}")


def snapshot_of(repo_path: Path) -> Path | None:
    """返回该仓库最新快照目录（无快照则 None）。"""
    snapshots = repo_path / "snapshots"
    if not snapshots.is_dir():
        return None
    dirs = [p for p in snapshots.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def is_installed(entry: dict) -> tuple[bool, Path | None]:
    """判断权重是否完整：快照内存在 safetensors 权重文件。"""
    snap = snapshot_of(entry["path"])
    if snap and any(snap.glob("*.safetensors")):
        return True, snap
    return False, snap


def find_installed(source: str, model_id: str) -> tuple[bool, Path | None, Path | None]:
    """返回 (是否完整, 仓库根路径, 快照路径)。"""
    entry = scan_cache(source).get(model_id)
    if not entry:
        return False, None, None
    installed, snap = is_installed(entry)
    return installed, entry["path"], snap


def resolve_model_path(name: str) -> Path | None:
    """服务加载用：返回已安装模型的本地快照路径（优先默认源）。

    未安装或未收录在目录中的名称返回 None。
    """
    spec = MODEL_CATALOG.get(name)
    if not spec:
        return None
    order = [spec["default_source"]] + [
        source for source in spec["sources"] if source != spec["default_source"]
    ]
    for source in order:
        installed, _, snap = find_installed(source, spec["sources"][source])
        if installed and snap:
            return snap
    return None


def download_model(
    source: str, model_id: str, ignore_patterns: list[str] | None = None
) -> Path:
    """下载整仓库快照并返回本地快照目录。"""
    if source == "hf":
        return Path(snapshot_download(repo_id=model_id))
    if source == "modelscope":
        from modelscope.hub.snapshot_download import snapshot_download as ms_download

        kwargs: dict = {}
        if ignore_patterns:
            kwargs["ignore_file_pattern"] = list(ignore_patterns)
        return Path(ms_download(model_id, **kwargs))
    raise ValueError(f"unknown source: {source}")


def _status_text(entry: dict | None) -> tuple[str, str]:
    if not entry:
        return "未安装", "-"
    installed, _ = is_installed(entry)
    return ("已安装" if installed else "不完整"), human_size(entry["size"])


def cmd_list(_args: argparse.Namespace) -> None:
    caches = {"hf": scan_hf_cache(), "modelscope": scan_modelscope_cache()}
    print(f"HF 缓存：{hf_cache_dir()}")
    print(f"ModelScope 缓存：{modelscope_cache_dir()}\n")
    print(f"{'名称':<14}{'源':<13}{'状态':<10}{'大小':>10}  仓库")
    print("-" * 84)
    for name, spec in MODEL_CATALOG.items():
        info = MODEL_INFO.get(name, {})
        for index, (source, model_id) in enumerate(spec["sources"].items()):
            status, size = _status_text(caches[source].get(model_id))
            note = info.get("note", "") if index == 0 else ""
            print(
                f"{name:<14}{SOURCE_LABELS[source]:<13}{status:<10}{size:>10}  "
                f"{model_id}  {note}"
            )


def cmd_install(args: argparse.Namespace) -> None:
    if not args.model:
        print("可安装的内置模型（默认源 | 全部源 | 说明）：")
        for name, spec in MODEL_CATALOG.items():
            sources = ", ".join(
                f"{source}={model_id}" for source, model_id in spec["sources"].items()
            )
            print(
                f"  {name:<14} 默认 {spec['default_source']:<11} | {sources} | "
                f"{spec.get('note', '')}"
            )
        raise SystemExit("请指定模型名称或 owner/name 仓库 ID，例如：uv run models.py install preview")

    source, model_id = resolve_model_ref(args.model, args.source)
    spec = MODEL_CATALOG.get(args.model) or {}
    ignore_patterns = spec.get("ignore_patterns") if source == "modelscope" else None
    print(f"模型：{model_id}")
    print(f"来源：{SOURCE_LABELS[source]}")
    print(f"缓存：{cache_dir(source)}")
    path = download_model(source, model_id, ignore_patterns=ignore_patterns)
    print(f"安装完成：{path}")


def cmd_path(args: argparse.Namespace) -> None:
    source, model_id = resolve_model_ref(args.model, args.source)
    entry = scan_cache(source).get(model_id)
    if not entry:
        raise SystemExit(f"未安装：{model_id}（先运行 install --source {source}）")
    snap = snapshot_of(entry["path"])
    if not snap:
        raise SystemExit(f"未找到快照：{model_id}")
    print(snap)


def cmd_remove(args: argparse.Namespace) -> None:
    source, model_id = resolve_model_ref(args.model, args.source)
    entry = scan_cache(source).get(model_id)
    if not entry:
        raise SystemExit(f"未安装：{model_id}")
    target: Path = entry["path"]
    if not args.yes:
        answer = input(
            f"确认删除 {target}（{human_size(entry['size'])}）？此操作不可恢复 [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("已取消")
            return
    shutil.rmtree(target)
    print(f"已删除：{target}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="models.py", description="Audio8Box 模型管理工具（HF / ModelScope）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出内置模型及每个源的安装状态")
    p_list.set_defaults(func=cmd_list)

    p_install = sub.add_parser("install", help="下载模型权重到对应源缓存")
    p_install.add_argument("model", nargs="?", help="preview / realistic / realistic-air / owner/name")
    p_install.add_argument(
        "--source", choices=SOURCES, default=None, help="下载源（默认取模型默认源）"
    )
    p_install.set_defaults(func=cmd_install)

    p_path = sub.add_parser("path", help="打印模型快照路径")
    p_path.add_argument("model", help="preview / realistic / realistic-air / owner/name")
    p_path.add_argument(
        "--source", choices=SOURCES, default=None, help="下载源（默认取模型默认源）"
    )
    p_path.set_defaults(func=cmd_path)

    p_remove = sub.add_parser("remove", help="删除模型缓存")
    p_remove.add_argument("model", help="preview / realistic / realistic-air / owner/name")
    p_remove.add_argument(
        "--source", choices=SOURCES, default=None, help="下载源（默认取模型默认源）"
    )
    p_remove.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    p_remove.set_defaults(func=cmd_remove)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
