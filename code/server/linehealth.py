#!/usr/bin/env python3
"""线路健康熔断器（车主 2026-10-15 定）。

## 为什么要有它

车主原话：
> 「之后如果你请求 DeepSeek 的那个请求不了，你就**自动换路线**啊……
>  不要出现像今天上午这样停止服务……**你发现一个 DeepSeek 用不了，
>  那基本上就全用不了了，代表官方你就直接切掉**，切到不是 DeepSeek 的，
>  因为不还给你连了 Gemini 和小米吗，这两个可以识图的。」

上午的问题：DeepSeek 官方挂了，但 9 条线路里**前 4 条都是 DeepSeek**，
每次答题都要挨个撞一遍超时才轮到小米 —— 又慢又容易把整条链拖死。

## 做法

按**厂商（family）**而不是按单条线路记健康状态：
  一条 DeepSeek 挂了 → **整个 DeepSeek family 熔断** → 后面 10 分钟直接跳过，
  直接落到小米 / Gemini。
这和车主的判断一致：「一个用不了，基本上就全用不了了」。

- 熔断有**冷却时间**（默认 10 分钟），到点自动半开重试，避免永久误杀。
- 状态落盘到 state/linehealth.json，重启不丢。
- **任何情况下都不会让列表变空** —— 全挂了就把冷却忽略掉，照原样返回。
"""
from __future__ import annotations

import json
import pathlib
import re
import time

COOLDOWN = 600          # 熔断冷却：10 分钟
STATE = pathlib.Path(__file__).resolve().parent / "state" / "linehealth.json"

# ⚠️ 厂商识别**按域名判，不按模型名判**（2026-10-15 修正）：
#    备用5（百度千帆）和备用8（火山方舟）跑的**也是 DeepSeek 模型**，
#    但它们不是 DeepSeek **官方** —— 车主说的是「代表官方」，
#    不能因为官方挂了就把千帆/火山也一起熔断（那正是我们的备用）。
#
# 例外：gemini 按**模型名**判 —— 识图 API 和「备用9中转站」共用同一个域名，
#       只能靠模型名区分。
_MODEL_FIRST = [
    ("gemini", re.compile(r"gemini", re.I)),
]
_HOST_RULES = [
    ("deepseek",  re.compile(r"api\.deepseek\.com", re.I)),      # ★ 官方
    ("mimo",      re.compile(r"xiaomimimo\.com", re.I)),          # 小米
    ("volc",      re.compile(r"volces\.com", re.I)),              # 火山方舟
    ("qianfan",   re.compile(r"qianfan\.baidubce\.com", re.I)),  # 百度千帆
    ("jieyouai",  re.compile(r"jieyouai\.it\.com", re.I)),       # GPT 中转
    ("relay",     re.compile(r"bingheai\.org", re.I)),            # 中转站/识图
    ("hunyuan",   re.compile(r"hunyuan\.cloud\.tencent\.com", re.I)),
    ("lkeap",     re.compile(r"lkeap\.cloud\.tencent\.com", re.I)),
]


def family(provider: dict) -> str:
    """判定这条线路属于哪个**厂商/服务**（熔断以它为粒度）。"""
    model = provider.get("model", "") or ""
    url = provider.get("base_url", "") or ""
    for name, pat in _MODEL_FIRST:          # gemini 先判（域名区分不了）
        if pat.search(model):
            return name
    for name, pat in _HOST_RULES:           # 再按域名判
        if pat.search(url):
            return name
    if re.search(r"deepseek", model, re.I):
        return "deepseek-model"             # 认不出域名但模型是 deepseek，单独一组
    return "other"


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def is_down(provider: dict, now: float | None = None) -> bool:
    """这个厂商是不是在熔断冷却里。"""
    now = now or time.time()
    d = _load()
    rec = d.get(family(provider)) or {}
    return now < float(rec.get("down_until") or 0)


def note_failure(provider: dict, why: str = "") -> None:
    """记一次失败 —— **整个厂商一起熔断**（车主：一个不行就全不行）。"""
    fam = family(provider)
    now = time.time()
    d = _load()
    rec = d.setdefault(fam, {})
    rec["fails"] = int(rec.get("fails") or 0) + 1
    rec["last_fail"] = now
    rec["last_why"] = (why or "")[:120]
    rec["last_line"] = provider.get("name", "")
    rec["down_until"] = now + COOLDOWN
    _save(d)
    try:
        import main as _m  # 复用日志
        _m.log(f"  ⛔ 线路熔断：{fam}（{provider.get('name','?')} 失败：{why[:60]}）"
               f"→ 之后 {COOLDOWN//60} 分钟跳过 {fam} 全部线路", "WARN")
    except Exception:
        pass


def note_success(provider: dict) -> None:
    """成功了就清掉该厂商的熔断。"""
    fam = family(provider)
    d = _load()
    if fam in d:
        d.pop(fam, None)
        _save(d)


def order(providers: list) -> list:
    """按健康度重排：**可用的在前，熔断的挪到最后**（但保留，不删）。

    这样即使判断错了，也只是"晚一点才用到"，不会真的丢掉任何线路。
    """
    if not providers:
        return list(providers or [])
    now = time.time()
    ok, bad = [], []
    for p in providers:
        (bad if is_down(p, now) else ok).append(p)
    return ok + bad


def available(providers: list) -> list:
    """只返回当前可用的（熔断的全滤掉）；如果**全都被熔断**就原样返回。"""
    if not providers:
        return []
    now = time.time()
    ok = [p for p in providers if not is_down(p, now)]
    return ok if ok else list(providers)


def status() -> str:
    """给运维看的摘要。"""
    d = _load()
    now = time.time()
    if not d:
        return "所有厂商正常（无熔断记录）"
    out = []
    for fam, rec in sorted(d.items()):
        if fam == "key_problems":          # 这是密钥级记录，不是厂商熔断
            continue
        left = float(rec.get("down_until") or 0) - now
        if left > 0:
            out.append(f"{fam} 熔断中（还剩 {left/60:.1f} 分钟，累计失败 {rec.get('fails')} 次）")
        else:
            out.append(f"{fam} 已恢复（历史失败 {rec.get('fails')} 次）")
    return " ｜ ".join(out)

# ══════════ 密钥级问题（不熔断厂商，但记下来给运维看）══════════
# 车主 2026-10-15：「确保每个 API 都有额度，这样调用不了肯定不是额度用完，
#                   就是它真的坏了。这两个要区分一下。」
# 所以：余额不足/限流/无权限 → 记在 key_problems 里，**不影响熔断**。
def note_key_problem(provider: dict, code: int, hint: str = "") -> None:
    d = _load()
    kp = d.setdefault("key_problems", {})
    rec = kp.setdefault(provider.get("name", "?"), {})
    rec["code"] = code
    rec["hint"] = hint
    rec["when"] = time.time()
    rec["fails"] = int(rec.get("fails") or 0) + 1
    _save(d)


def key_problems() -> list:
    """列出所有出现过密钥级问题的线路（额度/限流/权限）。"""
    d = _load()
    out = []
    for name, rec in sorted((d.get("key_problems") or {}).items()):
        out.append(f"{name}: HTTP {rec.get('code')} {rec.get('hint','')}（{rec.get('fails')} 次）")
    return out
