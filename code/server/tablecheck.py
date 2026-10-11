"""单纯形表算术自检 —— 把「会画表」从「格式好看」升级成「数字可验证」。

背景：模型画的单纯形表经常格式漂亮但数字是编的（真实案例：σ 行约定漂移、
入基变量选错、无可行解硬凑最优解）。评卷就是照着表给的，数字错 = 整个回答报废。

本模块做三件事：
  1. 解析：从 LaTeX 正文里抠出 tabular 单纯形表，所有数字转成精确分数（Fraction）
  2. 验算：表内一致性（σ 行 / z 值 / θ 列 / c_B 列 / 入基出基选择）+ 跨表换基验算
     （穷举主元重算，能从上一张表算出下一张才算通过 —— 不依赖任何约定）
  3. 修复：数据行（b / A / c_B / 基）本身一致时，直接用代码重算 σ 行回填；
     数据行错时生成错误清单交给模型改（revise 回调由 main 注入，避免循环导入）

设计红线（都是防误伤）：
  - 运输问题 / 指派问题的表、普通数据表：识别不出单纯形结构就跳过，绝不报错
  - 两阶段法的阶段切换、跳步（基变量一次变多于 1 个）：跳过该对表的跨表验算
  - 本模块任何函数都不许抛异常打断发消息主流程 —— 上层 catch 后放行原文

σ 行约定问题：国内教材主流是 σ_j = c_j − z_j，但模型有时写 z_j − c_j（数值自洽）。
验算器对两种约定做投票（基变量列的 σ 必为 0，两种约定下都成立，是铁锚点），
按表内占优的约定验数值，并在约定与主流相反时提示模型统一（下游入基选择跟着约定走）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

# ============================================================
#  数字 / 符号解析
# ============================================================

# 模型偶尔输出 unicode 减号/连接号，统一归一成 ASCII 减号
_MINUS_MAP = str.maketrans({
    "\u2212": "-",   # − math minus
    "\u2013": "-",   # – en dash
    "\u2014": "-",   # — em dash
    "\uff0b": "+",   # ＋ fullwidth plus
})

_FRAC_RE = re.compile(r"\\[td]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")

# 普通数字：整数 / 小数 / 简单分数 a/b（允许外层括号）
_PLAIN_NUM_RE = re.compile(r"^\(?[+-]?\d+(?:\.\d+)?(?:\s*/\s*\(?[+-]?\d+(?:\.\d+)?\)?)?\)?$")

# 变量名：x_1 / x1 / x_{1} / ${x}_{1}$（剥掉 $ 后剩 {x}_{1}）/ s_2 等，归一成 x1。
# 注意 X_B、C_B 这种两字母标签不能匹配（下划线后跟的不是数字）。
_VAR_RE = re.compile(
    r"^[{\s]*(?:\\(?:mathbf|mathrm|bold|bm)\s*\{)?[{\s]*([xswuay])[}\s]*"
    r"(?:[_\s]*[{\s]*(\d+)[}\s]*)?[}\s]*$",
    re.IGNORECASE,
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _strip_decor(cell: str) -> str:
    """去掉数学模式定界符、加粗、字号命令等外壳，只留内容。"""
    s = cell.strip().translate(_MINUS_MAP)
    for _ in range(3):
        s2 = s.strip()
        changed = False
        for wrapper in ("$", "\\(", "\\)"):
            while s2.startswith(wrapper) and s2.endswith(wrapper) \
                    and len(s2) >= 2 * len(wrapper):
                s2 = s2[len(wrapper): -len(wrapper)].strip()
                changed = True
        for cmd in ("\\textbf", "\\mathbf", "\\mathrm", "\\boldsymbol",
                    "\\text", "\\bm"):
            if s2.startswith(cmd + "{") and s2.endswith("}"):
                s2 = s2[len(cmd) + 1: -1].strip()
                changed = True
        # \left/\right 单删（\leftarrow 会剩下 arrow，无害）
        for cmd in ("\\small", "\\large", "\\footnotesize", "\\scriptsize",
                    "\\left", "\\right", "\\!", "\\,", "\\;", "\\:", "~"):
            if cmd in s2:
                s2 = s2.replace(cmd, " ").strip()
                changed = True
        s = s2
        if not changed:
            break
    return s.strip()


def _num_piece(s: str) -> Fraction:
    """解析整数/小数片段为 Fraction（十进制小数精确表示）。"""
    s = s.strip().strip("()").strip()
    if "." in s:
        return Fraction(s)
    return Fraction(int(s))


def _frac_from_tex(text: str) -> Fraction | None:
    """清洗后的文本 → 精确分数。失败返回 None。"""
    s = text.strip()
    if not s:
        return None
    # \frac{a}{b} → (a)/(b)（反复替换应对嵌套两层）
    def _sub(m: re.Match) -> str:
        return f"({m.group(1).strip()})/({m.group(2).strip()})"
    prev = None
    while prev != s:
        prev = s
        s = _FRAC_RE.sub(_sub, s)
    s = s.strip().strip("()").strip()
    if not _PLAIN_NUM_RE.match(s.replace(" ", "")):
        return None
    s = s.replace(" ", "")
    try:
        if "/" in s:
            parts = s.split("/")
            if len(parts) != 2:
                return None
            return _num_piece(parts[0]) / _num_piece(parts[1])
        return _num_piece(s)
    except (ZeroDivisionError, ValueError, ArithmeticError):
        return None


def parse_number(cell: str) -> Fraction | None:
    """表格单元格 → 精确分数。不是纯数字（变量名/中文/空/箭头）返回 None。"""
    s = _strip_decor(cell)
    if not s or _CJK_RE.search(s):
        return None
    return _frac_from_tex(s)


def normalize_var(text: str) -> str | None:
    """变量名归一：x_1 / x1 / x_{1} / X_1 → x1；不是变量名返回 None。"""
    s = _strip_decor(text)
    if not s or _CJK_RE.search(s):
        return None
    m = _VAR_RE.match(s)
    if not m:
        return None
    return m.group(1).lower() + (m.group(2) or "")


def fmt_frac(v: Fraction) -> str:
    """Fraction → 给模型/日志看的数字文本（整数直接给，分数给 a/b）。"""
    if v.denominator == 1:
        return str(v.numerator)
    return f"{v.numerator}/{v.denominator}"


def _decimal_digits(v: Fraction) -> int | None:
    """v 是纯十进制小数（分母只有 2/5 因子）时返回小数位数；否则 None。"""
    d = v.denominator
    i = j = 0
    while d % 2 == 0:
        d //= 2
        i += 1
    while d % 5 == 0:
        d //= 5
        j += 1
    if d != 1:
        return None
    return max(i, j)


def _trunc(v: Fraction, digits: int) -> Fraction:
    """向零截断到 digits 位小数（模型写 39/80≈0.4875、2/7≈0.2857 就是这么来的）。"""
    scaled = abs(v) * 10 ** digits
    q = scaled.numerator // scaled.denominator
    out = Fraction(q, 10 ** digits)
    return -out if v < 0 else out


def eq(a: Fraction, b: Fraction) -> bool:
    """分数相等，带「十进制截断/舍入感知」：
    - 一边是有限小数、另一边不是：小数必须是真值的直接截断，
      或与真值差严格小于半个末位（标准四舍五入）—— 0.333 ↔ 1/3 过，0.334 拦
    - 两边都是有限小数：严格比对（对不上就是真错）
    - 整数没有舍入一说（4.0 ≠ 5）
    """
    if a == b:
        return True
    da, db = _decimal_digits(a), _decimal_digits(b)
    if da is not None and db is None and da > 0:
        return a == _trunc(b, da) or abs(a - b) < Fraction(1, 2 * 10 ** da)
    if db is not None and da is None and db > 0:
        return b == _trunc(a, db) or abs(a - b) < Fraction(1, 2 * 10 ** db)
    return False


# ============================================================
#  行级解析
# ============================================================

# 整行级别的横线/装饰命令：解析单元格时抹掉。
# ⚠️ 不能在定位行边界之前全文删除 —— 会挪动行偏移，导致回填错位。
_ROW_NOISE = ("\\toprule", "\\midrule", "\\bottomrule", "\\hline",
              "\\rowcolor", "\\arrayrulecolor", "\\specialrule", "\\noalign")

_CELLINE_RE = re.compile(r"\\(?:cmidrule|cline)\s*(?:\([lr]{1,2}\))?\{[^{}]*\}")
_MULTICOL_RE = re.compile(r"\\multicolumn\s*\{\s*(\d+)\s*\}\s*\{[^{}]*\}\s*\{")


@dataclass
class RawRow:
    """原始行：单元格原文 + 在整篇 latex 里的偏移区间（供代码级修复回填）。"""
    cells: list[str]
    start: int
    end: int
    nums: list[Fraction | None] = field(default_factory=list)
    syms: list[str | None] = field(default_factory=list)


def _expand_multicolumn(row_text: str) -> str:
    r"""\multicolumn{n}{c}{内容} → 内容（其余 n-1 格补空）。"""
    s = row_text
    for _ in range(4):
        m = _MULTICOL_RE.search(s)
        if not m:
            break
        depth, i = 1, m.end()
        while i < len(s) and depth:
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
            i += 1
        inner = s[m.end(): i - 1]
        n = max(1, int(m.group(1)))
        s = s[: m.start()] + " & ".join([inner] + [""] * (n - 1)) + s[i:]
    return s


def _split_rows(body: str, base: int) -> list[RawRow]:
    """把 tabular 正文切成行。base = body 在整篇 latex 里的起始偏移，
    RawRow.start/end 直接是整篇 latex 的偏移，回填时零换算。"""
    rows: list[RawRow] = []
    pos = 0
    bounds: list[tuple[int, int]] = []
    for m in re.finditer(r"\\\\\*?(?:\[[^\]]*\])?", body):
        bounds.append((pos, m.start()))
        pos = m.end()
    bounds.append((pos, len(body)))

    for (a, b) in bounds:
        text = body[a:b]
        if not text.strip():
            continue
        for tok in _ROW_NOISE:
            text = text.replace(tok, "")
        text = _CELLINE_RE.sub("", text)
        text = _expand_multicolumn(text)
        cells = [c.strip() for c in text.split("&")]
        if all(not c for c in cells):
            continue
        rows.append(RawRow(cells=cells, start=base + a, end=base + b))
    return rows


# ============================================================
#  表结构识别
# ============================================================

_TABULAR_RE = re.compile(
    r"\\begin\{(tabular\*?|array)\}\s*(?:\{[^{}]*\})?(.*?)\\end\{\1\}",
    re.DOTALL,
)

_SIGMA_TOKENS = ("\\sigma", "σ", "检验数")
_ENTER_RE = re.compile(
    r"([a-zA-Z])\s*_?\s*\{?\s*(\d+)\s*\}?\s*(?:[\s$\\,，;；]*)*(入基|进基|换入)")
_LEAVE_RE = re.compile(
    r"([a-zA-Z])\s*_?\s*\{?\s*(\d+)\s*\}?\s*(?:[\s$\\,，;；]*)*(出基|退基|换出|离基)")
_OBJ_RE = re.compile(r"\b(max|min)\b", re.IGNORECASE)


@dataclass
class ParsedTable:
    """解析成功的单纯形表。认不出结构时 parse 返回 None（不当单纯形表处理）。"""
    span: tuple[int, int]
    rows: list[RawRow]
    var_names: list[str] = field(default_factory=list)     # A 列变量名（列序）
    c_j: list[Fraction | None] = field(default_factory=list)
    basis: list[str] = field(default_factory=list)
    c_B: list[Fraction | None] = field(default_factory=list)
    b: list[Fraction] = field(default_factory=list)
    A: list[list[Fraction]] = field(default_factory=list)
    theta: list[Fraction | None] = field(default_factory=list)
    sigma_nums: list[Fraction] = field(default_factory=list)
    sigma_row: RawRow | None = None
    z_value: Fraction | None = None
    obj_dir: str | None = None
    entering_declared: str | None = None
    leaving_declared: str | None = None
    claimed_optimal: bool = False            # 表后文字宣布了「最优」


def _classify_rows(rows: list[RawRow]) -> tuple[list[int], list[int], int | None]:
    """行分类 → (表头行下标, 数据行下标, σ 行下标)。

    数据行的硬标准：前 3 个非空格里有变量名，且整行至少 3 个数字格
    （b 和若干系数）。表头的变量名行（x1 x2 x3...）一个数字格都没有，
    靠这个跟数据行区分开 —— 认错会把表头当数据行，整表报废。
    """
    header, data, sigma = [], [], None
    for i, r in enumerate(rows):
        nonempty = [c for c in r.cells if c]
        if not nonempty:
            continue
        joined = " ".join(r.cells)
        if any(tok in joined for tok in _SIGMA_TOKENS):
            sigma = i
            continue
        first_sym = None
        for c in nonempty[:3]:
            sym = normalize_var(c)
            if sym:
                first_sym = sym
                break
        n_num = sum(1 for c in r.cells if parse_number(c) is not None)
        if first_sym and len(r.cells) >= 4 and n_num >= 3:
            data.append(i)
        elif sum(1 for c in r.cells if normalize_var(c)) >= 2:
            header.append(i)
    return header, data, sigma


def _find_cj_row(rows: list[RawRow], header_idx: list[int],
                 var_row_i: int) -> list[Fraction | None] | None:
    """变量名行紧邻的「纯数字、无变量名」行是 c_j 行（通常紧贴在上一行）。

    两条硬门槛（都是拿真实误报换来的）：
    - 候选行里出现任何变量名格 → 它是数据行/表头，不是 c_j
      （不挡的话，下面那行数据 b/A 会被当成 c_j，整张表的 σ 全部冤判）
    - 候选行是 σ 行（含 σ/检验数）→ 跳过
    """
    for cand in (var_row_i - 1, var_row_i + 1):
        if cand < 0 or cand >= len(rows) or cand in header_idx:
            continue
        r = rows[cand]
        joined = " ".join(r.cells)
        if any(tok in joined for tok in _SIGMA_TOKENS):
            continue
        if any(normalize_var(c) for c in r.cells):
            continue
        nums = [parse_number(c) for c in r.cells]
        good = [x for x in nums if x is not None]
        if len(good) >= 2:
            return nums
    return None


def _parse_one_table(latex: str, span: tuple[int, int],
                     rows: list[RawRow]) -> ParsedTable | None:
    header_idx, data_idx, sigma_idx = _classify_rows(rows)
    if not data_idx or sigma_idx is None:
        return None

    pt = ParsedTable(span=span, rows=rows)
    pt.sigma_row = rows[sigma_idx]

    # ---- 变量名列 & c_j 行 ----
    best = None
    for i in header_idx:
        names = [normalize_var(c) for c in rows[i].cells]
        named = [x for x in names if x]
        if best is None or len(named) > best[1]:
            best = (i, len(named), names)
    if best is None:
        return None
    var_names_all = best[2]
    pt.var_names = [x for x in var_names_all if x]
    n = len(pt.var_names)
    if n < 2:
        return None
    cj = _find_cj_row(rows, header_idx, best[0])
    if cj is not None:
        pos_of = [k for k, nm in enumerate(var_names_all) if nm]
        pt.c_j = [cj[k] if k < len(cj) else None for k in pos_of]

    # ---- 数据行：布局 [c_B?, 基, b, A(n), θ?]。
    # 每行独立判布局：看最后一个数字格的位置。θ 列后面常跟着 ← 箭头等
    # 非数字装饰格，必须按「最后一个数字格」数宽度，不能按总格数。----
    for r in (rows[i] for i in data_idx):
        nums = [parse_number(c) for c in r.cells]
        syms = [normalize_var(c) for c in r.cells]
        r.nums, r.syms = nums, syms
        last_num = max((k for k, v in enumerate(nums) if v is not None), default=-1)
        if last_num < 0:
            return None
        eff = last_num + 1                      # 有效宽度（剥掉尾部箭头等）
        basis_sym = next((s for s in syms if s), None)
        if basis_sym is None:
            return None
        pt.basis.append(basis_sym)

        if eff == n + 4:                        # c_B, 基, b, A, θ
            cb, b_idx, th = nums[0], 2, nums[last_num]
        elif eff == n + 3 and nums[0] is not None:   # c_B, 基, b, A（没 θ）
            cb, b_idx, th = nums[0], 2, None
        elif eff == n + 3:                      # 基, b, A, θ（没 c_B）
            cb, b_idx, th = None, 1, nums[last_num]
        elif eff == n + 2:                      # 基, b, A（都没）
            cb, b_idx, th = None, 1, None
        else:
            return None                         # 布局对不上号 → 不硬验
        if b_idx >= len(nums) or nums[b_idx] is None:
            return None
        a_vals = nums[b_idx + 1: b_idx + 1 + n]
        if len(a_vals) != n or any(v is None for v in a_vals):
            return None
        pt.c_B.append(cb)
        pt.b.append(nums[b_idx])
        pt.A.append([Fraction(v) for v in a_vals])
        pt.theta.append(th)

    # ---- σ 行数字 ----
    for c in pt.sigma_row.cells:
        v = parse_number(c)
        if v is not None:
            pt.sigma_nums.append(v)
    if len(pt.sigma_nums) < n:
        return None

    # ---- 目标方向 / 入基出基声明（从表前后文字抓）----
    before = latex[max(0, span[0] - 400): span[0]]
    after = latex[span[1]: span[1] + 300]
    mo = _OBJ_RE.search(before)
    if mo:
        pt.obj_dir = mo.group(1).lower()
    me = _ENTER_RE.search(after)
    ml = _LEAVE_RE.search(after)
    if me:
        pt.entering_declared = (me.group(1) + me.group(2)).lower()
    if ml:
        pt.leaving_declared = (ml.group(1) + ml.group(2)).lower()
    pt.claimed_optimal = "最优" in after
    return pt


def parse_simplex_tables(latex: str) -> tuple[list[ParsedTable], list[str]]:
    """解析正文里所有「能认成单纯形表」的 tabular。
    返回 (tables, notes)；认不出结构的不进 tables，原因进 notes。"""
    tables: list[ParsedTable] = []
    notes: list[str] = []
    for m in _TABULAR_RE.finditer(latex):
        body, base = m.group(2), m.start(2)
        try:
            rows = _split_rows(body, base)
            if len(rows) < 3:
                continue
            pt = _parse_one_table(latex, m.span(), rows)
        except Exception as exc:               # 解析绝不打断主流程
            notes.append(f"tabular 解析异常（跳过）：{exc!r}")
            continue
        if pt is None:
            notes.append("跳过一个 tabular：不像单纯形表（缺 σ 行或结构对不上）")
        else:
            tables.append(pt)
    return tables, notes


# ============================================================
#  验算
# ============================================================

@dataclass
class Report:
    issues: list[str] = field(default_factory=list)   # 给模型/日志看的错误清单
    notes: list[str] = field(default_factory=list)    # 跳过/未验说明（不算错）


def _z_values(pt: ParsedTable) -> list[Fraction]:
    """z_j = Σ c_B_i · A_ij。c_B 缺的行按 0 处理。"""
    z = [Fraction(0)] * len(pt.var_names)
    for i in range(len(pt.basis)):
        if pt.c_B[i] is None:
            continue
        for j in range(len(z)):
            z[j] += pt.c_B[i] * pt.A[i][j]
    return z


def _sigma_expected(pt: ParsedTable, convention: str) -> list[Fraction] | None:
    """按约定算 σ 应有值。convention: 'c-z' / 'z-c'。缺 c_j 时算不了。"""
    if not pt.c_j or any(v is None for v in pt.c_j):
        return None
    z = _z_values(pt)
    return [Fraction(pt.c_j[j]) - z[j] if convention == "c-z"
            else z[j] - Fraction(pt.c_j[j])
            for j in range(len(pt.var_names))]


def _detect_sigma(pt: ParsedTable) -> tuple[str | None, Fraction | None, list[Fraction]]:
    """σ 约定投票 + 对齐。返回 (convention, z 值, 对齐后的 σ 序列)。

    锚点：基变量列的 σ 必为 0（两种约定下都成立），权重最高；
    其次逐列跟两种约定的期望值比对。σ 行可能比数据行短（没有 θ 格），
    左对齐 / 右对齐都试，哪个得分高用哪个。
    """
    n = len(pt.var_names)
    raw = pt.sigma_nums
    cands: list[tuple[str, list[Fraction], list[Fraction] | None]] = []
    if len(raw) >= n:
        cands.append(("right", raw[-n:], raw[:-n] or None))
        cands.append(("left", raw[:n], raw[n:] or None))
    best = None
    for align, sig, rest in cands:
        for conv in ("c-z", "z-c"):
            score = 0
            for bvar in pt.basis:
                if bvar in pt.var_names:
                    j = pt.var_names.index(bvar)
                    if sig[j] == 0:
                        score += 2
                    else:
                        score -= 2
            exp = _sigma_expected(pt, conv)
            if exp is not None:
                for j in range(n):
                    if eq(sig[j], exp[j]):
                        score += 1
            if best is None or score > best[0]:
                best = (score, conv, align, sig, rest)
    if best is None:
        return None, None, raw[-n:]
    zval = best[4][0] if best[4] and len(best[4]) == 1 else None
    return best[1], zval, best[3]


def _basic_js(pt: ParsedTable) -> set[int]:
    return {pt.var_names.index(b) for b in pt.basis if b in pt.var_names}


def _check_single_table(pt: ParsedTable, rep: Report) -> None:
    """表内一致性验算。"""
    n = len(pt.var_names)
    conv, zval, sig = _detect_sigma(pt)

    # 1) c_B 列 vs c_j 表头
    if pt.c_j and all(v is not None for v in pt.c_j):
        for i, bvar in enumerate(pt.basis):
            if bvar in pt.var_names and pt.c_B[i] is not None:
                j = pt.var_names.index(bvar)
                if not eq(Fraction(pt.c_B[i]), Fraction(pt.c_j[j])):
                    rep.issues.append(
                        f"第 {i + 1} 行基变量 {bvar} 的 c_B 写的是 {fmt_frac(Fraction(pt.c_B[i]))}，"
                        f"但 c_j 表头里 {bvar} 的系数是 {fmt_frac(Fraction(pt.c_j[j]))}，对不上")

    # 2) z 值 = Σ c_B·b
    z_calc = sum((pt.c_B[i] or Fraction(0)) * pt.b[i]
                 for i in range(len(pt.basis)))
    if zval is not None and not eq(zval, z_calc):
        rep.issues.append(
            f"目标值 z 写的是 {fmt_frac(zval)}，按 c_B·b 重算应是 {fmt_frac(z_calc)}")

    # 3) σ 行逐列验（按投票选出的约定）
    exp = _sigma_expected(pt, conv) if conv else None
    if exp is not None:
        bad = [(j, sig[j], exp[j]) for j in range(n) if not eq(sig[j], exp[j])]
        if bad:
            other = "z-c" if conv == "c-z" else "c-z"
            exp_other = _sigma_expected(pt, other)
            if exp_other is not None and all(eq(sig[j], exp_other[j]) for j in range(n)):
                # 数值是另一种约定的 —— 提示统一，不算逐列数值错
                rep.issues.append(
                    "σ 行按 σ=z−c 约定写的；主流教材是 σ=c−z（应为 "
                    f"{', '.join(fmt_frac(v) for v in exp_other)}）。"
                    "请全文统一 σ=c−z，并据此重算入基选择和后续所有表")
            else:
                for j, got, want in bad:
                    rep.issues.append(
                        f"σ({pt.var_names[j]}) 写的是 {fmt_frac(got)}，"
                        f"按 σ=c−z 重算应是 {fmt_frac(want)}")

    # 4) 入基列：声明的优先，否则 θ 列反推，否则按约定推
    enter_j = _infer_entering(pt, conv, sig)
    if enter_j is not None:
        _check_theta(pt, rep, enter_j)
        _check_choices(pt, rep, enter_j, conv, sig)

    # 5) 宣布了「最优」却还有改善空间 —— 无可行解/漏迭代硬凑最优都栽在这
    _check_optimality(pt, rep, conv, sig)


def _check_optimality(pt: ParsedTable, rep: Report,
                      conv: str | None, sig: list[Fraction]) -> None:
    if not pt.claimed_optimal or not conv or not pt.obj_dir:
        return
    exp = _sigma_expected(pt, conv)
    if exp is None or not all(eq(sig[j], exp[j]) for j in range(len(sig))):
        return          # σ 数值本身有错已单独报过，这里不叠加
    pick_max = (conv == "c-z") == (pt.obj_dir == "max")
    basic = _basic_js(pt)
    for j in range(len(sig)):
        if j in basic:
            continue
        if (pick_max and sig[j] > 0) or (not pick_max and sig[j] < 0):
            rep.issues.append(
                f"宣布了最优，但 σ({pt.var_names[j]})={fmt_frac(sig[j])} 还能继续改善"
                f"（{pt.obj_dir} 问题、σ={'c−z' if conv == 'c-z' else 'z−c'} 约定下"
                f"{'>0' if pick_max else '<0'} 就该继续迭代）—— "
                "检查是不是漏了迭代；若题目本身无可行解或无界，请明确说明，别硬凑解")
            return


def _infer_entering(pt: ParsedTable, conv: str | None,
                    sig: list[Fraction]) -> int | None:
    """推断这张表用的入基列：声明 > θ 列反推 > 约定推。"""
    n = len(pt.var_names)
    if pt.entering_declared and pt.entering_declared in pt.var_names:
        return pt.var_names.index(pt.entering_declared)
    if pt.theta and any(t is not None for t in pt.theta):
        for j in range(n):
            ok, used = True, 0
            for i, t in enumerate(pt.theta):
                if t is None:
                    continue
                a = pt.A[i][j]
                if a == 0:
                    if not eq(t, Fraction(0)):
                        ok = False
                        break
                    continue
                used += 1
                if not eq(t, pt.b[i] / a):
                    ok = False
                    break
            if ok and used >= 1:
                return j
    basic = _basic_js(pt)
    nonbasic = [j for j in range(n) if j not in basic and sig[j] != 0]
    if conv and nonbasic and pt.obj_dir:
        pick_max = (conv == "c-z") == (pt.obj_dir == "max")
        return (max(nonbasic, key=lambda j: sig[j]) if pick_max
                else min(nonbasic, key=lambda j: sig[j]))
    return None


def _check_theta(pt: ParsedTable, rep: Report, enter_j: int) -> None:
    """θ 列 = b_i / a_i,entering。"""
    if not pt.theta or all(t is None for t in pt.theta):
        return
    for i, t in enumerate(pt.theta):
        if t is None:
            continue
        a = pt.A[i][enter_j]
        if a == 0:
            if not eq(t, Fraction(0)):
                rep.issues.append(
                    f"第 {i + 1} 行（{pt.basis[i]}）在 {pt.var_names[enter_j]} 列系数为 0，"
                    f"θ 不该有比值，表里写了 {fmt_frac(t)}")
            continue
        want = pt.b[i] / a
        if not eq(t, want):
            rep.issues.append(
                f"θ 列第 {i + 1} 行写的是 {fmt_frac(t)}，按 b/a 重算应是 {fmt_frac(want)}"
                f"（b={fmt_frac(pt.b[i])}，a={fmt_frac(a)}）")


def _check_choices(pt: ParsedTable, rep: Report, enter_j: int,
                   conv: str | None, sig: list[Fraction]) -> None:
    """入基/出基声明 vs 规则。σ 行数值有错时不报选择错（避免噪音）。"""
    n = len(pt.var_names)
    exp = _sigma_expected(pt, conv) if conv else None
    if exp is None or not all(eq(sig[j], exp[j]) for j in range(n)):
        return
    if not pt.obj_dir:
        return
    basic = _basic_js(pt)
    nonbasic = [j for j in range(n) if j not in basic and sig[j] != 0]

    if pt.entering_declared and nonbasic:
        pick_max = (conv == "c-z") == (pt.obj_dir == "max")
        want_j = (max(nonbasic, key=lambda j: sig[j]) if pick_max
                  else min(nonbasic, key=lambda j: sig[j]))
        decl_j = pt.var_names.index(pt.entering_declared)
        if decl_j != want_j:
            rep.issues.append(
                f"入基选择可疑：按 σ={'c−z' if conv == 'c-z' else 'z−c'} 约定和 "
                f"{pt.obj_dir} 问题，应选 σ 最{'大' if pick_max else '小'}的非基变量 "
                f"{pt.var_names[want_j]}（σ={fmt_frac(sig[want_j])}），"
                f"而不是 {pt.entering_declared}（σ={fmt_frac(sig[decl_j])}）")

    if pt.leaving_declared and pt.leaving_declared in pt.basis \
            and pt.theta and any(t is not None for t in pt.theta):
        cand = [(pt.theta[i], i) for i in range(len(pt.basis))
                if pt.theta[i] is not None and pt.A[i][enter_j] > 0]
        if cand:
            best = min(cand, key=lambda x: x[0])
            decl_i = pt.basis.index(pt.leaving_declared)
            ties = [i for t, i in cand if t == best[0]]
            if decl_i not in ties:
                rep.issues.append(
                    f"出基选择可疑：θ 最小的是 {pt.basis[best[1]]}（θ={fmt_frac(best[0])}），"
                    f"你选的 {pt.leaving_declared} 的 θ={fmt_frac(pt.theta[decl_i])}，不是最小")


# ============================================================
#  跨表换基验算
# ============================================================

def _pivot(T: ParsedTable, r: int, j: int):
    """在 T 上以 (r, j) 为主元转一轮 → (basis, c_B, b, A)。主元为 0 返回 None。"""
    p = T.A[r][j]
    if p == 0:
        return None
    basis = list(T.basis)
    basis[r] = T.var_names[j]
    cB = list(T.c_B)
    if T.c_j and T.c_j[j] is not None:
        cB[r] = Fraction(T.c_j[j])
    b = [T.b[i] - T.A[i][j] * (T.b[r] / p) for i in range(len(basis))]
    b[r] = T.b[r] / p
    A = []
    for i in range(len(basis)):
        if i == r:
            A.append([T.A[i][k] / p for k in range(len(T.var_names))])
        else:
            A.append([T.A[i][k] - T.A[i][j] * (T.A[r][k] / p)
                      for k in range(len(T.var_names))])
    return basis, cB, b, A


def _basis_distance(a: list[str], b: list[str]) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


def check_cross_tables(tables: list[ParsedTable], rep: Report) -> None:
    """相邻两表：穷举主元，存在一组 (r, j) 能从上一张转到下一张才算通过。

    基变量差 >1 个 → 阶段切换 / 跳步，跳过不验（防误伤，两阶段法必踩）。"""
    for k in range(len(tables) - 1):
        prev, cur = tables[k], tables[k + 1]
        if len(prev.basis) != len(cur.basis) or prev.var_names != cur.var_names:
            rep.notes.append(f"第 {k + 1}→{k + 2} 张表结构不同（阶段切换？），跳过跨表验算")
            continue
        d = _basis_distance(prev.basis, cur.basis)
        if d == 0:
            continue
        if d > 1:
            rep.notes.append(
                f"第 {k + 1}→{k + 2} 张表基变量换了 {d} 个（跳步/阶段切换），跳过跨表验算")
            continue
        ok = False
        for r in range(len(prev.basis)):
            for j in range(len(prev.var_names)):
                res = _pivot(prev, r, j)
                if res is None or res[0] != cur.basis:
                    continue
                _, _, b, A = res
                if all(eq(b[i], cur.b[i]) for i in range(len(b))) and all(
                        eq(A[i][jj], cur.A[i][jj])
                        for i in range(len(b)) for jj in range(len(prev.var_names))):
                    ok = True
                    break
            if ok:
                break
        if not ok:
            rep.issues.append(
                f"第 {k + 1} 张表到第 {k + 2} 张表对不上：以第 {k + 1} 张的任何一格为主元"
                f"转一轮，都算不出第 {k + 2} 张的数字 —— 换基过程有编造")


# ============================================================
#  代码级修复：数据行一致时，重算 σ 行 / z 值并回填
# ============================================================

def build_repaired_sigma_row(pt: ParsedTable) -> str | None:
    """按主流 σ=c−z 重算 σ 行的 LaTeX（保留原行的标签格与 $ 包裹风格）。"""
    if pt.sigma_row is None:
        return None
    exp = _sigma_expected(pt, "c-z")
    if exp is None:
        return None
    cells = list(pt.sigma_row.cells)
    label_cells = 0
    for c in cells:
        if parse_number(c) is None:
            label_cells += 1
        else:
            break
    if label_cells >= len(cells):
        return None
    n = len(pt.var_names)
    z_calc = sum((pt.c_B[i] or Fraction(0)) * pt.b[i]
                 for i in range(len(pt.basis)))
    # 原 σ 行若多出一个数（z 值位），回填时保留这个位置
    tail = ([fmt_frac(z_calc)] if len(pt.sigma_nums) == n + 1 else []) \
        + [fmt_frac(v) for v in exp]
    wrapped = "$" in cells[label_cells]
    new_cells = cells[:label_cells] + [
        (f"${t}$" if wrapped else t) for t in tail
    ]
    return " & ".join(new_cells)


def repair_sigma(latex: str) -> tuple[str, list[str]]:
    """代码级修复：只重算「c_B 与 c_j 一致（数据行可信）」的表的 σ 行。
    返回 (新 latex, 修复说明)。"""
    fixed_notes: list[str] = []
    out = latex
    try:
        tables, _ = parse_simplex_tables(latex)
        for pt in reversed(tables):        # 从后往前替换，保持前面的偏移有效
            data_ok = True
            if pt.c_j and all(v is not None for v in pt.c_j):
                for i, bvar in enumerate(pt.basis):
                    if bvar in pt.var_names and pt.c_B[i] is not None:
                        j = pt.var_names.index(bvar)
                        if not eq(Fraction(pt.c_B[i]), Fraction(pt.c_j[j])):
                            data_ok = False
            if not data_ok:
                continue
            new_row = build_repaired_sigma_row(pt)
            if new_row is None or pt.sigma_row is None:
                continue
            old = " & ".join(pt.sigma_row.cells)
            if old.strip() == new_row.strip():
                continue
            out = out[: pt.sigma_row.start] + new_row + out[pt.sigma_row.end:]
            exp = _sigma_expected(pt, "c-z") or []
            fixed_notes.append(
                f"σ 行按 σ=c−z 重算回填（{', '.join(fmt_frac(v) for v in exp)}）")
    except Exception as exc:
        fixed_notes.append(f"σ 行修复失败（保留原文）：{exc!r}")
    return out, fixed_notes


# ============================================================
#  对外入口
# ============================================================

def verify(latex: str) -> tuple[list[str], list[str], bool]:
    """验算一段 LaTeX 里的所有单纯形表。
    返回 (issues, notes, checked)。issues 非空 = 有错；checked=False = 没认出可验的表。"""
    issues: list[str] = []
    notes: list[str] = []
    checked = False
    try:
        tables, pnotes = parse_simplex_tables(latex)
        notes.extend(pnotes)
        for idx, pt in enumerate(tables, 1):
            rep = Report()
            _check_single_table(pt, rep)
            checked = True
            issues.extend(f"表{idx}：{m}" for m in rep.issues)
            notes.extend(f"表{idx}：{m}" for m in rep.notes)
        rep2 = Report()
        check_cross_tables(tables, rep2)
        issues.extend(rep2.issues)
        notes.extend(rep2.notes)
    except Exception as exc:              # 自检自身故障绝不打断发消息
        notes.append(f"自检模块内部异常（按未校验处理）：{exc!r}")
        checked = False
    return issues, notes, checked


def _annotation(issues: list[str]) -> str:
    """兜底标注：验算没过也要发时，跟群友说实话（人设：算错了就认）。"""
    return f"（表我校了两遍还是有点心虚：{issues[0][:60]}……你们也帮着盯一眼）"


def enforce(parts: list[dict], revise=None, log=None,
            max_revise: int = 1) -> tuple[list[dict], str]:
    """main.py 的插桩点：对 parts 里的 math 片段做 验算 → 修复 → 模型重试。

    顺序：代码重算 σ 行（不花钱、瞬时）→ 带错误清单让模型重算 →
    还错就放行原文并附标注。返回 (parts, note)，note 非空时由调用方
    并进要发的文字里。全程自兜底，绝不抛异常打断发消息主流程。
    """
    notes_out: list[str] = []
    try:
        for p in parts:
            if not isinstance(p, dict) or p.get("kind") != "math":
                continue
            latex = p.get("latex", "")
            if "tabular" not in latex and "array" not in latex:
                continue

            issues, notes, checked = verify(latex)
            if not checked:
                if log and notes:
                    log(f"表格未识别为单纯形表，跳过自检：{notes[0][:60]}")
                continue
            if not issues:
                continue
            if log:
                log(f"表格验算发现 {len(issues)} 处问题：{issues[0][:80]}")

            # 1) 代码级修复：数据行可信时直接重算 σ 行回填
            fixed, fix_notes = repair_sigma(latex)
            issues2, _, checked2 = verify(fixed)
            if checked2 and not issues2:
                p["latex"] = fixed
                if log:
                    log(f"σ 行已用代码重算修复：{(fix_notes or [''])[0][:60]}")
                continue

            # 2) 带错误清单让模型重算（可能换线路，最坏十几秒）
            best_latex, best_issues = None, list(issues2 or issues)
            base_count = len(best_issues)
            if revise and max_revise > 0:
                revised = revise(fixed, issues2 or issues)
                if revised:
                    issues3, _, checked3 = verify(revised)
                    if checked3 and not issues3:
                        p["latex"] = revised
                        if log:
                            log("模型按错误清单重算后通过验算")
                        continue
                    if checked3 and len(issues3) < base_count:
                        # 改好了但没全对：取更好的版本，剩余问题走标注
                        best_latex, best_issues = revised, issues3
                        if log:
                            log(f"模型重算后问题从 {base_count} 处减到 "
                                f"{len(issues3)} 处")
            if best_latex is not None:
                p["latex"] = best_latex

            # 3) 兜底：放行（可能已是较好的版本）+ 标注，不假装可靠
            notes_out.append(_annotation(best_issues))
            if log:
                log(f"修复后仍有 {len(best_issues)} 处没过验算，放行并附标注")
    except Exception as exc:
        if log:
            log(f"表格自检内部异常（放行原文）：{exc!r}")
    return parts, " ".join(notes_out).strip()
