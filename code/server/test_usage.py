#!/usr/bin/env python3
"""回归测试：真实 token 用量统计（车主 2026-10-12 定）。

**背景**：车主问「官方池能撑到 10-17 吗」，我只能靠估算 ——
余额 API 有延迟（跑了十几次调用余额纹丝不动），测不出消耗率。

**转机**：API 每次响应都带 `usage` 字段 → 记下来就是真数。

**设计要点（本测试盯的就是这些）**：
  ① 只记**成功**的调用（失败的不产生费用）
  ② 五个调用点全部走 `brain._post_llm` 统一出口 —— 不能有漏网的
  ③ 记录必须**原子写**（写一半崩了不能把统计弄坏）
  ④ 任何异常都不能影响回答

跑法：PYTHONPATH=/home/qqbot/qq-bot python3 test_usage.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))
_TMP = tempfile.mkdtemp(prefix="qqbot-test-state-")
os.environ["QQBOT_STATE_DIR"] = _TMP

try:
    from tests.test_tablecheck import T
except ImportError:
    from test_tablecheck import T  # noqa: E402

try:
    import requests  # noqa: F401
except ImportError:  # pragma: no cover
    import types as _t
    _r = _t.ModuleType("requests")

    class _E(Exception):
        pass

    _r.RequestException = _E
    _r.get = lambda *a, **k: (_ for _ in ()).throw(_E("stub"))
    _r.post = lambda *a, **k: (_ for _ in ()).throw(_E("stub"))
    sys.modules["requests"] = _r

import usage  # noqa: E402


def _fresh():
    f = Path(_TMP) / "usage.json"
    if f.exists():
        f.unlink()
    return f


def test_records_tokens():
    print("== 记录一次调用的 token 用量 ==")
    _fresh()
    usage.record("主线路", "deepseek-chat",
                 {"prompt_tokens": 3000, "completion_tokens": 500,
                  "prompt_cache_hit_tokens": 2000, "prompt_cache_miss_tokens": 1000},
                 kind="decide")
    d = json.loads((Path(_TMP) / "usage.json").read_text(encoding="utf-8"))
    day = list(d["days"])[0]
    r = d["days"][day]
    T.ok(r["prompt_tokens"] == 3000, "输入记对了")
    T.ok(r["completion_tokens"] == 500, "输出记对了")
    T.ok(r["cache_hit"] == 2000, "缓存命中记对了")
    T.ok(r["cache_miss"] == 1000, "缓存未命中记对了")
    T.ok(r["calls"]["主线路"] == 1, "线路记对了")
    # 2026-10-13 起 by_kind 从「只记次数(int)」升级成「次数+token(dict)」——
    # 这样能看出解题/识图/修LaTeX各自烧了多少 token
    bk = r["by_kind"]["decide"]
    T.ok((bk["calls"] if isinstance(bk, dict) else bk) == 1, "用途记对了")
    T.ok(isinstance(bk, dict) and bk.get("in") == 3000, "★用途也记了输入 token")
    T.ok(isinstance(bk, dict) and bk.get("out") == 500, "★用途也记了输出 token")


def test_backward_compat_old_int_kind():
    print("== 老格式兼容（by_kind 以前是 int）==")
    # ⚠️ 不能依赖前面测试留下的数据 —— 它们会清状态。
    #    自己造一份「老格式」的 usage.json（by_kind 的值是 int 不是 dict）。
    p = Path(_TMP) / "usage.json"
    import time as _t
    _DAY = _t.strftime("%Y-%m-%d")      # ⚠️ 不能用写死的日期 —— 跨午夜后 record 写到新的一天，断言就崩
    p.write_text(json.dumps({
        "days": {_DAY: {
            "calls": {"主线路": 3}, "prompt_tokens": 1000, "completion_tokens": 200,
            "cache_hit": 600, "cache_miss": 400,
            "by_kind": {"judge": 7, "decide": {"calls": 2, "in": 500, "out": 100}},
        }},
        "balances": {},
    }, ensure_ascii=False), encoding="utf-8")
    rep = usage.report(1)                            # 不能炸
    T.ok("judge" in rep, "★老格式(int)也能出报表")
    T.ok("decide" in rep, "新格式(dict)也一起出")
    # 老格式再记一笔，应该能平滑升级成 dict
    usage.record("主线路", "m", {"prompt_tokens": 10, "completion_tokens": 5}, kind="judge")
    d2 = json.loads(p.read_text(encoding="utf-8"))
    bk = d2["days"][_DAY]["by_kind"]["judge"]
    T.ok(isinstance(bk, dict) and bk["calls"] == 8, "★老数据能平滑升级成 dict（7+1=8）")


def test_accumulates():
    print("== 多次调用会累加（不是覆盖）==")
    _fresh()
    for _ in range(3):
        usage.record("主线路", "deepseek-chat",
                     {"prompt_tokens": 100, "completion_tokens": 10}, kind="decide")
    d = json.loads((Path(_TMP) / "usage.json").read_text(encoding="utf-8"))
    r = d["days"][list(d["days"])[0]]
    T.ok(r["calls"]["主线路"] == 3, f"3 次，实际 {r['calls'].get('主线路')}")
    T.ok(r["prompt_tokens"] == 300, f"输入 300，实际 {r['prompt_tokens']}")


def test_multiple_providers():
    print("== 按线路分开统计（看降级到哪条线烧了多少）==")
    _fresh()
    usage.record("主线路", "m", {"prompt_tokens": 10, "completion_tokens": 1})
    usage.record("备用1", "m", {"prompt_tokens": 20, "completion_tokens": 2})
    usage.record("备用1", "m", {"prompt_tokens": 20, "completion_tokens": 2})
    d = json.loads((Path(_TMP) / "usage.json").read_text(encoding="utf-8"))
    r = d["days"][list(d["days"])[0]]
    T.ok(r["calls"] == {"主线路": 1, "备用1": 2}, f"实际 {r['calls']}")


def test_ignores_empty_usage():
    print("== 没有 usage 字段 → 不记（失败调用不产生费用）==")
    _fresh()
    usage.record("主线路", "m", None)
    usage.record("主线路", "m", {})
    f = Path(_TMP) / "usage.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        T.ok(not d.get("days"), "没产生任何记录")
    else:
        T.ok(True, "文件都没建（也对）")


def test_never_raises():
    print("== 垃圾输入不炸（绝不影响回答）==")
    _fresh()
    for bad in ({"prompt_tokens": "abc"}, {"prompt_tokens": None},
                {"prompt_tokens": -5}, {"unknown_field": 1}):
        try:
            usage.record("x", "y", bad)
        except Exception as exc:
            T.ok(False, f"不该抛：{bad} → {exc!r}")
            return
    T.ok(True, "全程无异常")


def test_report_renders():
    print("== 报告能渲染出来 ==")
    _fresh()
    usage.record("主线路", "m", {"prompt_tokens": 1000, "completion_tokens": 200},
                 kind="decide")
    txt = usage.report(7)
    T.ok("调用" in txt and "输入tok" in txt, "有表头")
    T.ok("主线路" in txt, "有线路明细")
    T.ok("decide" in txt, "有用途明细")
    T.ok("单价假设" in txt, "写明了用的什么单价")


def test_atomic_write():
    print("== 原子写：不留一半的坏文件 ==")
    _fresh()
    usage.record("主线路", "m", {"prompt_tokens": 1, "completion_tokens": 1})
    # 不该留下 .tmp
    T.ok(not (Path(_TMP) / "usage.tmp").exists(), "没有残留 .tmp")


def test_balance_snapshot():
    print("== 余额快照能存（用来反推真实单价）==")
    _fresh()
    usage.snapshot_balance("主线路", 3.67)
    usage.snapshot_balance("主线路", 3.50)
    d = json.loads((Path(_TMP) / "usage.json").read_text(encoding="utf-8"))
    T.ok(len(d["balances"]) == 2, f"2 条，实际 {len(d['balances'])}")
    T.ok(abs(d["balances"][-1]["balance"] - 3.50) < 1e-9, "值对")


def test_all_call_sites_go_through_helper():
    print("== ★五个调用点必须全走 _post_llm（漏一个就统计不准）==")
    # 源码目录：**本地**是 qq-bot/server/xxx.py，**服务器**是平铺的 qq-bot/xxx.py
    cands = [HERE.parent / "server" / "brain.py", HERE.parent / "brain.py"]
    src_path = next((c for c in cands if c.exists()), cands[0])
    src = src_path.read_text(encoding="utf-8")
    # 裸的 requests.post 只应出现在 _post_llm 内部（1 处调用 + 1 处文档提及）
    n = src.count("requests.post(")
    T.ok(n <= 2, f"裸 requests.post 只剩 {n} 处（应为 1 处调用）")
    for kind in ("decide", "transcribe", "fix_latex", "judge", "tablecheck"):
        T.ok(f'kind="{kind}"' in src, f"{kind} 走了统一出口")


if __name__ == "__main__":
    for fn in (test_records_tokens, test_accumulates, test_multiple_providers,
               test_ignores_empty_usage, test_never_raises, test_report_renders,
               test_atomic_write, test_balance_snapshot,
               test_all_call_sites_go_through_helper,
               test_backward_compat_old_int_kind):
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
