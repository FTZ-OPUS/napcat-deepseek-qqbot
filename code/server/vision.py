"""图片收集与预处理 —— 让机器人真正「看得见」。

背景：之前的 message_text() 把图片段直接替换成 "[图片]" 三个字，
模型收到的永远只有这三个字，压根没有图。群友发图 @ 它要求批改，
它只能答非所问。这个模块把图真正取出来送进多模态请求。

为什么不用 NapCat 给的本地路径：
    get_image 返回的本地文件在 /root/.config/QQ/... 下，
    /root 权限是 700、文件是 600，而机器人跑在 qqbot 用户下，读不到。
    所以走它同时返回的 CDN url —— 实测无需额外鉴权，直接能下。

成本控制（图片很吃 token）：
    - 一条消息最多带 MAX_IMAGES 张
    - 长边超过 MAX_LONG_SIDE 先缩小
    - 单张超过 MAX_DOWNLOAD 直接放弃
"""
from __future__ import annotations

import base64
import io
import re

import requests

import config
import onebot

# 这两个上限从 config 读，方便在 .env 里调而不用改代码。
# 默认 5 张 / 长边 1536px —— 额度充足时给足画质，密集的题目截图看得更清。
MAX_IMAGES = config.VISION_MAX_IMAGES
MAX_LONG_SIDE = config.VISION_MAX_LONG_SIDE
MAX_DOWNLOAD = 24 * 1024 * 1024

# QQ 自带表情：[发怒] 这种 summary 就是表情名，没有信息量，不值得占一个视觉名额
_QQ_FACE_RE = re.compile(r"^\[.{1,8}\]$")


def _looks_like_qq_face(data: dict) -> bool:
    if data.get("sub_type") == 7:
        return True
    return bool(_QQ_FACE_RE.match(str(data.get("summary", "")).strip()))


def _images_in(message) -> list[dict]:
    """从消息段数组里挑出图片段。"""
    out = []
    if not isinstance(message, list):
        return out
    for seg in message:
        if not isinstance(seg, dict) or seg.get("type") != "image":
            continue
        data = seg.get("data") or {}
        if _looks_like_qq_face(data) or not data.get("url"):
            continue
        out.append(data)
    return out


def extract(message) -> tuple[str, list[str]]:
    """把一条消息解析成 (被引用消息的文字, 图片 data-url 列表)。

    处理三件事：
      1. 消息本身的图片
      2. reply 段引用的那条消息 —— 回溯它，取里面的文字和图片
         （群友常见操作：发张图 → 引用那张图 @ 机器人 让它批改）
      3. 引用消息的文字，拼进上下文，方便模型理解在说什么
    """
    quoted_text = ""
    raw_images: list[dict] = []

    if isinstance(message, list):
        for seg in message:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "reply":
                target = (seg.get("data") or {}).get("id")
                if target:
                    q_text, q_images = _fetch_quoted(target)
                    quoted_text = q_text
                    raw_images.extend(q_images)

    raw_images.extend(_images_in(message))

    data_urls = []
    for data in raw_images[:MAX_IMAGES]:
        url = data.get("url")
        if not url:
            continue
        got = _download(url)
        if got:
            data_urls.append(_to_data_url(got))
    return quoted_text, data_urls


def _fetch_quoted(message_id) -> tuple[str, list[dict]]:
    """回溯被引用的消息，返回 (文字, 图片段列表)。"""
    try:
        resp = onebot.call("get_msg", message_id=message_id)
    except Exception:
        return "", []
    payload = resp.get("data") or {}
    message = payload.get("message")
    text = onebot.message_text(message)
    return text, _images_in(message)


def _download(url: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    raw = resp.content
    if not raw or len(raw) > MAX_DOWNLOAD:
        return None
    return raw


def _to_data_url(raw: bytes) -> str:
    """压到合适尺寸再转 base64，控制 token 开销。"""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im = im.convert("RGB")
        if max(im.size) > MAX_LONG_SIDE:
            im.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        raw = buf.getvalue()
    except Exception:
        # 装没装 Pillow 都能跑：处理失败就用原图
        pass
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/qqbot/qq-bot")
    import config

    msgs = onebot.get_group_msg_history(config.ALLOWED_GROUPS[0], count=40)
    print("扫描最近 40 条消息，看图片能不能取到：")
    found = 0
    for m in msgs:
        segs = m.get("message")
        if not isinstance(segs, list):
            continue
        if not any(isinstance(s, dict) and s.get("type") in ("image", "reply") for s in segs):
            continue
        text, urls = extract(segs)
        if urls:
            found += 1
            print(f"  message_id={m.get('message_id')}  "
                  f"引用文字={text[:30]!r}  取到 {len(urls)} 张图，"
                  f"首张 base64 长度 {len(urls[0])}")
        if found >= 4:
            break
    if not found:
        print("  （最近没有能取到图的消息）")
