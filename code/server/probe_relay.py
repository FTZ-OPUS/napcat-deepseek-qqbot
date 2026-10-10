#!/usr/bin/env python3
"""中转站接入探测 —— 给一个令牌，自动找出该用哪个模型，并跑通一次对话。

用法：
    python3 probe_relay.py sk-你的令牌
    python3 probe_relay.py                 # 不传则读 .env 里的 FALLBACK_API_KEY

它会依次做四件事：
    1. 拉 /v1/models，把中转站支持的模型全列出来
    2. 自动挑出「deepseek v4.1 flash」对应的那个 ID
       （中转站的模型 ID 命名五花八门，不能靠猜，要看它实际返回什么）
    3. 真实发一次对话请求，验证这个模型能用
    4. 打印出该往 .env 里写哪几行

它不会修改任何配置，只做只读探测。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

BASE = sys.argv[2] if len(sys.argv) > 2 else "https://api.bingheai.org/v1"


def load_key() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    env = Path("/home/qqbot/qq-bot/.env")
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("FALLBACK_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def score(model_id: str) -> int:
    """给模型 ID 打分，挑出最像「deepseek v4.1 flash」的那个。"""
    m = model_id.lower()
    s = 0
    if "flash" in m:
        s += 100
    if "v4.1" in m or "v4-1" in m or "4.1" in m:
        s += 60
    if "deepseek" in m or m.startswith("ds"):
        s += 30
    if "v4" in m:
        s += 10
    # 排掉明显不是目标的东西
    for bad in ("embed", "rerank", "whisper", "tts", "image", "vision", "pro", "reasoner"):
        if bad in m:
            s -= 40
    return s


def main() -> int:
    key = load_key()
    if not key:
        print("没有令牌。用法: python3 probe_relay.py sk-xxxx")
        return 1

    headers = {"Authorization": f"Bearer {key}"}
    print(f"中转站 : {BASE}")
    print(f"令牌   : {key[:8]}...{key[-4:]}  （长度 {len(key)}）")
    print("=" * 62)

    # ---- 1. 列模型 ----
    try:
        resp = requests.get(f"{BASE}/models", headers=headers, timeout=25)
    except requests.RequestException as exc:
        print(f"连不上：{exc}")
        return 1

    print(f"[1] GET /models → HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(f"    返回：{resp.text[:300]}")
        print("    → 令牌无效或权限不足，去后台「令牌」页面重新建一个")
        return 1

    try:
        data = resp.json()
    except ValueError:
        print(f"    返回值不是 JSON：{resp.text[:200]}")
        return 1

    items = data.get("data") if isinstance(data, dict) else data
    ids = []
    for it in items or []:
        if isinstance(it, dict):
            ids.append(str(it.get("id") or it.get("model") or ""))
        elif isinstance(it, str):
            ids.append(it)
    ids = [i for i in ids if i]

    print(f"    共 {len(ids)} 个模型：")
    for i in sorted(ids):
        print(f"      · {i}")

    # ---- 2. 挑目标模型 ----
    ranked = sorted(ids, key=score, reverse=True)
    print()
    print("[2] 按「v4.1 flash」匹配度排序，前 3 名：")
    for i in ranked[:3]:
        print(f"      {score(i):>4} 分  {i}")
    if not ranked:
        print("    没有可用模型")
        return 1
    target = ranked[0]

    # ---- 3. 真跑一次 ----
    print()
    print(f"[3] 用 {target} 真发一次对话请求……")
    payload = {
        "model": target,
        "messages": [
            {"role": "system", "content": "你只说一个词的答案。"},
            {"role": "user", "content": "2 的 10 次方是多少？只回数字。"},
        ],
        "max_tokens": 50,
    }
    try:
        resp = requests.post(f"{BASE}/chat/completions", headers=headers, json=payload, timeout=60)
    except requests.RequestException as exc:
        print(f"    请求失败：{exc}")
        return 1

    print(f"    HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(f"    返回：{resp.text[:300]}")
        return 1

    try:
        body = resp.json()
        msg = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        print(f"    ✅ 回答：{msg.strip()[:100]}")
        print(f"    token 用量：{usage.get('prompt_tokens')} 入 / {usage.get('completion_tokens')} 出")
    except Exception as exc:
        print(f"    返回结构异常：{exc}\n    {resp.text[:300]}")
        return 1

    # ---- 4. 给出 .env 配置 ----
    print()
    print("=" * 62)
    print("[4] 探测通过。往 /home/qqbot/qq-bot/.env 里加这几行：")
    print()
    print(f"    FALLBACK_API_KEY={key}")
    print(f"    FALLBACK_BASE_URL={BASE}")
    print(f"    FALLBACK_MODEL={target}")
    print()
    print("然后 systemctl restart qqbot 即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
