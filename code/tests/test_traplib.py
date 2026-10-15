#!/usr/bin/env python3
"""回归测试：数学分析陷阱库（车主 2026-10-12 定）。

**核心思路**：数学分析真正难的不是题型，是**条件的细微差别**。
机器人最容易犯的错是**把题目没写的条件当成有**（条件臆造）。
所以最值钱的知识不是"题解"，而是「陷阱 + 反例 + 正确做法」。

⚠️ 只注入**相关**的几条：全量 3.7k 字符 ≈ 1.2k token，而现在整个 prompt 才 2.2k token，
   每次都全塞会让成本和延迟涨 50%+。没命中就注入 0 字符。
跑法：PYTHONPATH=/home/qqbot/qq-bot python3 test_traplib.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
os.environ.setdefault("QQBOT_STATE_DIR", tempfile.mkdtemp(prefix="qqbot-test-state-"))

try:
    from tests.test_tablecheck import T
except ImportError:
    from test_tablecheck import T  # noqa: E402

import traplib  # noqa: E402


def test_data_file_found():
    print("== 数据文件能定位到（本地/服务器路径不同，都要能找着）==")
    T.ok(traplib._path() is not None, f"找到数据文件：{traplib._path()}")
    T.ok(len(traplib.load()) >= 20, f"条目数 ≥20，实际 {len(traplib.load())}")


def test_parse_fields():
    print("== 解析出必要字段（标题/陷阱/反例/正确做法）==")
    items = traplib.load()
    T.ok(all(i["title"] for i in items), "每条都有标题")
    with_trap = [i for i in items if i["trap"]]
    with_right = [i for i in items if i["right"]]
    T.ok(len(with_trap) >= 20, f"有「陷阱」描述的 ≥20 条，实际 {len(with_trap)}")
    T.ok(len(with_right) >= 20, f"有「正确做法」的 ≥20 条，实际 {len(with_right)}")


def test_real_incident_trap_matched_first():
    print("== ★真实事故题必须命中「二阶可导 ⇏ 二阶连续可导」，而且要排第一 ==")
    q = "设 f 在 [0,1] 上二阶可导，f''(x) 存在但不必连续，证明存在 ξ 使 f''(ξ)=2∫f"
    items = traplib.relevant(q)
    T.ok(bool(items), "有命中")
    T.ok(items and "二阶可导" in items[0]["title"],
         f"第一条就是它：{items[0]['title'] if items else '（无）'}")


def test_hint_content_and_warning():
    print("== 注入块里要有「不许按通常情况补强条件」的警告 ==")
    h = traplib.hint("设 f 二阶可导，f'' 存在但不必连续，证明…")
    T.ok("陷阱库" in h, "有标题")
    T.ok("不许按" in h, "有禁止补强条件的警告")


def test_unrelated_injects_nothing():
    print("== 无关闲聊 → 注入 0 字符（绝不白占 token）==")
    for t in ("今天天气不错", "哈哈笑死", "在吗", ""):
        T.ok(traplib.hint(t) == "", f"不注入：{t or '(空)'}")


def test_inject_size_capped():
    print("== 注入量有上限（不能把 prompt 撑爆）==")
    h = traplib.hint("证明数列极限、一致收敛、偏导数连续、级数重排、中值定理、泰勒展开")
    T.ok(len(h) <= traplib.MAX_INJECT_CHARS + 200,
         f"注入 {len(h)} 字符 ≤ 上限 {traplib.MAX_INJECT_CHARS}+200")


def test_never_raises():
    print("== 垃圾输入不炸（绝不让陷阱库影响回答）==")
    for bad in (None, 12345, "x" * 99999, "\\begin{itemize}"):
        try:
            traplib.hint(bad)  # type: ignore[arg-type]
        except Exception as exc:
            T.ok(False, f"不该抛异常：{bad!r} → {exc!r}")
            return
    T.ok(True, "全程无异常")


def test_multiline_fields_parsed():
    print("== ★多行字段要完整解析（真实 bug：内容被截断，精确解全丢）==")
    # 真实 bug（2026-10-12）：新加的「有精确解却去搞数值积分」条目写了好几行，
    # 老解析器只读单行 → 注入时最关键的 π/2 精确解全丢了。
    items = traplib.load()
    tgt = [i for i in items if "数值积分" in i["title"]]
    T.ok(bool(tgt), "找到了这条（有精确解却去搞数值积分）")
    if tgt:
        e = tgt[0]
        T.ok(len(e["trap"]) > 50, f"陷阱字段完整（{len(e['trap'])} 字符）")
        T.ok(len(e["counter"]) > 100, f"反例字段完整（{len(e['counter'])} 字符）")
        T.ok(len(e["right"]) > 200, f"正确做法字段完整（{len(e['right'])} 字符）")
        T.ok("π/2" in e["counter"] or "π/2" in e["right"], "★精确答案 π/2 在内容里")
        T.ok("单位圆" in e["right"], "★关键解法（单位圆参数化）在内容里")


def test_exact_answer_trap_matches_integral():
    print("== 定积分题要命中「必须给精确解」这条 ==")
    h = traplib.hint("计算定积分 I = ∫_{-1}^{1} e^x sin(sqrt(1-x^2)) dx")
    T.ok("数值积分" in h, "命中了这条陷阱")
    T.ok("π/2" in h, "带上了精确答案")


if __name__ == "__main__":
    for fn in (test_data_file_found, test_parse_fields,
               test_real_incident_trap_matched_first, test_hint_content_and_warning,
               test_unrelated_injects_nothing, test_inject_size_capped,
               test_never_raises, test_multiline_fields_parsed,
               test_exact_answer_trap_matches_integral):
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
