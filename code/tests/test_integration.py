"""tablecheck 与 main/brain 的接线打桩测试 —— 离线，绝不发真实群。

按项目惯例打桩：不真调 onebot（这里测的是 enforce 编排层和 brain 的
可导入性/协议一致性），revise 回调用假函数代替模型。
跑法：python3 tests/test_integration.py
"""
from __future__ import annotations

import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER))

import tablecheck  # noqa: E402
try:
    from tests.test_tablecheck import BROKEN_SAMPLE, GOOD_SAMPLE, T
except ImportError:
    from test_tablecheck import BROKEN_SAMPLE, GOOD_SAMPLE, T  # noqa: E402

# 本机没装 requests：给 brain 打桩（导入期只要模块存在，函数体内的
# requests.post 在这些用例里不会真的执行）
import types as _types
if "requests" not in sys.modules:
    try:
        import requests  # noqa: F401
    except ImportError:
        _fake = _types.ModuleType("requests")
        class _RE(Exception):
            pass
        _fake.RequestException = _RE
        _fake.post = lambda *a, **k: (_ for _ in ()).throw(_RE("offline stub"))
        sys.modules["requests"] = _fake

import brain   # noqa: E402  （导入即跑提示词自检，反斜杠坏了当场炸）
import os as _os
import tempfile as _tempfile
# ⚠️ 必须在 import config 之前：把状态目录指到临时目录，
# 否则测试会写坏线上真实的 state/（2026-10-12 真实事故：游标被覆盖 → 凌晨骚扰好友）
_os.environ.setdefault("QQBOT_STATE_DIR", _tempfile.mkdtemp(prefix="qqbot-test-state-"))

import config  # noqa: E402
import main    # noqa: E402  （导入即验证插桩点的名字都对）


def _part(latex):
    return [{"kind": "math", "mode": "doc", "latex": latex,
             "caption": "", "filename": "解题过程"}]


def test_wiring():
    print("== 接线自检 ==")
    T.ok(config.TABLECHECK_ENABLED in (True, False), "TABLECHECK_ENABLED 开关存在")
    T.ok(hasattr(brain, "revise_table"), "brain.revise_table 存在")
    T.ok("__LATEX__" in brain.TABLE_FIX_PROMPT, "校对提示词里有 __LATEX__ 占位")
    T.ok("__ISSUES__" in brain.TABLE_FIX_PROMPT, "校对提示词里有 __ISSUES__ 占位")
    # main.py 里真的调了 enforce（源码级确认，防手滑删了钩子）
    src_file = SERVER / "main.py"
    if src_file.exists():          # 源码检查只在本地开发目录有意义
        T.ok("tablecheck.enforce" in src_file.read_text(encoding="utf-8"),
             "main.py 插了 enforce 钩子")
    else:
        T.ok(hasattr(main, "send_parts"), "main.py 源码不在旁（服务器环境），跳过钩子源码检查")
    T.ok(hasattr(main, "send_parts"), "main 模块可导入且函数齐全")


def test_repair_path_no_revise():
    print("== 只坏 σ 行：代码直接修，不花模型调用 ==")
    calls = []
    parts, note = tablecheck.enforce(
        _part(GOOD_SAMPLE.replace("$3$ & $2$", "$3$ & $5$")),
        revise=lambda *a, **k: calls.append(a) or None,
        log=lambda m: None)
    T.ok(not calls, "revise 没被调用（省 API 钱）")
    T.ok(not note, "无标注")
    issues, _, checked = tablecheck.verify(parts[0]["latex"])
    T.ok(checked and not issues, "修复后验算通过")


def test_revise_path():
    print("== 数据行错：带错误清单交给模型，改对了才放行 ==")
    got_args = {}

    def fake_revise(latex, issues):
        got_args["latex"] = latex
        got_args["issues"] = issues
        return GOOD_SAMPLE          # 假装模型改对了

    broken = BROKEN_SAMPLE          # 数据/选择全错，代码修不动 σ 之外的东西
    parts, note = tablecheck.enforce(_part(broken), revise=fake_revise,
                                     log=lambda m: print(f"    [log] {m}"))
    T.ok(bool(got_args.get("issues")), f"revise 收到了错误清单（{len(got_args.get('issues', []))} 条）")
    T.ok("tabular" in got_args.get("latex", ""), "revise 收到的是完整 LaTeX")
    T.ok(parts[0]["latex"] == GOOD_SAMPLE, "改对后的 latex 已替换进 parts")
    T.ok(not note, "改对了就没有标注")


def test_revise_fails_annotation():
    print("== 模型改不动：放行 + 说实话标注 ==")
    parts, note = tablecheck.enforce(
        _part(BROKEN_SAMPLE),
        revise=lambda *a, **k: None,        # 模型摆烂
        log=lambda m: None)
    T.ok(parts[0]["latex"] == BROKEN_SAMPLE, "原文放行")
    T.ok("心虚" in note and "入基" in note, f"标注附上了具体问题（{note[:50]}…）")


def test_non_table_untouched():
    print("== 非表格 math 片段原样通过 ==")
    latex = r"\[ \int_0^1 x^2 \,dx = \frac{1}{3} \]"
    parts, note = tablecheck.enforce(_part(latex), revise=lambda *a: "X",
                                     log=lambda m: None)
    T.ok(parts[0]["latex"] == latex, "没被改动")
    T.ok(not note, "无标注")


def test_text_part_and_mixed():
    print("== text+math 混合 parts：只动 math，text 原样 ==")
    parts = [{"kind": "text", "text": "这题用单纯形法"},
             {"kind": "math", "mode": "doc", "latex": BROKEN_SAMPLE,
              "caption": "", "filename": "解题过程"}]
    out, note = tablecheck.enforce(parts, revise=lambda *a: None,
                                   log=lambda m: None)
    T.ok(out[0]["text"] == "这题用单纯形法", "text 片段没动")
    T.ok(note, "math 的标注在 note 里（由 main 并进 text）")


def test_revise_table_no_key():
    print("== brain.revise_table：没 key 时安全返回 None ==")
    if config.DEEPSEEK_API_KEY:
        print("    （本机配了 key，跳过该用例）")
        return
    T.ok(brain.revise_table(BROKEN_SAMPLE, ["表1：有错"]) is None,
         "无 key → None（不炸）")
    T.ok(brain.revise_table("", ["x"]) is None, "空 latex → None")
    T.ok(brain.revise_table(BROKEN_SAMPLE, []) is None, "空 issue → None")


def test_enforce_never_raises():
    print("== enforce 喂垃圾不炸 ==")
    for bad in ([], [{"kind": "math"}], [{"kind": "math", "latex": None}],
                [{"kind": "math", "latex": r"\begin{tabular}{c}\end{tabular}"}],
                [{"kind": "text", "text": "hi"}]):
        out, note = tablecheck.enforce(bad, revise=lambda *a: (_ for _ in ()).throw(
            RuntimeError("revise 炸了")), log=lambda m: None)
        T.ok(isinstance(out, list) and note == "", "垃圾输入安全返回")


if __name__ == "__main__":
    for fn in [test_wiring, test_repair_path_no_revise, test_revise_path,
               test_revise_fails_annotation, test_non_table_untouched,
               test_text_part_and_mixed, test_revise_table_no_key,
               test_enforce_never_raises]:
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
