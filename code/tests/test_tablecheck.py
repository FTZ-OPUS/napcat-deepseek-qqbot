"""tablecheck 单元测试 —— 全部离线，绝不触碰真实群。

数据来源：
  BROKEN_SAMPLE：复刻自服务器 out/table_test.pdf（模型真实产出的坏表，
  σ 行用了 z−c 约定还选错了入基变量 —— DSH 交接时留下的靶子）。
  其余正确表由 tests 里内置的精确分数单纯形求解器生成（当 ground truth）。

跑法：python3 tests/test_tablecheck.py
"""
from __future__ import annotations

# ⚠️ 状态目录隔离 —— **必须最早执行**：
# 别的测试文件都会 `from tests.test_tablecheck import T`，所以放在这里等于给
# 整套测试兜底。不隔离的话，测试会写坏线上真实的 state/
# （2026-10-12 真实事故：测试用假 QQ 覆盖了线上 pmchat 游标 →
#   重启后把历史私信全重答一遍 → 凌晨 3 点骚扰了两个好友）。
import os as _os
import tempfile as _tempfile
_os.environ.setdefault("QQBOT_STATE_DIR", _tempfile.mkdtemp(prefix="qqbot-test-state-"))

import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import tablecheck as tc  # noqa: E402

F = Fraction


# ============================================================
#  精确分数单纯形求解器（只服务于测试，当 ground truth）
# ============================================================

def solve_max(c, A, b, max_iter=30):
    """max c·x s.t. A x ≤ b, x ≥ 0（b ≥ 0）。松弛变量起步，Bland 规则。
    返回 (tables, status)，每个 table: dict(basis, cB, b, A, sigma, z)。"""
    m, n = len(A), len(c)
    cj = [F(v) for v in c] + [F(0)] * m
    T = [[F(v) for v in row] + [F(1) if k == i else F(0) for k in range(m)]
         for i, row in enumerate(A)]
    bb = [F(v) for v in b]
    basis = [n + i for i in range(m)]
    tables = []
    for _ in range(max_iter):
        cB = [cj[i] for i in basis]
        z = [sum(cB[i] * T[i][j] for i in range(m)) for j in range(n + m)]
        sigma = [cj[j] - z[j] for j in range(n + m)]
        zval = sum(cB[i] * bb[i] for i in range(m))
        tables.append(dict(basis=list(basis), cB=cB, b=list(bb),
                           A=[row[:] for row in T], sigma=sigma, z=zval))
        cand = [j for j in range(n + m) if sigma[j] > 0]
        if not cand:
            return tables, "optimal"
        j = cand[0]                       # Bland：最小下标
        ratios = [(bb[i] / T[i][j], basis[i], i) for i in range(m) if T[i][j] > 0]
        if not ratios:
            return tables, "unbounded"
        r = min(ratios)[2]
        p = T[r][j]
        T[r] = [x / p for x in T[r]]
        bb[r] = bb[r] / p
        for i in range(m):
            if i != r and T[i][j] != 0:
                f = T[i][j]
                T[i] = [T[i][k] - f * T[r][k] for k in range(n + m)]
                bb[i] = bb[i] - f * bb[r]
        basis[r] = j
    return tables, "iter_limit"


def num(v: Fraction) -> str:
    return tc.fmt_frac(v)


def emit_latex(tables, status, obj_text, k):
    """把求解器的表序列按模型的真实排版习惯输出成 LaTeX。"""
    n = k  # 变量总数（含松弛）
    chunks = [obj_text, ""]
    for idx, t in enumerate(tables):
        is_last = idx == len(tables) - 1
        chunks.append(r"\begin{tabular}{cccccccc}")
        chunks.append(r"\toprule")
        head = [r"$C_B$", r"$X_B$", r"$b$",
                rf"\multicolumn{{{n}}}{{c}}{{$c_j \to$}}"]
        if not is_last:
            head.append(r"$\theta$")
        chunks.append(" & ".join(head) + r" \\")
        row2 = ["", "", ""] + [num(v) for v in
                               ([F(2), F(3)] + [F(0)] * (n - 2) if idx == 0 else
                                [F(0)] * n)]
        # c_j 行永远写完整的 c（模型实际也这么画）
        row2 = ["", "", ""] + [num(v) for v in _CJ_OF[idx]] + (
            [""] if not is_last else [])
        chunks.append(" & ".join(row2) + r" \\")
        row3 = ["", "", ""] + [f"${{x}}_{{{j+1}}}$" for j in range(n)] + (
            [""] if not is_last else [])
        chunks.append(" & ".join(row3) + r" \\")
        chunks.append(r"\midrule")
        for i in range(len(t["basis"])):
            basis_name = f"${{x}}_{{{t['basis'][i]+1}}}$"
            cells = [num(t["cB"][i]), basis_name, num(t["b"][i])]
            cells += [num(v) for v in t["A"][i]]
            if not is_last:
                cells.append("")  # θ 由调用方填
            chunks.append(" & ".join(cells) + r" \\")
        chunks.append(r"\midrule")
        sig_cells = [r"$\sigma_j$", num(t["z"])] + [num(v) for v in t["sigma"]]
        chunks.append(" & ".join(sig_cells) + r" \\")
        chunks.append(r"\bottomrule")
        chunks.append(r"\end{tabular}")
        chunks.append("")
        if not is_last:
            chunks.append(f"${{x}}_{{{t['_enter']+1}}}$ 入基，"
                          f"${{x}}_{{{t['_leave']+1}}}$ 出基")
            chunks.append("")
        else:
            if status == "optimal":
                chunks.append("至此所有检验数均 ≤ 0，最优解已得到。")
            elif status == "unbounded":
                chunks.append("该问题无界。")
    return "\n".join(chunks)


