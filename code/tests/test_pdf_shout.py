#!/usr/bin/env python3
"""回归测试：**「/pdf + 图」这种公开喊话格式也要接**（车主 2026-10-12 定）。

背景：群里流行一种"喊 PDF 机器人"的格式 ——
    开头写 `/pdf`，同时**引用一张题目图**，**不 @ 任何具体的人**。
凛音同学（群里的另一个机器人）会接，而我们以前只认 @，所以这种请求全被漏掉了。

车主原话：
    "别人说一句 /PDF，同时引用一个图片，这个图片又是题目的话…
     没有艾特他的时候它也能自动回复，那这个时候你也能自动回复"
    "你俩可以比一下谁的答案更好。只要你的答案更好的，别人不就用你了吗？"

判定规则（宁可漏，不可误伤）：
  ✅ 以 /pdf 开头（认全角／、大小写、前导空白、无空格如 /pdf详细）
  ✅ 带图（直接发的或引用的）
  ❌ 没图 → 不接（空喊）
  ❌ /pdf 不在开头 → 不接（是正文里提了一句）
  ❌ /pdfx、/pdfs → 不接（不是这个格式）
  ❌ @ 了别人 → 不接（不去抢别人的 @）

跑法：PYTHONPATH=/home/qqbot/qq-bot python3 test_pdf_shout.py
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

# main.py 依赖 pdf2md → pymupdf4llm，缺依赖时跳过而不是崩
try:
    import main
except Exception as exc:                                    # pragma: no cover
    print(f"  ⚠️ 无法导入 main（{exc}），跳过")
    sys.exit(0)


def test_accepts_standard_format():
    print("== 标准格式：/pdf 开头 + 带图 → 接 ==")
    for text in ("/pdf 详细解答下这个题，别上来就乱套东西",
                 "/PDF 解题",
                 "／pdf 全角斜杠",
                 "   /pdf 前导空白",
                 "/pdf详细解答（没空格）",
                 "/pdf: 冒号分隔",
                 "/pdf，中文逗号"):
        T.ok(main.is_pdf_command(text, True), f"认：{text[:22]}")


def test_accepts_without_image_now():
    print("== ★没图也要接（2026-10-13 车主改的规则）==")
    # 真实场景：群友打「/pdf 中国近代史到新中国成立前马克思主义中国化的进程」
    # （纯文字、没图），凛音同学答了，我们没答 —— 等于白送用户。
    # 车主原话：「只要出现了 /pdf 开头的问题，你都要回答」
    T.ok(main.is_pdf_command("/pdf 中国近代史到新中国成立前马克思主义中国化的进程", False),
         "纯文字 /pdf 要接")
    T.ok(main.is_pdf_command("/pdf 帮我总结马原考点", False), "纯文字总结要接")
    T.ok(main.is_pdf_command("/pdf 带图", True), "带图照旧接")
    # 假命令仍然不接
    T.ok(not main.is_pdf_command("/pdfx 不是命令", False), "/pdfx 不接")
    T.ok(not main.is_pdf_command("我想看pdf", False), "不是开头不接")


def test_rejects_not_at_start():
    print("== /pdf 不在开头 → 不接（正文里提了一句而已）==")
    for text in ("这道题 /pdf 中间出现的", "帮我看看这个 /pdf", "我 /pdf 会用"):
        T.ok(not main.is_pdf_command(text, True), f"不认：{text[:22]}")


def test_rejects_lookalike():
    print("== /pdfx、/pdfs 这类近似词 → 不接 ==")
    for text in ("/pdfx 不是标准格式", "/pdfs 复数形式", "/pdf2 版本号"):
        T.ok(not main.is_pdf_command(text, True), f"不认：{text[:22]}")


def test_rejects_empty():
    print("== 空文本 → 不接 ==")
    T.ok(not main.is_pdf_command("", True), "空文本不接")
    T.ok(not main.is_pdf_command(None, True), "None 不接")


def test_pdf_revise_detection():
    print("== 引用 PDF 让改 → 必须出新 PDF（不许只回文字）==")
    T.ok(main.wants_pdf_revise("没说二阶导连续啊 你咋用了 快重写", True), "「快重写」认")
    T.ok(main.wants_pdf_revise("你第一题写错了 改一下", True), "「改一下」认")
    T.ok(main.wants_pdf_revise("你这个PDF过程写的太简洁了 写完整严谨 改", True), "「写完整+改」认")
    T.ok(main.wants_pdf_revise("改完把新PDF发我", True), "「新PDF」认")


def test_pdf_revise_negative():
    print("== 只是阅读/闲聊 → 不强制（别把普通请求也掰成 PDF）==")
    for text in ("阅读一下", "帮我看看这个", "白熊咋样", "次数等于n的时候帮我看看"):
        T.ok(not main.wants_pdf_revise(text, True), f"不认：{text[:20]}")
    T.ok(not main.wants_pdf_revise("快重写", False), "没点名不认")


def test_persona_asks_to_transcribe_each_problem():
    print("== persona 要求「每题先抄题再解答」（方便以后对着 PDF 改）==")
    import persona
    T.ok("每道题都要先把题目抄一遍" in persona.PERSONA, "有抄题规则")
    T.ok("以后群友引用这份 PDF 让你改" in persona.PERSONA, "写明了这条规则的用意")


if __name__ == "__main__":
    for fn in (test_accepts_standard_format, test_accepts_without_image_now,
               test_rejects_not_at_start, test_rejects_lookalike,
               test_rejects_empty, test_pdf_revise_detection,
               test_pdf_revise_negative,
               test_persona_asks_to_transcribe_each_problem):
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
