#!/usr/bin/env python3
"""回归测试：用户纠错反馈的采集（车主 2026-10-12 定）。

**核心是「纠错」** —— 车主原话：
> 「我的意思就类似于纠错吧，别人指出你错误了，这就属于我说的这种建议，
>  其他类似情况也算。」

这里只测**关键词预筛**（廉价那一段）。LLM 提炼那一段要真实 API，
靠端到端验证（见 README 十四之十五的实测记录）。
跑法：PYTHONPATH=/home/qqbot/qq-bot python3 test_feedback.py
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

import feedback as F  # noqa: E402


def test_catches_corrections():
    print("== ① 直接指出错误 —— 最核心的一类，必须抓到 ==")
    for t in ("没说二阶导连续啊 你咋用了 快重写",   # 真实案例
              "你第一题写错了 勒展开错了",
              "这题你算错了",
              "下标抄错了",
              "识别错了吧",
              "你这条件用错了吧"):
        T.ok(F.looks_like_suggestion(t), f"抓到：{t[:22]}")


def test_catches_delivery_complaints():
    print("== ② 交付毛病（太简略/没抄题/格式）也要抓 ==")
    for t in ("你这个PDF过程写的太简洁了",
              "下次记得批改时候要抄题",
              "写完整严谨一点",
              "格式有点乱"):
        T.ok(F.looks_like_suggestion(t), f"抓到：{t[:22]}")


def test_catches_behavior_requests():
    print("== ③ 行为建议（下次记得…/希望你…）也算 ==")
    for t in ("下次记得批改要有得分",
              "以后能不能写详细点",
              "希望你注意一下格式"):
        T.ok(F.looks_like_suggestion(t), f"抓到：{t[:22]}")


def test_ignores_normal_chat():
    print("== 普通提问、客套、夸奖 → 不收（宁缺毋滥，别让车主看噪音）==")
    for t in ("这题怎么做", "谢谢老板", "厉害啊", "在吗", "哈哈笑死",
              "6", "好的", "可以可以"):
        T.ok(not F.looks_like_suggestion(t), f"不收：{t[:22]}")


def test_ignores_too_short():
    print("== 太短的不收（信息量不够）==")
    T.ok(not F.looks_like_suggestion("错"), "单词不收")
    T.ok(not F.looks_like_suggestion(""), "空不收")


def test_folder_is_gitignored():
    """反馈文件夹含真实群友消息，绝不能被 git 跟踪。

    ⚠️ 注意：**服务器上根本没有 .gitignore**（git 仓库在本地案例目录里，
    服务器只跑代码）。所以这里找不到时**优雅跳过**，不判失败 ——
    真正的防线是案例目录那份 .gitignore（本地提交前会生效）。
    """
    print("== 反馈文件夹必须被 .gitignore 挡住（含真实群友消息）==")
    here = Path(__file__).resolve().parent.parent
    candidates = [here / ".gitignore",
                  here.parent / "基于NapCat和DeepSeek-Harness开发数学与陪聊QQ机器人的实战案例" / ".gitignore"]
    found = None
    for c in candidates:
        try:
            if c.exists() and "重要用户建议反馈" in c.read_text(encoding="utf-8"):
                found = c
                break
        except OSError:
            continue
    if found is None and not any(c.exists() for c in candidates):
        print("    ⏭ 本机没有 .gitignore（服务器只跑代码），跳过这项")
        return
    T.ok(found is not None, f".gitignore 里有这条规则（找到：{found}）")


if __name__ == "__main__":
    for fn in (test_catches_corrections, test_catches_delivery_complaints,
               test_catches_behavior_requests, test_ignores_normal_chat,
               test_ignores_too_short, test_folder_is_gitignored):
        fn()
    print(f"\n全部 {T.count} 项断言通过 ✅")
