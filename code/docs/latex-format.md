---
name: 简单latex格式
description: LaTeX 精美排版模板 — ctex 中文支持、tcolorbox 彩色定理框、TikZ 插图、fancyhdr 页眉、统一颜色主题。Use when writing LaTeX documents for math proofs, geometry notes, or problem solutions with Chinese text.
metadata:
  type: reference
  tags: [latex, template, math, chinese, tcolorbox, tikz, skill]
---

# LaTeX 精美排版 Skill

基于"托勒密定理两种证明"PDF 提炼的可复用模板。

---

## 1. 文档骨架

```latex
% !TEX program = xelatex
\documentclass[12pt,a4paper]{ctexart}

% === 宏包 ===
\usepackage{amsmath,amssymb,amsthm}
\usepackage[margin=2.5cm]{geometry}
\usepackage{tikz}
\usetikzlibrary{calc,through,intersections,angles,quotes}
\usepackage{tcolorbox}
\tcbuselibrary{skins,breakable}
\usepackage{enumitem}
\usepackage{fancyhdr}
\usepackage{hyperref}
\hypersetup{colorlinks=true,linkcolor=blue!70!black,urlcolor=teal}
```

**铁律：**
- 编译用 **`xelatex`**（中文必须）
- 中文走 **`ctexart`**，不要用 `ctex` 包 + `article`
- 页边距 `margin=2.5cm`

---

## 2. 颜色主题

```latex
\definecolor{ptred}{HTML}{D64545}
\definecolor{ptgold}{HTML}{D4A017}
\definecolor{ptgreen}{HTML}{2E8B57}
\definecolor{ptblue}{HTML}{3A7CA5}
\definecolor{ptpurple}{HTML}{7B3F8C}
\definecolor{ptteal}{HTML}{008080}
\definecolor{ptorange}{HTML}{E07020}
\definecolor{ptbg}{HTML}{F8F5F0}      % 浅背景色
```

**用法：** `\color{ptblue}文字` 或 `colback=ptbg, colframe=ptblue`

---

## 3. 定理/证明彩色框

### 3.1 定理框（红框，标题居中）

```latex
\newtcolorbox{theorembox}[1][]{
    enhanced, breakable,
    colback=ptbg, colframe=ptred!70!black,
    title={\color{white}\bfseries #1},
    attach boxed title to top center={xshift=0mm,yshift=-3mm},
    boxed title style={colback=ptred,sharp corners,size=small},
    fonttitle=\large\centering,
    before skip=16pt, after skip=12pt
}
```

### 3.2 证明框（蓝框，标题左贴）

```latex
\newtcolorbox{beautifulproof}[1][]{
    enhanced, breakable,
    colback=ptbg, colframe=ptblue!60!black,
    title={\color{white}\bfseries #1},
    attach boxed title to top left={xshift=3mm,yshift=-3mm},
    boxed title style={colback=ptblue,sharp corners},
    fonttitle=\large,
    before skip=12pt, after skip=12pt
}
```

---

## 4. 定理环境声明

```latex
\theoremstyle{definition}
\newtheorem{theorem}{定理}[section]
\newtheorem{lemma}[theorem]{引理}
\newtheorem{corollary}[theorem]{推论}
```

**共享计数器 `[theorem]`** 确保按节编号（定理 1.1, 引理 1.2…）。

---

## 5. 封面

```latex
\begin{titlepage}
    \centering\vspace*{3cm}
    {\Huge\bfseries\color{ptblue} 标题}
    \vspace{0.8cm}
    {\Large 副标题英文}
    \vspace{2cm}
    % 可选 TikZ 插图
    \begin{tikzpicture}[scale=1.8]
        ...
    \end{tikzpicture}
    \vfill
    {\small\color{gray}\today}
\end{titlepage}
```

**注意：** `titlepage` 环境不计页码，正文从第 1 页开始。

---

## 6. TikZ 插图规范

```latex
\begin{tikzpicture}[scale=2.0]
    % 背景圆/曲线
    \draw[ptblue, very thick] (0,0) circle (1.5);

    % 定义坐标
    \coordinate (A) at (140:1.5);
    \coordinate (B) at (50:1.5);

    % 连线
    \draw[ptred, very thick] (A) -- (B);
    \draw[ptgold, thick, densely dashed] (A) -- (C);

    % 填充区域
    \filldraw[ptred,opacity=0.15] (A) -- (B) -- (C) -- cycle;

    % 点 + 标签
    \filldraw[ptred] (A) circle (0.05);
    \node at ($(A)+(140:0.25)$) {\bfseries A};
\end{tikzpicture}
```