def annotate(tables):
    """求解器不知道入基/出基是谁 —— 这里补：每次迭代的 σ 最大列和 θ 最小行。"""
    for idx, t in enumerate(tables):
        sig = t["sigma"]
        cand = [j for j in range(len(sig)) if sig[j] > 0]
        if not cand:
            t["_enter"] = None
            t["_leave"] = None
            continue
        j = cand[0]
        t["_enter"] = j
        ratios = [(t["b"][i] / t["A"][i][j], t["basis"][i], i)
                  for i in range(len(t["basis"])) if t["A"][i][j] > 0]
        t["_leave"] = min(ratios)[1] if ratios else None
        # 把 θ 填进数据行（emit 时占位在最后）
        t["_theta"] = [t["b"][i] / t["A"][i][j] if t["A"][i][j] > 0 else None
                       for i in range(len(t["basis"]))] if ratios else None
    return tables


# c_j：不同题不同，存这里给 emit 用
_CJ_OF = {}


# ============================================================
#  测试数据
# ============================================================

# 复刻自服务器 out/table_test.pdf 的真实坏表（模型产出）：
# σ 行按 z−c 约定写（−3,−2,0,0），据此却宣布 x2 入基（按它自己的约定也该 x1）。
# 正确的初始表（σ=c−z）应为 3,2,0,0，x1 入基、x3 出基。
BROKEN_SAMPLE = r"""初始单纯形表 \quad max z = 3x_1 + 2x_2

\begin{tabular}{ccccccccc}
\toprule
$C_B$ & $X_B$ & $b$ & \multicolumn{4}{c}{$c_j \to$} & $\theta$ & \\
 & & & 3 & 2 & 0 & 0 & & \\
 & & & $x_1$ & $x_2$ & $x_3$ & $x_4$ & & \\
\midrule
0 & $x_3$ & 4 & 1 & 1 & 1 & 0 & 4 & \\
0 & $x_4$ & 6 & 1 & \textbf{3} & 0 & 1 & 2 & $\leftarrow$ \\
\midrule
$\sigma_j$ & 0 & $-3$ & $-2$ & 0 & 0 & & & \\
\bottomrule
\end{tabular}

$x_2$ 入基，$x_4$ 出基（$\theta$ 最小者为 2）"""

# 上表改成对的版本（σ=c−z，x1 入基，x3 出基）
GOOD_SAMPLE = BROKEN_SAMPLE \
    .replace("$-3$ & $-2$", "$3$ & $2$") \
    .replace("$x_2$ 入基，$x_4$ 出基（$\\theta$ 最小者为 2）",
             "$x_1$ 入基，$x_3$ 出基（$\\theta$ 最小者为 4）") \
    .replace("1 & \\textbf{3} & 0 & 1 & 2", "1 & 3 & 0 & 1 & 6")


# ============================================================
#  测试
# ============================================================

class T:
    count = 0

    @staticmethod
    def ok(cond, msg):
        T.count += 1
        if cond:
            print(f"  ✓ {msg}")
        else:
            print(f"  ✗ FAIL: {msg}")
            raise AssertionError(msg)


def test_broken_sample():
    print("== 坏样本（table_test.pdf 复刻）必须被抓 ==")
    issues, notes, checked = tc.verify(BROKEN_SAMPLE)
    T.ok(checked, "坏样本被识别为单纯形表")
    joined = " ".join(issues)
    T.ok("入基" in joined, f"抓到入基选择错：{joined[:80]}")
    T.ok(any("σ" in i for i in issues), "抓到 σ 行约定问题")


