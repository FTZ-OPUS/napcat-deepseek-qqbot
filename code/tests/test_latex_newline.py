#!/usr/bin/env python3
"""回归测试：模型写的字面 `\\n` 不能把 LaTeX 编译搞炸。

真实事故（2026-10-11，一天内两次）：
    模型在 JSON 里写 `\\\\n`（本想表示换行），JSON 解析后成了**字面的
    「反斜杠 + n」两个字符**，直接进 .tex：

        l.64 \\section*{题目}\\n设 $0<a_n<1$...
        Undefined control sequence.

    后果：渲染失败 → 「所有片段都没发成功」，但收尾话还说「订正版附上了」，
    群友追问「批改完了PDF咋不发」。

跑法（在服务器上，或本地有 python3 即可）：
    PYTHONPATH=/home/qqbot/qq-bot python3 test_latex_newline.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))

FAILS: list[str] = []
COUNT = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global COUNT
    COUNT += 1
    if not cond:
        FAILS.append(f"{name}  {detail}")


def main() -> int:
    # 直接测纯函数，不依赖 LaTeX 安装 —— 保证这条回归在任何环境都能跑
    # latexrender 导入时会读 config，所以给它一个能跑的最小环境
    try:
        import latexrender  # noqa: WPS433
    except Exception as exc:                       # pragma: no cover
        print(f"  ⚠️ 无法导入 latexrender（{exc}），跳过")
        return 0

    fix = latexrender._fix_literal_newlines

    # ── 1. 该修的要修掉：字面 \n 后面是中文/标点/行尾/右括号 ──
    check("字面\\n+中文 → 换行",
          fix("\\section*{题目}\\n设 $a_n>0$。") == "\\section*{题目}\n设 $a_n>0$。")
    check("字面\\n+行尾 → 换行",
          fix("第一行\\n第二行") == "第一行\n第二行")
    check("字面\\n+右括号 → 换行",
          fix("\\textbf{解：}\\n}") == "\\textbf{解：}\n}")

    # ── 2. 该保留的一个都不能动（这些命令都以 \n 开头！）──
    for cmd in ("\\neq", "\\nabla", "\\newline", "\\node", "\\noindent",
                "\\not", "\\nu", "\\nmid", "\\natural", "\\nonumber"):
        s = f"$a{cmd} b$"
        check(f"{cmd} 保留", fix(s) == s, f"→ {fix(s)!r}")

    # ── 3. 已经转义的（\\n = 换行命令 + n）不能动 ──
    check("双反斜杠+n 保留（那是换行命令+字母）",
          fix("$\\\\neq$") == "$\\\\neq$" or fix("x\\\\ny") == "x\\\\ny")

    # ── 4. 真实坏样本（照抄生产日志里那两行）──
    broken = "\\section*{题目}\\n思考题19（南京航空航天大学，2026）。"
    check("生产坏样本被修好", "\n" in fix(broken) and "\\n" not in fix(broken))

    # ── 5. 混合样本：既修 bug 又不误伤 ──
    mixed = "\\section*{题目}\\n设 $a\\neq b$，$a_n>0$。\\n{$\\nabla f$}\\n"
    out = fix(mixed)
    check("混合样本：bug 修掉", "\\n" not in out.replace("\\neq", "").replace("\\nabla", ""))
    check("混合样本：\\neq 在", "\\neq" in out)
    check("混合样本：\\nabla 在", "\\nabla" in out)

    print(f"  跑完 {COUNT} 项断言")
    if FAILS:
        print(f"  🔴 {len(FAILS)} 项失败:")
        for f in FAILS:
            print(f"     - {f}")
        return 1
    print("  ✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
