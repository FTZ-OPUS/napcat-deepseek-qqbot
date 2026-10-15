#!/usr/bin/env python3
"""云端全线路体检（车主 2026-10-15 定）。

车主原话：
> 「我如果对你说句测试连通性，你能不能直接在云端测一下，然后回复我……
>  回复我各个 API 接口的连通性，并标注它们是什么模型？」

所以：群主 @ 机器人说「测试连通性」→ **真去挨个打一遍所有 API** →
回一条报告，写明每条线路【通不通】【是什么模型】【是哪家】。

设计要点：
  · **并发探测**（13 条线一起打），否则一条一条串行要等一分钟
  · 单条超时压到 12 秒 —— 体检要快，不能让群主干等
  · **只报状态和模型名，绝不回显 key**（报告会发到群里）
  · 任何异常都吞掉转成「不通」，绝不让体检本身把服务搞挂
"""
from __future__ import annotations

import concurrent.futures as cf
import time

import requests

PROBE_TIMEOUT = 12          # 普通线超时（体检要快）
NAMED_TIMEOUT = 80          # 点名线超时（GPT/ASTRA 是"想得久"的模型，
                            # 实测单题 30~90s；给 12s 会把健康的线路误报成不通）
MAX_WORKERS = 13


def _probe(provider: dict) -> dict:
    """打一条线路，返回 {name, model, family, ok, ms, detail}。"""
    name = provider.get("name", "?")
    model = provider.get("model", "?")
    base = (provider.get("base_url") or "").rstrip("/")
    key = provider.get("key") or ""
    out = {"name": name, "model": model, "family": "", "ok": False,
           "ms": 0, "detail": ""}
    try:
        import linehealth
        out["family"] = linehealth.family(provider)
    except Exception:
        pass
    if not base or not key:
        out["detail"] = "没配 key"
        return out
    _to = NAMED_TIMEOUT if (name or "").startswith("GPT") else PROBE_TIMEOUT
    out["timeout"] = _to
    t0 = time.time()
    try:
        r = requests.post(
            base + "/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "max_tokens": 16,
                  "messages": [{"role": "user", "content": "只回两字：可用"}]},
            timeout=_to)
        out["ms"] = (time.time() - t0) * 1000
        if r.status_code == 200:
            out["ok"] = True
        else:
            hint = {401: "密钥无效", 402: "余额不足", 403: "无权限",
                    429: "被限流", 500: "服务端错误", 502: "网关错误",
                    503: "服务不可用", 504: "网关超时"}.get(r.status_code, "")
            out["detail"] = f"HTTP {r.status_code} {hint}".strip()
    except requests.Timeout:
        out["ms"] = (time.time() - t0) * 1000
        out["detail"] = f"超时（>{out.get('timeout', PROBE_TIMEOUT)}s）"
    except Exception as exc:
        out["ms"] = (time.time() - t0) * 1000
        out["detail"] = type(exc).__name__
    return out


def check_all() -> str:
    """并发体检所有线路，返回一段可以直接发群的报告文字。"""
    import config

    targets = []
    for p in config.PROVIDERS:
        q = dict(p); q["_kind"] = "常规"
        targets.append(q)
    for p in config.NAMED_PROVIDERS:
        q = dict(p); q["_kind"] = "点名"
        targets.append(q)
    # 识图 API 单独加进去
    if getattr(config, "VISION_API_KEY", ""):
        targets.append({
            "name": "识图API", "model": getattr(config, "VISION_API_MODEL", "?"),
            "base_url": getattr(config, "VISION_API_BASE", ""),
            "key": config.VISION_API_KEY, "_kind": "识图",
        })

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(_probe, targets))
    spent = time.time() - t0

    ok_n = sum(1 for r in results if r["ok"])
    lines = [f"【全线路体检】{ok_n}/{len(results)} 条通 ｜ 耗时 {spent:.1f}s", ""]
    for kind in ("常规", "点名", "识图"):
        group = [r for r, t in zip(results, targets) if t.get("_kind") == kind]
        if not group:
            continue
        lines.append(f"— {kind}线路 —")
        for r in group:
            mark = "✅" if r["ok"] else "❌"
            ms = f"{r['ms']/1000:.1f}s" if r["ms"] else "  —  "
            extra = "" if r["ok"] else f"（{r['detail']}）"
            lines.append(f"{mark} {r['name']} · {r['model']} · {r['family']} · {ms}{extra}")
        lines.append("")
    # 点名线是有意给长超时的，报告里提醒一句
    if any((r["name"] or "").startswith("GPT") for r in results):
        lines.append("（点名线给 80s 超时，它们本来就想得久）")
    if ok_n == len(results):
        lines.append("全部正常 ✅")
    else:
        bad = [r["name"] for r in results if not r["ok"]]
        lines.append(f"不通：{'、'.join(bad)}")
    return "\n".join(lines).strip()
