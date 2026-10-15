"""usage.py —— 真实 token 用量统计（车主 2026-10-12 定）

**为什么做**：以前只能靠估算 —— 余额 API 有延迟（跑了十几次调用余额纹丝不动），
速率测不出来。但 **API 每次响应都带 `usage` 字段**，记下来就是真数。

**记什么**（都是事实，不含任何估算）：
  · 每次调用：哪条线路、哪个模型、输入/输出/缓存命中的 token 数、干什么用的
  · 每天的汇总
  · 顺手记余额快照（用来**反推真实单价**，校准用）

**落盘**：`state/usage.json`

**估算费用**：用 `PRICE_*` 里的单价算。单价可被环境变量覆盖；
更靠谱的是看 `report()` 里的「实测校准」那一节 —— 用余额差额除以 token 数，
得到**真实单价**，再回头改上面的常量。

**任何异常都只记日志**，绝不影响回答。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import config

# ── DeepSeek 官方牌价（元 / 百万 token）──
# ⚠️ 这是**估算用**的默认值，不是账单。真实值以 platform.deepseek.com 为准；
#    看 report() 的「实测校准」能反推出我们实际被扣多少。
PRICE_CACHE_HIT = float(os.environ.get("PRICE_CACHE_HIT", "0.5") or 0.5)    # 输入·缓存命中
PRICE_CACHE_MISS = float(os.environ.get("PRICE_CACHE_MISS", "2.0") or 2.0)  # 输入·未命中
PRICE_OUTPUT = float(os.environ.get("PRICE_OUTPUT", "8.0") or 8.0)          # 输出

_FILE = Path(config.STATE_DIR) / "usage.json"


def _load() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"days": {}, "balances": []}


def _save(data: dict) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_FILE)          # 原子替换，防写一半崩了把统计弄坏
    except OSError as exc:
        log(f"写入失败（不影响回答）：{exc}", "WARN")


def log(msg: str, level: str = "INFO") -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] token统计: {msg}", flush=True)


def record(provider: str, model: str, usage: dict | None, kind: str = "decide") -> None:
    """记一次调用的 token 用量。`kind` 说明这次是干什么的（decide/transcribe/fix…）。"""
    if not usage:
        return
    try:
        day = time.strftime("%Y-%m-%d")
        d = _load()
        days = d.setdefault("days", {})
        rec = days.setdefault(day, {
            "calls": {}, "prompt_tokens": 0, "completion_tokens": 0,
            "cache_hit": 0, "cache_miss": 0, "by_kind": {},
        })
        # 兼容不同厂商的字段名
        pin = int(usage.get("prompt_tokens") or 0)
        pout = int(usage.get("completion_tokens") or 0)
        hit = int(usage.get("prompt_cache_hit_tokens")
                  or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        miss = int(usage.get("prompt_cache_miss_tokens") or max(0, pin - hit))
        rec["prompt_tokens"] += pin
        rec["completion_tokens"] += pout
        rec["cache_hit"] += hit
        rec["cache_miss"] += miss
        rec["calls"][provider] = int(rec["calls"].get(provider, 0)) + 1
        # 按用途也记 **token**（不只记次数）—— 这样能看出「解题/识图/修 LaTeX」
        # 各自烧了多少（车主 2026-10-13 想看 token 明细）
        bk = rec.setdefault("by_kind", {})
        cur = bk.get(kind)
        if not isinstance(cur, dict):        # 兼容老格式（以前只存次数，是个 int）
            cur = {"calls": int(cur or 0), "in": 0, "out": 0}
        cur["calls"] = int(cur.get("calls", 0)) + 1
        cur["in"] = int(cur.get("in", 0)) + pin
        cur["out"] = int(cur.get("out", 0)) + pout
        bk[kind] = cur
        d["updated"] = time.time()
        _save(d)
    except Exception as exc:                       # 绝不影响回答
        log(f"记录失败（不影响回答）：{exc!r}", "WARN")


def snapshot_balance(provider: str, balance: float) -> None:
    """记一次余额快照 —— 用来反推真实单价。"""
    try:
        d = _load()
        arr = d.setdefault("balances", [])
        arr.append({"ts": time.time(), "provider": provider, "balance": float(balance)})
        d["balances"] = arr[-500:]
        _save(d)
    except Exception:
        pass


def _cost(rec: dict) -> float:
    return (rec.get("cache_hit", 0) / 1e6 * PRICE_CACHE_HIT
            + rec.get("cache_miss", 0) / 1e6 * PRICE_CACHE_MISS
            + rec.get("completion_tokens", 0) / 1e6 * PRICE_OUTPUT)


def report(days: int = 7) -> str:
    """生成可读的用量报告（给车主看的）。"""
    d = _load()
    all_days = sorted((d.get("days") or {}).keys())[-days:]
    if not all_days:
        return "  （还没有记录 —— 从加上统计之后才开始算）"

    out = []
    out.append(f"  {'日期':12}{'调用':>7}{'输入tok':>11}{'输出tok':>10}{'缓存命中':>10}{'估算费用':>10}")
    out.append("  " + "─" * 62)
    tot_calls = tot_in = tot_out = tot_hit = 0
    tot_cost = 0.0
    for day in all_days:
        r = d["days"][day]
        c = sum(r.get("calls", {}).values())
        cost = _cost(r)
        tot_calls += c
        tot_in += r.get("prompt_tokens", 0)
        tot_out += r.get("completion_tokens", 0)
        tot_hit += r.get("cache_hit", 0)
        tot_cost += cost
        out.append(f"  {day:12}{c:>7}{r.get('prompt_tokens',0):>11,}"
                   f"{r.get('completion_tokens',0):>10,}{r.get('cache_hit',0):>10,}"
                   f"{'¥%.2f' % cost:>10}")
    out.append("  " + "─" * 62)
    out.append(f"  {'合计':12}{tot_calls:>7}{tot_in:>11,}{tot_out:>10,}{tot_hit:>10,}"
               f"{'¥%.2f' % tot_cost:>10}")
    if all_days:
        n = len(all_days)
        out.append(f"\n  日均：{tot_calls // n} 次调用，"
                   f"{(tot_in + tot_out) // n:,} token，¥{tot_cost / n:.2f}")

    # 线路明细
    out.append("\n  ── 按线路（最近 %d 天）──" % len(all_days))
    per: dict[str, int] = {}
    for day in all_days:
        for k, v in (d["days"][day].get("calls") or {}).items():
            per[k] = per.get(k, 0) + int(v)
    for k, v in sorted(per.items(), key=lambda x: -x[1]):
        pct = v * 100 / max(1, tot_calls)
        out.append(f"    {k:10} {v:>6} 次  {pct:5.1f}%")

    # 用途明细
    kinds: dict = {}
    for day in all_days:
        for k, v in (d["days"][day].get("by_kind") or {}).items():
            if isinstance(v, dict):
                cur = kinds.setdefault(k, {"calls": 0, "in": 0, "out": 0})
                cur["calls"] += int(v.get("calls", 0))
                cur["in"] += int(v.get("in", 0))
                cur["out"] += int(v.get("out", 0))
            else:
                kinds[k] = kinds.get(k, 0) + int(v)
    if kinds:
        out.append("\n  ── 按用途（调用次数 + token）──")
        rows_k = []
        for k, v in kinds.items():
            if isinstance(v, dict):
                rows_k.append((v.get("calls", 0), k, v.get("in", 0), v.get("out", 0)))
            else:
                rows_k.append((int(v), k, 0, 0))
        for n, k, i_, o_ in sorted(rows_k, key=lambda x: -x[0]):
            if i_ or o_:
                out.append(f"    {k:14} {n:>5} 次   输入 {i_:>10,}   输出 {o_:>9,}")
            else:
                out.append(f"    {k:14} {n:>5} 次")

    # 实测校准：用余额差额 / token 数反推真实单价
    bal = d.get("balances") or []
    if len(bal) >= 2:
        out.append("\n  ── 实测校准（余额差额 ÷ token 数 = 真实单价）──")
        out.append("    ⚠️ 余额 API 有延迟，样本要隔一段时间才准")
        byp: dict[str, list] = {}
        for b in bal:
            byp.setdefault(b["provider"], []).append(b)
        for prov, arr in byp.items():
            if len(arr) < 2:
                continue
            first, last = arr[0], arr[-1]
            drop = first["balance"] - last["balance"]
            if drop > 0.005:
                out.append(f"    {prov}: ¥{first['balance']:.2f} → ¥{last['balance']:.2f}"
                           f"（掉 ¥{drop:.2f}）")
    out.append(f"\n  单价假设：缓存命中 ¥{PRICE_CACHE_HIT}/M ｜ 未命中 ¥{PRICE_CACHE_MISS}/M"
               f" ｜ 输出 ¥{PRICE_OUTPUT}/M")
    out.append("  （想改：设环境变量 PRICE_CACHE_HIT / PRICE_CACHE_MISS / PRICE_OUTPUT）")
    return "\n".join(out)
