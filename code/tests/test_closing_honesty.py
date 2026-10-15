#!/usr/bin/env python3
"""回归测试：**收尾话必须基于"实际发出去的"，不能说假话。**

真实事故（2026-10-12，一天三次）：

    11:26:26 [WARN] 所有片段都没发成功
    11:26:29 收尾：发你了              ← 假话，什么都没发出去
    11:27:13 群友：PDF发群里啊          ← 群友只能再催一遍
    11:27:21 发送 PDF（这次才真发出去）

根因：`send_parts()` 原来返回 None，收尾逻辑只能看**模型"计划要发什么"**
（`result["parts"]` 里有没有 math 片段），于是渲染失败也照样说"发你了"。

修法：`send_parts()` 返回 `(sent_any, sent_doc)`，收尾逻辑据此二选一：
  - `sent_any=False` → 发**诚实告知**（FAIL_CLOSING），绝不说"发了"
  - `sent_doc=True`  → 才叠正常的收尾 @

跑法：PYTHONPATH=/home/qqbot/qq-bot python3 test_closing_honesty.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

# 同其它测试：状态目录隔离（绝不能碰线上 state/）
os.environ.setdefault("QQBOT_STATE_DIR", tempfile.mkdtemp(prefix="qqbot-test-state-"))

try:
    from tests.test_tablecheck import T
except ImportError:
    from test_tablecheck import T  # noqa: E402

import config      # noqa: E402
import onebot      # noqa: E402
import humanize    # noqa: E402
import main        # noqa: E402
import latexrender # noqa: E402

GID = 999999
_real_render = latexrender.render


def setup():
    onebot.call = lambda action, timeout=30, **kw: {
        "status": "ok", "retcode": 0, "data": {"message_id": 1}}
    humanize.sleep_typing = lambda t: 0
    humanize.gap_between_parts = lambda: 0
    humanize.split_long_text = lambda t: [t]


def test_render_failure_returns_false():
    print("== 渲染失败 → send_parts 必须返回 (False, False)，不许谎报成功 ==")
    setup()
    def boom(b, m="card", style=""):
        raise RuntimeError("正文过长（16098 字符，上限 8000）")
    latexrender.render = boom
    try:
        r = main.send_parts(GID, [{"kind": "math", "mode": "doc",
                                   "latex": "x", "filename": "测试"}], style="")
    finally:
        latexrender.render = _real_render
    T.ok(r == (False, False), f"返回 (False, False)，实际 {r}")


def test_normal_pdf_returns_true_true():
    print("== 正常发出 PDF → (True, True) ==")
    setup()
    latexrender.render = lambda b, m="card", style="": Path("/tmp/_stub.pdf")
    try:
        r = main.send_parts(GID, [{"kind": "math", "mode": "doc",
                                   "latex": "x", "filename": "测试"}], style="")
    finally:
        latexrender.render = _real_render
    T.ok(r == (True, True), f"返回 (True, True)，实际 {r}")


def test_text_only_returns_true_false():
    print("== 只发文字 → (True, False)：纯打字不叠收尾 @ ==")
    setup()
    r = main.send_parts(GID, [{"kind": "text", "text": "一句话"}], style="")
    T.ok(r == (True, False), f"返回 (True, False)，实际 {r}")


def test_card_image_not_counted_as_pdf():
    print("== 发卡片图 → (True, False)：不能说「PDF 发你了」==")
    setup()
    latexrender.render = lambda b, m="card", style="": Path("/tmp/_stub.png")
    try:
        r = main.send_parts(GID, [{"kind": "math", "mode": "card", "latex": "x"}], style="")
    finally:
        latexrender.render = _real_render
    T.ok(r == (True, False), f"返回 (True, False)，实际 {r}")


def test_partial_success_doc_failed():
    print("== 部分成功：文字发出去了、PDF 失败 → (True, False)（第二次事故的场景）==")
    setup()
    def boom(b, m="card", style=""):
        raise RuntimeError("Undefined control sequence \\mathscr")
    latexrender.render = boom
    try:
        r = main.send_parts(GID, [
            {"kind": "text", "text": "这道题我来答一下，发个 PDF"},
            {"kind": "math", "mode": "doc", "latex": "x", "filename": "测试"}], style="")
    finally:
        latexrender.render = _real_render
    T.ok(r == (True, False), f"返回 (True, False)，实际 {r}")
    T.ok(r[0] is True and r[1] is False,
         "sent_any=True 但 sent_doc=False —— 调用方据此把收尾改成诚实告知")


def test_fail_closing_never_claims_success():
    print("== 诚实话术里不能出现「已发/发你了/附上」这类肯定说法 ==")
    BAD = ("发你了", "发出来了", "已发", "附上了")
    T.ok(bool(main.FAIL_CLOSING), "FAIL_CLOSING 不为空")
    for line in main.FAIL_CLOSING:
        hits = [w for w in BAD if w in line]
        # 注意：允许「没发出来」这种**否定句**；只禁肯定式谎报
        T.ok(not hits or "没发出来" in line, f"话术诚实：{line[:40]}")


if __name__ == "__main__":
    for fn in (test_render_failure_returns_false, test_normal_pdf_returns_true_true,
               test_text_only_returns_true_false, test_card_image_not_counted_as_pdf,
               test_partial_success_doc_failed, test_fail_closing_never_claims_success):
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
