#!/usr/bin/env python3
"""汇报 token 用量（车主 2026-10-12 定：「我要的时候你就向我汇报一下」）。

用法：
    sudo -u qqbot /home/qqbot/qq-bot/venv/bin/python /home/qqbot/qq-bot/show_usage.py
    # 想看更久：加天数，例如 30
    sudo -u qqbot ... show_usage.py 30
"""
import sys

sys.path.insert(0, "/home/qqbot/qq-bot")
import usage  # noqa: E402

days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
print("═" * 74)
print(f"  e阶导 · 真实 token 用量（最近 {days} 天）")
print("═" * 74)
print()
print(usage.report(days))
print()
print("═" * 74)
