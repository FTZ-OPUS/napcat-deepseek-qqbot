#!/usr/bin/env python3
"""回归测试：persona 里的**硬规则**不能丢。

为什么单独测这个：persona 是提示词，改起来很随意（"顺手调个措辞"），
但里面有几条是**踩过事故才加上的**。措辞一改、规则没了，事故就会重演，
而且**不会报错** —— 只能靠这种断言守住。

跑法：PYTHONPATH=/home/qqbot/qq-bot python3 test_persona.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))
os.environ.setdefault("QQBOT_STATE_DIR", tempfile.mkdtemp(prefix="qqbot-test-state-"))

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
    _r.post = lambda *a, **k: (_ for _ in ()).throw(_E("stub"))
    sys.modules["requests"] = _r

import persona  # noqa: E402

P = persona.PERSONA


def test_answers_any_exam_subject():
    print("== ★不许把自己框死在数学里（2026-10-12 事故：拒答考研政治）==")
    # 真实事故：群友发了考研政治的一页，机器人回「数学题喊我，政治这页我真接不住」，
    # 群友当即不满「考研政治也要答」。而同一天早上它还答了一堆政治经济题 ——
    # 不是能力问题，是被「数学机器人」这个自我定位框住了。
    T.ok("考研政治" in P, "明确提到考研政治要答")
    T.ok("别把自己框死" in P, "有这条标题")
    T.ok("接不住" in P, "把事故原话写进去了（当反面教材）")
    T.ok("正经的考试题目" in P, "说明了范围：正经考试题都答")


def test_planning_rule_recorded():
    print("== 讲义归档≠能不能答（别把两件事搞混）==")
    T.ok("归档规则" in P, "明确区分「讲义不收」和「照样要答」")


def test_exact_answer_rule():
    print("== ★考试题必须给精确解，不许用数值近似 ==")
    # 真实事故：定积分精确值 π/2，机器人却用 Simpson 给近似值
    T.ok("必须给精确解" in P, "有精确解规则")
    T.ok("没有初等原函数」不是终点" in P or "不是终点" in P, "写明「没找到≠不存在」")


def test_no_fabricated_numbers():
    print("== ★不许编造数值结果 ==")
    T.ok("绝不许编造数值" in P, "有禁止编数字的规则")


def test_no_fake_sent_files():
    print("== ★不许谎报「发了文件」==")
    # ⚠️ 这条在 OUTPUT_RULES 里，不在 PERSONA
    T.ok("真的发了文件或图片" in persona.OUTPUT_RULES,
         "有关于「只在真发了才说」的约束（OUTPUT_RULES）")


def test_transcribe_each_problem():
    print("== 多题解答要每题先抄题 ==")
    T.ok("每道题都要先把题目抄一遍" in P, "有抄题规则")


def test_vision_rules():
    print("== 识图规则（不许说看不到）==")
    T.ok("确实能看到" in P or "你确实能看到" in P, "不许说看不到图")
    # ⚠️ 「不许臆加条件」这条在 brain.IMAGE_HINT_TEMPLATE 里，不在 persona
    src = (HERE.parent / "server" / "brain.py")
    if not src.exists():
        src = HERE.parent / "brain.py"
    T.ok("绝对不许臆加" in src.read_text(encoding="utf-8"),
         "识图提示词里有「不许臆加条件」")


def test_politics_is_casual_by_default():
    print("== ★政治题默认闲聊式回答，不说 PDF 就不出文件（2026-10-13 车主定）==")
    # 车主原话：「别问政治题，如果没说要 PDF，你就直接用闲聊的方式，潜移默化的回答」
    #          「说要 PDF 再发 PDF，不说 PDF 你就跟人一样聊天的几句，把那个选择题答了」
    # 群友反馈：那晚（全是短文字、没有 PDF）的答法最好。
    T.ok("默认用「闲聊」的方式答" in P, "默认闲聊式")
    T.ok("别摆讲课的架子" in P, "不摆讲课架子")
    T.ok("不说要 PDF，就绝对不要出文件" in P, "★不说 PDF 就不出文件")
    T.ok("只有群友明确要 PDF" in P, "★明确要才出文件")
    T.ok("别写成教科书条目" in P, "不许写成教科书条目")
    T.ok("答案要准" in P, "但答案要准")


def test_politics_can_draw_figures():
    print("== 政治/经济题能画图（要文件时用）==")
    T.ok("供需曲线" in P, "点名了供需曲线")
    T.ok("tikzpicture" in P or "pgfplots" in P, "给了解法（写 tikz 代码）")


def test_pdf_default_not_longimg():
    print("== 默认 PDF（实测更快），长图只在点名要图片时 ==")
    T.ok("一律默认用" in P and "doc" in P, "默认 PDF")
    T.ok("只有群友明确点名要图片时" in P, "长图只在点名时")
    T.ok("需要画图就在 PDF 里画" in P, "需要配图时在 PDF 里画")


def test_escape_integrity():
    print("== 转义自检（persona 里的 LaTeX 命令不能被吃掉）==")
    # persona.py 导入时会自己跑 _assert_no_escape_damage()，能 import 就说明过了
    T.ok(len(P) > 3000, f"PERSONA 长度正常（{len(P)} 字符）")
    T.ok("\\textbf" in P or "\\frac" in P or "\\sqrt" in P,
         "LaTeX 命令还在（没被转义吃掉）")


def test_chat_style_diagrams():
    print("== ★政治闲聊里要顺手插关系图（2026-10-13 车主定）==")
    T.ok("顺手插 1~2 张「关系图」" in P, "有插关系图的规则")
    T.ok("每道选择题最多插 1 张" in P, "★选择题最多一张")
    T.ok("维恩图" in P and "流程图" in P, "点名了维恩图/流程图")
    T.ok("夹在文字中间" in P, "★图夹在文字中间")
    T.ok('mode 用 "card"' in P or 'mode="card"' in P, "用 card（小图最快）")


def test_relation_diagram_recipe():
    print("== ★关系图预置样式（彩色方框+带标签箭头，2026-10-13 车主定）==")
    T.ok("rbox=rbA" in P, "配方里给了 rbox 预置方框")
    T.ok("rarr" in P and "rarrR" in P, "给了正向箭头和反向虚线箭头")
    T.ok("决定" in P and "反作用" in P, "点名了「决定」「反作用」这类标注")
    T.ok("别自己重定义" in P, "明确要求用预置样式")
    # 模板里得真的有这些定义
    for name in ("template_card.tex", "template_doc.tex",
                 "template_doc_style2.tex", "template_longimg.tex"):
        f = Path(__file__).resolve().parent.parent / "latex" / name
        if f.exists():
            s = f.read_text(encoding="utf-8")
            T.ok("rbox/.style" in s, f"{name} 定义了 rbox")
            T.ok("rbA" in s and "rbR" in s, f"{name} 定义了配色")


def test_no_skipping_steps():
    print("== ★不许跳步、不许写「留给你自己验算」（2026-10-13 事故）==")
    # 真实事故：范德蒙+余子式求和的题，机器人写到关键步骤
    # 写「这一步是纯代数，留给你自己验算」然后给结论 0，答案是错的。
    T.ok("留给你自己验算" in P, "点名了这句敷衍话")
    T.ok("关键步骤必须自己算完" in P, "要求把关键步骤算完")
    T.ok("没有真正算出来，就不要写结论" in P, "★没算就不许下结论")
    T.ok("干净的结果最容易是编的" in P, "点破了「干净数最可疑」")
    T.ok("浮点相消" in P, "提醒了验证时的浮点陷阱")


def test_matrix_inverse_must_verify():
    print("== ★逆矩阵必须验算、写进答案的数必须被用到或验过（2026-10-13 事故）==")
    T.ok("逆矩阵算完必须验算" in P, "★要求验算 P·P⁻¹=I")
    T.ok("P\\cdot P^{-1}=I" in P, "给了具体验算式")
    T.ok("-5/2" in P, "点名了这次写错的 −5/2")
    T.ok("错了也没人知道" in P, "点破「没用上也没验过」是最危险的形态")
    T.ok("凡写进答案的数" in P or "凡是写进答案的数字" in P, "写进答案的数必须被用到/验过")


def test_completion_requests_need_context():
    print("== ★「补全 X」要先回看自己之前说过什么 ==")
    T.ok("补全 / 续写" in P, "点名了「补全」类请求")
    T.ok("先回看自己之前那条回答" in P, "要求回看上下文")
    T.ok("别挑最省事的那个解释" in P, "★符号歧义别挑省事解释")
    T.ok("P^{-1}" in P or "P−1" in P, "点名了这次的 P⁻¹ 歧义")
    T.ok("先问一句" in P, "拿不准就先问")


if __name__ == "__main__":
    for fn in (test_answers_any_exam_subject, test_planning_rule_recorded,
               test_exact_answer_rule, test_no_fabricated_numbers,
               test_no_fake_sent_files, test_transcribe_each_problem,
               test_vision_rules, test_politics_is_casual_by_default,
               test_politics_can_draw_figures,
               test_pdf_default_not_longimg, test_chat_style_diagrams,
               test_relation_diagram_recipe, test_no_skipping_steps,
               test_escape_integrity, test_matrix_inverse_must_verify,
               test_completion_requests_need_context):
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
