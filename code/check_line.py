#!/usr/bin/env python3
"""check_line.py —— 新线路接入前的**兼容性体检**。

**为什么写这个**：车主给了一个中转站的 key，但那个账户余额为 0。
余额为 0 时**光知道"key 有效"根本不够** —— 真正的风险在充值之后：
模型 id 对不对、输出格式跟不跟得上、速度行不行、支不支持我要的功能。
所以「先充最小额 → 跑这个脚本 → 全过再充够」，避免钱白花。

**测什么**（每一项都对应机器人真实依赖的能力）：
  ① 连通性 + 认证
  ② /v1/models 能不能列（顺便找出所有可用模型 id）
  ③ 基础对话能不能跑通
  ④ **响应速度**（跑 3 次取中位数 —— 机器人主打"回复快"）
  ⑤ **JSON 模式**（`response_format={"type":"json_object"}`，brain.decide 依赖它）
  ⑥ **大 max_tokens**（出 PDF 时要 16000，被截断就废了）
  ⑦ **图片输入**（识图批改要发 base64 图片）
  ⑧ **传 `thinking` 字段会不会 400**（DeepSeek 推理模型要用它关思考）

用法：
    python3 check_line.py                     # 用 .env 里的 FALLBACK9_*
    python3 check_line.py <base_url> <key>    # 手动指定
"""
from __future__ import annotations

import base64
import json
import statistics
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent


def load_env(name: str) -> str:
    """从 .env 读一个值（不依赖 python-dotenv）。"""
    for cand in (HERE / ".env", HERE.parent / ".env", HERE.parent / "server" / ".env"):
        if not cand.exists():
            continue
        for line in cand.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


BASE = (sys.argv[1] if len(sys.argv) > 2 else load_env("FALLBACK9_BASE_URL")
        or "https://api.jieyouai.it.com/v1").rstrip("/")
KEY = (sys.argv[2] if len(sys.argv) > 2 else load_env("FALLBACK9_API_KEY")).strip()

OK, BAD, WARN = "✅", "🔴", "⚠️ "
results: list[tuple[str, str, str]] = []

# ⚠️ 「某个模型不可用」和「线路有硬伤」是两回事 —— 一个中转站有几十个模型，
#    其中一半跑不通很正常（下架了、要加钱、要特殊参数）。**不能算成线路缺陷**，
#    否则结论会变成"有 5 项硬伤，先别充钱"，把人吓跑但其实是误报。
#    （真实踩坑 2026-10-14：第一次跑就误报了 5 项。）
INFO = "ℹ️  "


def mark(name: str, state: str, detail: str = "", count: bool = True) -> None:
    if count:
        results.append((name, state, detail))
    print(f"  {state} {name}" + (f"   {detail}" if detail else ""))


def _content(resp) -> str:
    """安全取 choices[0].message.content —— 有些站返回的 choices 里是 null，
    直接下标会抛 TypeError（真实踩坑 2026-10-14，一跑就崩 5 个模型）。"""
    try:
        ch = (resp.json() or {}).get("choices") or []
        if not ch or not isinstance(ch[0], dict):
            return ""
        msg = ch[0].get("message") or {}
        return (msg.get("content") or "") if isinstance(msg, dict) else ""
    except Exception:
        return ""


def post(payload: dict, timeout: int = 120):
    return requests.post(f"{BASE}/chat/completions",
                         headers={"Authorization": f"Bearer {KEY}",
                                  "Content-Type": "application/json"},
                         json=payload, timeout=timeout)