def test_good_sample():
    print("== 改对后的样本必须零 issue ==")
    issues, notes, checked = tc.verify(GOOD_SAMPLE)
    T.ok(checked, "好样本被识别")
    T.ok(not issues, f"无 issue（实际：{issues}）")


def test_solver_end_to_end():
    print("== 求解器生成的完整迭代链（含最优宣布）零误报 ==")
    # max z = 2x1+3x2, x1+x2≤4, x1+3x2≤6 → 需要两轮迭代
    tables, status = solve_max([2, 3], [[1, 1], [1, 3]], [4, 6])
    annotate(tables)
    _CJ_OF.clear()
    _CJ_OF[0] = [F(2), F(3), F(0), F(0)]
    _CJ_OF[1] = [F(2), F(3), F(0), F(0)]
    _CJ_OF[2] = [F(2), F(3), F(0), F(0)]
    T.ok(status == "optimal" and len(tables) == 3, f"求解器出 3 张表（{status}）")
    latex = emit_latex(tables, status, "max z = 2x_1 + 3x_2", 4)
    issues, notes, checked = tc.verify(latex)
    T.ok(checked, "识别出 3 张单纯形表")
    T.ok(not issues, f"零误报（实际 issues：{issues}；notes：{notes[:2]}）")


def test_fabricated_number():
    print("== 第二张表编一个数字，跨表验算必须抓 ==")
    tables, status = solve_max([2, 3], [[1, 1], [1, 3]], [4, 6])
    annotate(tables)
    tables[1]["b"][0] = F(7)          # 把第一个 b 从 2 改成 7（编数）
    _CJ_OF.clear()
    for i in range(3):
        _CJ_OF[i] = [F(2), F(3), F(0), F(0)]
    latex = emit_latex(tables, status, "max z = 2x_1 + 3x_2", 4)
    issues, notes, checked = tc.verify(latex)
    T.ok(checked, "识别成功")
    T.ok(any("对不上" in i or "编造" in i for i in issues),
         f"跨表验算抓到编数（实际：{issues}）")


def test_repair_sigma():
    print("== σ 行数字算错（数据行全对）→ repair_sigma 直接修好 ==")
    # σ2: 2 → 5，两种约定下都对不上，是真正的算术错误
    bad = GOOD_SAMPLE.replace("$3$ & $2$", "$3$ & $5$")
    issues, _, checked = tc.verify(bad)
    T.ok(checked and any("σ(x2)" in i for i in issues),
         f"坏 σ 行被抓（实际：{issues}）")
    fixed, notes = tc.repair_sigma(bad)
    issues2, _, _ = tc.verify(fixed)
    T.ok(not issues2, f"修复后零 issue（实际：{issues2}）")
    T.ok(any("重算回填" in n for n in notes), f"有修复说明（{notes}）")
    # 附：纯约定漂移（z−c 但选择自洽）不算错 —— 防噪音，是设计行为
    drift = GOOD_SAMPLE.replace("$3$ & $2$", "$-3$ & $-2$") \
                       .replace("$x_1$ 入基", "$x_1$ 入基")
    issues3, _, _ = tc.verify(drift)
    T.ok(not issues3, "纯约定漂移+选择自洽 → 不报错")


def test_decimal_tolerance():
    print("== 十进制小数 ↔ 分数（39/80 ↔ 0.4875）不误报 ==")
    t = BROKEN_SAMPLE.replace("0 & $x_3$ & 4", "0 & $x_3$ & 4")
    # 把好样本 θ=6 那行换成 39/80 ↔ 0.4875 场景：构造一个小表
    latex = r"""\begin{tabular}{cccccc}
$C_B$ & $X_B$ & $b$ & 3 & 2 & $\theta$ \\
 & & & $x_1$ & $x_2$ & \\
\midrule
0 & $x_1$ & 0.4875 & 1 & 0 & \\
\midrule
$\sigma_j$ & 0 & 0 & 0 & & \\
\bottomrule
\end{tabular}"""
    # 只验解析层：39/80 == 0.4875
    T.ok(tc.eq(F(39, 80), tc.parse_number("0.4875")), "0.4875 == 39/80")
    T.ok(tc.eq(F(1, 3), tc.parse_number("0.333")), "0.333 ≈ 1/3（容差）")
    T.ok(not tc.eq(F(1, 3), tc.parse_number("0.334")), "0.334 ≠ 1/3（不许放过编数）")


