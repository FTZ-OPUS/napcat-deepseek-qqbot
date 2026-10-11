"""OneBot 11 HTTP 客户端 —— 只依赖 requests。

NapCat 的 HTTP 服务监听在 127.0.0.1:3000，带 token 鉴权。
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import requests

import config


class OneBotError(RuntimeError):
    pass


def call(action: str, timeout: int = 30, **params: Any) -> dict:
    """调用任意 OneBot 动作。失败抛 OneBotError。"""
    headers = {"Content-Type": "application/json"}
    if config.ONEBOT_TOKEN:
        headers["Authorization"] = f"Bearer {config.ONEBOT_TOKEN}"

    try:
        resp = requests.post(
            f"{config.ONEBOT_API}/{action}",
            headers=headers,
            json=params,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OneBotError(f"{action} 网络错误: {exc}") from exc

    if resp.status_code != 200:
        raise OneBotError(f"{action} HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    if data.get("status") != "ok" and data.get("retcode") not in (0, 1):
        raise OneBotError(f"{action} 返回失败: {str(data)[:200]}")
    return data


# ---------------- 消息解析 ----------------

def message_text(message: Any) -> str:
    """把消息段数组拼成纯文本（图片/表情用占位符表示）。"""
    if isinstance(message, str):
        return message
    out = []
    for seg in message or []:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("type")
        data = seg.get("data") or {}
        if kind == "text":
            out.append(str(data.get("text", "")))
        elif kind == "at":
            qq = str(data.get("qq", ""))
            out.append("@全体成员" if qq == "all" else f"@{qq}")
        elif kind == "image":
            out.append("[图片]")
        elif kind == "face":
            out.append("[表情]")
        elif kind == "reply":
            out.append("")
        elif kind == "file":
            out.append(f"[文件:{data.get('name','')}]")
    return "".join(out).strip()


def at_targets(message: Any) -> list[str]:
    """取出这条消息 @ 了谁。"""
    if not isinstance(message, list):
        return []
    return [
        str((seg.get("data") or {}).get("qq", ""))
        for seg in message
        if isinstance(seg, dict) and seg.get("type") == "at"
    ]


# ---------------- 发送 ----------------

def _send(kind: str, target_id: int, message: list) -> dict:
    """统一发送入口。kind 为 group / private。"""
    if kind == "group":
        return call("send_group_msg", group_id=target_id, message=message)
    return call("send_private_msg", user_id=target_id, message=message)


def _image_ok(path: Path) -> dict:
    return {"type": "image", "data": {"file": f"file://{path.resolve()}"}}


def _image_b64(path: Path) -> dict:
    return {
        "type": "image",
        "data": {"file": "base64://" + base64.b64encode(path.read_bytes()).decode()},
    }


def _media_ok(media_type: str, path: Path) -> dict:
    return {"type": media_type, "data": {"file": f"file://{path.resolve()}"}}


def _media_b64(media_type: str, path: Path) -> dict:
    return {
        "type": media_type,
        "data": {"file": "base64://" + base64.b64encode(path.read_bytes()).decode()},
    }


def _send_media(kind: str, target_id: int, path: Path, media_type: str) -> dict:
    """发图片/视频：优先 file:// 本地路径，失败退回 base64。

    开 enableLocalFile2Url 时 file:// 就能用；base64 是兜底，
    防止以后有人把那个开关关掉就发不出图了。
    """
    try:
        return _send(kind, target_id, [_media_ok(media_type, path)])
    except OneBotError:
        return _send(kind, target_id, [_media_b64(media_type, path)])


def _send_image(kind: str, target_id: int, path: Path) -> dict:
    return _send_media(kind, target_id, path, "image")


def send_group_text(group_id: int, text: str) -> dict:
    return _send("group", group_id, [{"type": "text", "data": {"text": text}}])


def send_group_at(group_id: int, qq, text: str) -> dict:
    """@ 某个群友 + 说一句话（用于做完文件后的收尾通知）。"""
    return _send("group", group_id, [
        {"type": "at", "data": {"qq": str(qq)}},
        {"type": "text", "data": {"text": " " + text}},
    ])


def send_private_text(user_id: int, text: str) -> dict:
    return _send("private", user_id, [{"type": "text", "data": {"text": text}}])


def send_group_image(group_id: int, path: Path) -> dict:
    return _send_image("group", group_id, path)


def send_group_video(group_id: int, path: Path) -> dict:
    return _send_media("group", group_id, path, "video")


def send_private_video(user_id: int, path: Path) -> dict:
    return _send_media("private", user_id, path, "video")


def send_private_image(user_id: int, path: Path) -> dict:
    return _send_image("private", user_id, path)


def send_group_file(group_id: int, path: Path, name: str | None = None) -> dict:
    """发文件（PDF 走这里）。"""
    path = path.resolve()
    name = name or path.name
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        return _send("group", group_id, [
            {"type": "file", "data": {"file": f"file://{path}", "name": name}}
        ])
    except OneBotError:
        # 退回群文件上传接口
        return call("upload_group_file", group_id=group_id, file=str(path), name=name)


def send_private_file(user_id: int, path: Path, name: str | None = None) -> dict:
    path = path.resolve()
    name = name or path.name
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return _send("private", user_id, [
        {"type": "file", "data": {"file": f"file://{path}", "name": name}}
    ])


def get_group_msg_history(group_id: int, count: int = 30) -> list[dict]:
    data = call("get_group_msg_history", group_id=group_id, count=count)
    payload = data.get("data") or {}
    return payload.get("messages") or []


def get_msg(message_id) -> dict:
    """按 message_id 取单条消息（reply 引用回溯用）。"""
    return call("get_msg", message_id=message_id).get("data") or {}


def get_file(file_id: str) -> dict:
    """按 file_id 取文件（NapCat 会下载到本地，返回本地路径等信息）。"""
    return call("get_file", file_id=file_id).get("data") or {}


def get_friend_msg_history(user_id: int, count: int = 15) -> list[dict]:
    """拉好友私信历史（NapCat 扩展接口）。失败抛 OneBotError，由调用方兜底。"""
    data = call("get_friend_msg_history", user_id=user_id, count=count)
    payload = data.get("data")
    if isinstance(payload, list):
        return payload
    return (payload or {}).get("messages") or []


def download_file(url: str, dest, timeout: int = 90,
                  max_bytes: int = 25 * 1024 * 1024) -> None:
    """把 QQ 群文件的下载 URL 拉到本地（流式 + 大小护栏）。

    群文件消息自带 url（QQ 网盘中转直链，无需鉴权）。
    超过 max_bytes 视为异常文件，拒绝落盘 —— 防止有人拿大文件刷磁盘。
    """
    dest = Path(dest)
    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise OneBotError(
                            f"文件超过 {max_bytes // 1024 // 1024}MB 上限，拒绝下载")
                    fh.write(chunk)
    except requests.RequestException as exc:
        dest.unlink(missing_ok=True)
        raise OneBotError(f"文件下载失败: {exc}") from exc


def get_login_info() -> dict:
    return call("get_login_info").get("data") or {}