def main() -> None:
    print("═" * 72)
    print(f"  线路体检 · {BASE}")
    print(f"  key: {KEY[:10]}...{KEY[-6:]}（{len(KEY)} 字符）")
    print("═" * 72)
    print()

    if not KEY:
        print("  🔴 没有 key。用法：check_line.py <base_url> <key>")
        return

    # ① 连通性 + 认证
    print("① 连通性 / 认证")
    try:
        r = requests.get(f"{BASE}/models", headers={"Authorization": f"Bearer {KEY}"}, timeout=20)
        if r.status_code == 200:
            mark("认证", OK, f"HTTP 200")
        elif r.status_code == 401:
            mark("认证", BAD, "HTTP 401 —— key 本身无效，别再充钱")
            return
        elif r.status_code == 403:
            body = r.text[:80]
            mark("认证", WARN, f"HTTP 403（key 有效但余额不足）{body}")
            print("\n  ⚠️  余额不足 —— 充最小额后重跑本脚本，才能测出下面这些项")
        else:
            mark("认证", WARN, f"HTTP {r.status_code} {r.text[:70]}")
    except Exception as exc:
        mark("连接", BAD, f"{exc!r}")
        return

    # ② 列模型
    print("\n② 可用模型")
    candidates: list[str] = []
    try:
        r = requests.get(f"{BASE}/models", headers={"Authorization": f"Bearer {KEY}"}, timeout=25)
        if r.status_code == 200:
            d = r.json()
            ids = [m.get("id", "") for m in (d.get("data") or d.get("models") or [])]
            ids = [i for i in ids if i]
            if ids:
                mark("列模型", OK, f"{len(ids)} 个")
                for i in sorted(ids)[:40]:
                    print(f"       {i}")
                candidates = ids
            else:
                mark("列模型", WARN, "返回 200 但没有模型列表")
        else:
            mark("列模型", WARN, f"HTTP {r.status_code}（有些站不开放这个接口）")
    except Exception as exc:
        mark("列模型", WARN, f"{exc!r}")

    if not candidates:
        candidates = ["deepseek-chat", "deepseek-v3", "gpt-4o-mini", "deepseek-4.1-flash"]

    # ③ 找出能跑的模型
    print("\n③ 基础对话（逐个试）")
    good: list[str] = []
    for m in candidates[:12]:
        try:
            t0 = time.time()
            r = post({"model": m, "max_tokens": 16,
                      "messages": [{"role": "user", "content": "只回两个字：可用"}]}, timeout=60)
            dt = time.time() - t0
            if r.status_code == 200:
                txt = _content(r)
                mark(f"对话 {m}", OK, f"{dt:.1f}s → {txt[:24]!r}", count=False)
                good.append(m)
            else:
                try:
                    msg = (r.json().get("error") or {}).get("message", "")[:52]
                except Exception:
                    msg = r.text[:52]
                # 单个模型不可用 → 只提示，不计入结论
                mark(f"对话 {m}", INFO, f"HTTP {r.status_code} {msg}", count=False)
        except Exception as exc:
            mark(f"对话 {m}", INFO, f"{exc!r}", count=False)

    if not good:
        print("\n  🔴 没有一个模型能跑通 —— 先解决余额或模型 id 的问题")
        summary()
        return

    M = good[0]
    print(f"\n  → 后面用 **{M}** 测（第一个跑通的）")

    # ④ 速度
    print("\n④ 响应速度（跑 3 次取中位数，机器人主打「回复快」）")
    ts = []
    for _ in range(3):
        try:
            t0 = time.time()
            r = post({"model": M, "max_tokens": 64,
                      "messages": [{"role": "user", "content": "用一句话说明什么是导数。"}]}, timeout=90)
            if r.status_code == 200:
                ts.append(time.time() - t0)
        except Exception:
            pass
    if ts:
        med = statistics.median(ts)
        state = OK if med < 3 else (WARN if med < 8 else BAD)
        mark("速度", state, f"中位数 {med:.2f}s（最快 {min(ts):.2f}s 最慢 {max(ts):.2f}s）")
    else:
        mark("速度", BAD, "3 次都没成功")

    # ⑤ JSON 模式
    print("\n⑤ JSON 模式（brain.decide 依赖）")
    try:
        r = post({"model": M, "max_tokens": 200,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "user",
                                "content": '严格输出 JSON：{"ok":true,"n":3}'}]}, timeout=90)
        if r.status_code == 200:
            txt = _content(r)
            try:
                json.loads(txt)
                mark("JSON 模式", OK, "能解析")
            except Exception:
                mark("JSON 模式", WARN, f"返回的不是合法 JSON：{txt[:50]!r}")
        else:
            mark("JSON 模式", BAD, f"HTTP {r.status_code}（机器人会退化成纯文本解析，风险中等）")
    except Exception as exc:
        mark("JSON 模式", BAD, f"{exc!r}")

    # ⑥ 大 max_tokens
    print("\n⑥ 大 max_tokens（出 PDF 时要 16000）")
    try:
        r = post({"model": M, "max_tokens": 16000,
                  "messages": [{"role": "user", "content": "从 1 数到 30，只数数。"}]}, timeout=180)
        if r.status_code == 200:
            u = r.json().get("usage") or {}
            mark("大 max_tokens", OK, f"接受 16000（实际输出 {u.get('completion_tokens','?')} tok）")
        else:
            try:
                msg = (r.json().get("error") or {}).get("message", "")[:60]
            except Exception:
                msg = r.text[:60]
            mark("大 max_tokens", BAD, f"HTTP {r.status_code} {msg}")
    except Exception as exc:
        mark("大 max_tokens", BAD, f"{exc!r}")

    # ⑦ 图片输入
    print("\n⑦ 图片输入（识图批改要用）")
    try:
        from PIL import Image, ImageDraw
        import io
        im = Image.new("RGB", (220, 90), (255, 255, 255))
        ImageDraw.Draw(im).text((12, 36), "1 + 1 = ?", fill=(0, 0, 0))
        buf = io.BytesIO(); im.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        r = post({"model": M, "max_tokens": 60,
                  "messages": [{"role": "user", "content": [
                      {"type": "text", "text": "图里写的什么算式？"},
                      {"type": "image_url",
                       "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}, timeout=120)
        if r.status_code == 200:
            txt = _content(r)
            mark("图片输入", OK, f"→ {txt[:40]!r}")
        else:
            try:
                msg = (r.json().get("error") or {}).get("message", "")[:56]
            except Exception:
                msg = r.text[:56]
            mark("图片输入", BAD if r.status_code < 500 else WARN, f"HTTP {r.status_code} {msg}")
    except Exception as exc:
        mark("图片输入", WARN, f"{exc!r}")

    # ⑧ thinking 字段
    print("\n⑧ thinking 字段（DeepSeek 推理模型靠它关思考）")
    try:
        r = post({"model": M, "max_tokens": 32, "thinking": {"type": "disabled"},
                  "messages": [{"role": "user", "content": "hi"}]}, timeout=60)
        if r.status_code == 200:
            mark("thinking 字段", OK, "不报错（说明被忽略或支持）")
        else:
            mark("thinking 字段", WARN, f"HTTP {r.status_code}（要用 thinking 的话得加白名单逻辑）")
    except Exception as exc:
        mark("thinking 字段", WARN, f"{exc!r}")

    summary()


def summary() -> None:
    print()
    print("═" * 72)
    print("  体检结论")
    print("═" * 72)
    reds = [r for r in results if r[1] == BAD]
    warns = [r for r in results if r[1] == WARN]
    for name, state, detail in results:
        print(f"  {state} {name:22} {detail[:56]}")
    print()
    if reds:
        print(f"  🔴 有 {len(reds)} 项硬伤 —— **先别充钱**，把上面红色的解决了再说")
    elif warns:
        print(f"  ⚠️  没硬伤，但有 {len(warns)} 项要留意 —— 可以充最小额先小范围试")
    else:
        print("  ✅ 全项通过 —— 可以把它提到主线路了")
    print()


if __name__ == "__main__":
    main()