def test_transport_table_not_flagged():
    print("== 运输问题的表（无 σ 行）绝不能误报 ==")
    latex = r"""\begin{tabular}{lcccc}
\toprule
  & $B_1$ & $B_2$ & $B_3$ & 产量 \\
\midrule
$A_1$ & 4 & 8 & 8 & 76 \\
$A_2$ & 16 & 24 & 16 & 82 \\
$A_3$ & 8 & 16 & 24 & 77 \\
销量 & 72 & 102 & 41 &  \\
\bottomrule
\end{tabular}"""
    issues, notes, checked = tc.verify(latex)
    T.ok(not issues, f"零 issue（实际：{issues}）")
    T.ok(not checked, "明确按未校验处理")


def test_phase_switch_skipped():
    print("== 两阶段法阶段切换（基变量跳变 >1）跳过跨表验算，不误报 ==")
    tables, status = solve_max([2, 3], [[1, 1], [1, 3]], [4, 6])
    annotate(tables)
    _CJ_OF.clear()
    for i in range(3):
        _CJ_OF[i] = [F(2), F(3), F(0), F(0)]
    latex = emit_latex(tables, status, "max z = 2x_1 + 3x_2", 4)
    # 删掉中间表 → 第 1 张直接跳到第 3 张（基变 2 个）
    parts = latex.split(r"\end{tabular}")
    shortened = (parts[0] + r"\end{tabular}" + "\n（第二张表略）\n"
                 + parts[2] + r"\end{tabular}" + parts[3])
    issues, notes, checked = tc.verify(shortened)
    T.ok(checked, "识别成功")
    T.ok(not issues, f"跳步不报错（实际：{issues}）")
    T.ok(any("跳过跨表验算" in n for n in notes), f"有跳过说明（{notes[:2]}）")


def test_degenerate():
    print("== 退化问题（θ=0 行）不误报 ==")
    # max z = x1+x2, x1+x2≤4, x1≤2, x2≤2 → 第二轮起出现 b=0
    tables, status = solve_max([1, 1], [[1, 1], [1, 0], [0, 1]], [4, 2, 2])
    annotate(tables)
    _CJ_OF.clear()
    for i in range(len(tables)):
        _CJ_OF[i] = [F(1), F(1), F(0), F(0), F(0)]
    latex = emit_latex(tables, status, "max z = x_1 + x_2", 5)
    issues, notes, checked = tc.verify(latex)
    T.ok(checked, "识别成功")
    T.ok(not issues, f"零误报（实际：{issues}；notes：{notes[:2]}）")


def test_multiple_optima():
    print("== 多重最优解（最终表有非基 σ=0）不误报 ==")
    # max z = 2x1+2x2, x1+x2≤4, x1≤2 → 最终 σ 有 0
    tables, status = solve_max([2, 2], [[1, 1], [1, 0]], [4, 2])
    annotate(tables)
    _CJ_OF.clear()
    for i in range(len(tables)):
        _CJ_OF[i] = [F(2), F(2), F(0), F(0)]
    latex = emit_latex(tables, status, "max z = 2x_1 + 2x_2", 4)
    issues, notes, checked = tc.verify(latex)
    T.ok(checked, "识别成功")
    T.ok(not issues, f"零误报（实际：{issues}；notes：{notes[:2]}）")


def test_robust_layouts():
    print("== 排版变体：resizebox / array / unicode 减号 / 无 c_B 列 ==")
    # resizebox 包裹 + array 环境 + unicode −
    latex = r"""\resizebox{\textwidth}{!}{
\begin{array}{ccccccc}
\toprule
 & $X_B$ & $b$ & 3 & 2 & $\theta$ & \\
 & & & $x_1$ & $x_2$ & & \\
\midrule
0 & $x_3$ & 4 & 1 & 1 & 4 & \\
\midrule
$\sigma_j$ & 0 & 3 & 2 & & \\
\bottomrule
\end{array}}$x_1$ 入基"""
    issues, notes, checked = tc.verify(latex)
    T.ok(checked, f"resizebox+array 识别成功（notes: {notes[:1]}）")
    T.ok(not issues, f"零误报（实际：{issues}）")


def test_never_raises():
    print("== 喂垃圾不炸 ==")
    for s in ["", "乱码", r"\begin{tabular}{c}a\end{tabular}",
              r"\begin{tabular}{cc}" + "&" * 200 + r"\end{tabular}",
              "$\\frac{}{}$"]:
        tc.verify(s)
        tc.repair_sigma(s)
    T.ok(True, "垃圾输入全程无异常")


if __name__ == "__main__":
    for fn in [test_broken_sample, test_good_sample, test_solver_end_to_end,
               test_fabricated_number, test_repair_sigma, test_decimal_tolerance,
               test_transport_table_not_flagged, test_phase_switch_skipped,
               test_degenerate, test_multiple_optima, test_robust_layouts,
               test_never_raises]:
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
