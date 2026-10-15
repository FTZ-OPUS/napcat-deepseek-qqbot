#!/usr/bin/env python3
"""回归测试：**LaTeX 渲染的容错**，目标是"群友不用重发"（车主 2026-10-12 定）。

真实事故：群友发手写图让批改，模型写的 LaTeX 里有个 `\\begin{itemize}` 忘了闭合 →
LaTeX 报 "\\begin{itemize} ... ended by \\end{document}" → 整篇渲染失败 →
机器人回「麻烦你再发我一次，我重做」→ **群友就不太愿意再发了**。

车主原话：「我们办事最好只办一次，不要让群众跑几趟。」
         「实在不行还是要诚实说让他重发，但**要把这种情况的概率大幅下降，趋向于 0**。」

所以现在有三层：
  ① **配平环境**（渲染前预防）—— `render.balance_environments`
  ② **自动修复**（渲染失败后让模型修一遍再重试）—— `brain.fix_latex`
  ③ **诚实告知**（前两层都救不回来才用）—— `main.FAIL_CLOSING`

本测试盯住 ① 和 ③（② 需要真实 API，见 test 里的说明）。
跑法：PYTHONPATH=/home/qqbot/qq-bot python3 test_latex_robust.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

LATEX_DIR = Path(__file__).resolve().parent.parent / "latex"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
sys.path.insert(0, str(LATEX_DIR))
os.environ.setdefault("QQBOT_STATE_DIR", tempfile.mkdtemp(prefix="qqbot-test-state-"))

try:
    from tests.test_tablecheck import T
except ImportError:
    from test_tablecheck import T  # noqa: E402

# 直接把 balance_environments 抠出来跑（不导入整个 render，避免拉依赖）
src = (LATEX_DIR / "render.py").read_text(encoding="utf-8")
m = re.search(r"(_BEGIN_RE = .*?)(?=\ndef compile_tex)", src, re.S)
assert m, "找不到 balance_environments 区块"
_ns: dict = {}
exec("import re\n" + m.group(1), _ns)
balance = _ns["balance_environments"]


def test_fixes_unclosed_environment():
    print("== 忘写 \\end → 自动补上（这是生产环境真实报的那个错）==")
    out, notes = balance("\\begin{itemize}\\item 第一点\\item 第二点")
    T.ok("\\end{itemize}" in out, "补上了 \\end{itemize}")
    T.ok(notes, "记录了修了什么")


def test_fixes_nested_unclosed():
    print("== 嵌套没闭合 → 逆序补齐两层 ==")
    out, notes = balance("\\begin{itemize}\\item 外层\\begin{enumerate}\\item 内层")
    T.ok("\\end{enumerate}" in out and "\\end{itemize}" in out, "两层都补了")
    T.ok(out.index("\\end{enumerate}") < out.index("\\end{itemize}"),
         "逆序闭合（enumerate 先于 itemize）")


def test_removes_stray_end():
    print("== 多余的 \\end → 删掉 ==")
    out, notes = balance("正文\\end{itemize}后面还有字")
    T.ok("\\end{itemize}" not in out, "多余的 \\end 被删")
    T.ok("后面还有字" in out, "正文没被破坏")


def test_balanced_body_untouched():
    print("== 本来就平衡的正文 → 一个字都不动（不许画蛇添足）==")
    for body in ("\\begin{itemize}\\item 对的\\end{itemize}",
                 "\\begin{proof}证明过程\\end{proof}",
                 "\\begin{align} a &= b \\end{align}",
                 "纯文字，没有环境",
                 ""):
        out, notes = balance(body)
        T.ok(out == body and not notes, f"原样返回：{body[:22] or '(空)'}")


def test_proof_env_available_in_templates():
    print("== 三个模板都加载了 amsthm（模型写证明天天用）==")
    for name in ("template_card.tex", "template_doc.tex", "template_doc_style2.tex"):
        txt = (LATEX_DIR / name).read_text(encoding="utf-8")
        T.ok("amsthm" in txt, f"{name} 有 amsthm")
        T.ok("newtheorem{theorem}" in txt, f"{name} 定义了 theorem")


def test_fail_closing_still_honest():
    print("== ③ 诚实话术还在（最后手段，不能说假话）==")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
    try:
        import main
    except Exception as exc:
        print(f"    ⚠️ 无法导入 main（{exc}），跳过这项")
        return
    T.ok(bool(main.FAIL_CLOSING), "FAIL_CLOSING 非空")
    T.ok(all("已发" not in x for x in main.FAIL_CLOSING), "没有「已发」这类谎报")


if __name__ == "__main__":
    for fn in (test_fixes_unclosed_environment, test_fixes_nested_unclosed,
               test_removes_stray_end, test_balanced_body_untouched,
               test_proof_env_available_in_templates, test_fail_closing_still_honest):
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
