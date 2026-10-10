"""表情包 / 短视频素材库。

目录结构：
    stickers/            图片（jpg / png / gif / webp）
    stickers/videos/     视频（mp4 / mov / webm）

轮换策略：**轮流发，不重样**。游标存在 state/media_cursor.json，
所以重启服务不会每次从头开始，也不会连着发同一张。

加新素材：直接把文件丢进对应目录就行，不用改代码，也不用重启。
    scp 新表情包.jpg root@服务器:/home/qqbot/qq-bot/stickers/
    scp 新视频.mp4   root@服务器:/home/qqbot/qq-bot/stickers/videos/

看看现在有哪些素材：
    sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python /home/qqbot/qq-bot/media.py
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import config

STICKER_DIR = config.BASE / "stickers"
VIDEO_DIR = STICKER_DIR / "videos"
CURSOR_FILE = config.STATE_DIR / "media_cursor.json"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}


def list_images() -> list[Path]:
    if not STICKER_DIR.is_dir():
        return []
    return sorted(
        p for p in STICKER_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXT
    )


def list_videos() -> list[Path]:
    if not VIDEO_DIR.is_dir():
        return []
    return sorted(
        p for p in VIDEO_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXT
    )


def _load_cursor() -> dict:
    try:
        return json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cursor(data: dict) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CURSOR_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CURSOR_FILE)


def pick(kind: str = "image") -> Path | None:
    """按游标轮流取一个素材；库是空的就返回 None。

    对素材总数取模，所以中途加/删素材也不会越界。
    """
    pool = list_images() if kind == "image" else list_videos()
    if not pool:
        return None

    cursor = _load_cursor()
    key = f"{kind}_idx"
    idx = int(cursor.get(key, 0)) % len(pool)

    cursor[key] = (idx + 1) % len(pool)
    cursor[f"{kind}_last"] = pool[idx].name
    cursor[f"{kind}_at"] = int(time.time())
    _save_cursor(cursor)
    return pool[idx]


def pick_random(kind: str = "image") -> Path | None:
    """随机抽一个，不走轮换。"""
    pool = list_images() if kind == "image" else list_videos()
    return random.choice(pool) if pool else None


def stats() -> dict:
    cursor = _load_cursor()
    return {
        "images": list_images(),
        "videos": list_videos(),
        "last_image": cursor.get("image_last"),
        "last_video": cursor.get("video_last"),
    }


if __name__ == "__main__":
    s = stats()
    print("=" * 56)
    print(f"  图片 {len(s['images'])} 张    视频 {len(s['videos'])} 个")
    print(f"  上次发的图 : {s['last_image'] or '（还没发过）'}")
    print(f"  上次发的视频: {s['last_video'] or '（还没发过）'}")
    print("=" * 56)
    for p in s["images"]:
        print(f"  [图] {p.name}")
    for p in s["videos"]:
        print(f"  [视频] {p.name}")
    if not s["images"] and not s["videos"]:
        print("  （素材库是空的）")