**常用样式：**
| 元素 | 样式 |
|------|------|
| 圆/曲线 | `ptblue, very thick` |
| 边/线段 | `ptred/orange/teal/purple, very thick` |
| 对角线 | `ptgold, thick, densely dashed` |
| 辅助线 | `ptteal, very thick` |
| 填充 | `opacity=0.15` |
| 点 | `circle (0.05)` |
| 标签 | `\bfseries` + 极坐标偏移 `($(P)+(angle:0.25)$)` |

---

## 7. 数学公式规范

```latex
% 行内公式
圆内接四边形 $ABCD$ 满足 $AB \cdot CD + BC \cdot AD = AC \cdot BD$。

% 独立公式
\[
\boxed{\,|AB|\cdot|CD| + |BC|\cdot|AD| \;\ge\; |AC|\cdot|BD|\,}
\]

% 多行对齐
\[
\begin{aligned}
\overrightarrow{AB}\cdot\overrightarrow{CD} + \overrightarrow{BC}\cdot\overrightarrow{AD}
&= z_1(z_3 - z_2) + z_3(z_2 - z_1) \\
&= z_1z_3 - z_1z_2 + z_2z_3 - z_1z_3 \\
&= z_2(z_3 - z_1)
\end{aligned}
\]

% 条件大括号
\[
\begin{cases}
\angle ABE = \angle DBC &\text{（构造）}\\[4pt]
\angle BAE = \angle BDC &\text{（$\widehat{BC}$ 所对）}
\end{cases}
\]

% 箭头
\;\Longrightarrow\;
\;\Rightarrow\;
\quad\Longrightarrow\quad
```

---

## 8. 页眉页脚

```latex
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\color{ptblue}左标题}
\fancyhead[R]{\color{ptblue}\thepage}
\renewcommand{\headrulewidth}{0.5pt}
\renewcommand{\headrule}{\hbox to\headwidth{\color{ptblue}\leaders\hrule height 0.5pt\hfill}}
```

---

## 9. 列表与间距

```latex
% 紧凑列表
\begin{itemize}[nosep]
    \item $\angle BAC = \angle BDC$ \hspace{1cm}（$\widehat{BC}$ 所对）
    \item $\angle ABD = \angle ACD$ \hspace{1cm}（$\widehat{AD}$ 所对）
\end{itemize}

% 自定义间距
\\[4pt]      % 行间距
\vspace{8pt}  % 段落间距
```

---

## 10. 编译命令

```bash
xelatex -interaction=nonstopmode file.tex   # 第一遍
xelatex -interaction=nonstopmode file.tex   # 第二遍（更新交叉引用）
```

**PDF 被锁住时**，用 `-job-name=新名字` 或复制 `.tex` 文件重命名编译。

---

## 11. 写入讲义文件夹 & 合并总讲义

当用户要求将内容加入桌面 `latex讲义` 文件夹的总讲义时：

### 11.1 工作流程

1. **编写 .tex 文件** → 先放在工作目录（避免权限拦截），再 `cp` 到桌面文件夹
2. **编译为 PDF** → `cd "/c/Users/123/Desktop/latex讲义/" && xelatex -interaction=nonstopmode file.tex`
3. **尝试合并** → 用 PyPDF2 合并到 `Total_讲义.pdf`：

```python
python -c "
import PyPDF2
merger = PyPDF2.PdfMerger()
merger.append('Total_讲义.pdf')
merger.append('today_content.pdf')
merger.write('Total_讲义_new.pdf')
merger.close()
"
```

4. **替换原文件** → `mv Total_讲义_new.pdf Total_讲义.pdf`
   - 若 PDF 被占用（`Device or resource busy`），输出为 `Total_讲义_YYYYMMDD.pdf`，告知用户手动重命名

### 11.2 注意事项

- **不要**直接 Write 到桌面路径（会被拦截），先写工作目录再 `cp`
- 合并用 PyPDF2，简单追加，不重建整份文档
- 若合并失败或被占用，告知用户手动处理，**不要反复尝试**
