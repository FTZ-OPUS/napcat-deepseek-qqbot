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
import time

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


def _images_in(message, depth: int = 0) -> list[dict]:
    """从消息段数组里挑出图片段。

    ⚠️ 2026-10-15 加：**支持 forward（合并转发）**。
    真实事故：群友引用了一条**转发消息**让机器人解题，机器人回
    「图片都没收到」—— 因为转发里的图不在 image 段里，
    而在 forward 段的子节点里，必须用 get_forward_msg 展开才看得到。
    群里引用转发是很常见的操作（尤其转来的题目）。
    """
    out = []
    if not isinstance(message, list):
        return out
    for seg in message:
        if not isinstance(seg, dict):
            continue
        _ty = seg.get("type")
        if _ty == "image":
            data = seg.get("data") or {}
            if _looks_like_qq_face(data) or not data.get("url"):
                continue
            out.append(data)
        elif _ty == "forward" and depth < 2:
            fid = (seg.get("data") or {}).get("id")
            if not fid:
                continue
            for node in _forward_nodes(fid):
                nm = node.get("message") or node.get("content")
                out.extend(_images_in(nm, depth + 1))
    return out


def _forward_nodes(forward_id) -> list[dict]:
    """用 get_forward_msg 展开一条合并转发，返回节点列表（失败返回空）。"""
    for _try in range(2):
        try:
            resp = onebot.call("get_forward_msg", id=forward_id)
            data = resp.get("data") or {}
            nodes = data.get("messages") or data.get("message") or []
            if isinstance(nodes, list) and nodes:
                return nodes
        except Exception:
            pass
        time.sleep(0.35)
    return []


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
    """回溯被引用的消息，返回 (文字, 图片段列表)。

    ⚠️ 2026-10-15 加：**空结果重试**。实测 get_msg 偶尔返回 message: []
    （消息刚落库、还没进缓存），隔 0.4 秒再问就有了。
    不重试的话，群友刚发的图会被判成「没收到图片」。
    """
    message = None
    for _try in range(3):
        try:
            resp = onebot.call("get_msg", message_id=message_id)
            payload = resp.get("data") or {}
            message = payload.get("message")
            if isinstance(message, list) and message:
                break
        except Exception:
            message = None
        time.sleep(0.4)
    if not message:
        return "", []
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


def looks_handwritten(raw: bytes) -> bool:
    """粗判：这张图是**拍照的纸面手稿**，还是**干净的截图/印刷题**？

    为什么需要这个判断（车主 2026-10-13 定）：
      群友反映「草稿纸上的手写题」识别难度大，别的机器人（用 Gemini）认得更准，
      人都跑去那边批改草稿了。所以**手写稿走 Gemini 识图，正常题走 DeepSeek**。

    ⚠️ 判错的代价**不对称**：
      · 误判成手写 → 只是多花几秒（走一次 Gemini，实测 8.9s vs 0.8s）
      · 误判成截图 → 手写题可能被读错，**答案就错了**
    所以判断**宁可宽松**。

    ⚠️ 阈值是**实测校准**过的（2026-10-13），不是拍脑袋：
      | 样张            | 纸面亮度 | 对比 | 格式 |
      | 干净渲染图(截图) | 255.0   |  0.0 | PNG  |
      | 白底 PNG        | 255.0   |  0.0 | PNG  |
      | 拍照模拟·中退化  | 243.6   | 20.3 | JPEG |
      | 拍照模拟·强退化  | 233.5   | 25.3 | JPEG |
      两组分得很开 —— **纯白截图纸面恒为 255、对比恒为 0**。
    """
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        fmt = (img.format or "").upper()
        im = img.convert("L")
        if max(im.size) < 60:
            return False
        im.thumbnail((400, 400))          # 小图统计，快很多且够用
        px = im.load()
        w, h = im.size
        vals = sorted(px[x, y] for y in range(0, h, 3) for x in range(0, w, 3))
        if not vals:
            return False
        top = vals[int(len(vals) * 0.9):] or vals[-1:]
        paper = sum(top) / len(top)                     # 纸面（最亮那一批）
        inner = vals[int(len(vals) * 0.2):int(len(vals) * 0.8)] or vals
        mid = sum(inner) / len(inner)                   # 中部亮度
        contrast = paper - mid                          # 纸面 vs 内容的对比
        dark = sum(1 for v in vals if v < 128) / len(vals)

        if dark < 0.002 and contrast < 4:               # 几乎空白，别折腾
            return False
        # ① 纸面不是纯白（拍照纸面偏灰/偏黄，实测 233~244，纯白截图恒 255）
        # ② 纸面与内容有明显对比（光照不均/阴影，实测 >20，纯白截图为 0）
        # ③ 有损格式 + 纸面不白（手机拍照基本走 JPEG）
        if paper < 250 or contrast > 8:
            return True
        if fmt == "JPEG" and paper < 253:
            return True
        return False
    except Exception:
        # 判不出来时**按手写处理**（宁可慢几秒，也不要把手写题读错）
        return True


def any_handwritten(data_urls: list[str]) -> bool:
    """这批图里**有没有手写稿**（有就走 Gemini 识图）。

    data_urls 是 `data:image/...;base64,...` 形式，解出原始字节再判。
    """
    for u in (data_urls or []):
        try:
            b64 = u.split(",", 1)[1] if "," in u else u
            import base64
            if looks_handwritten(base64.b64decode(b64)):
                return True
        except Exception:
            continue
    return False


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
