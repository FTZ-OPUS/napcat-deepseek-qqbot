#!/usr/bin/env python3
"""show_report.py —— 看每日群使用分析报告。

用法：
    python3 show_report.py            # 最近一份
    python3 show_report.py 2026-10-13 # 指定日期
    python3 show_report.py --list     # 列出所有
    python3 show_report.py --gen      # 现场重新生成（不落盘覆盖，直接打印）
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))
sys.path.insert(0, str(HERE.parent))

for cand in (HERE.parent / "server", HERE.parent):
    if (cand / "config.py").exists():
        sys.path.insert(0, str(cand))
        break

import config  # noqa: E402

D = Path(config.STATE_DIR) / "reports"


def main() -> None:
    args = [a for a in sys.argv[1:] if a.strip()]
    if args and args[0] == "--list":
        fs = sorted(D.glob("*.md")) if D.exists() else []
        if not fs:
            print("（还没有日报）")
            return
        for f in fs:
            print(f"  {f.stem}   {f.stat().st_size // 1024:>3} KB")
        return
    if args and args[0] == "--gen":
        import report
        print(report.build(args[1] if len(args) > 1 else ""))
        return
    day = args[0] if args else ""
    if day:
        f = D / f"{day}.md"
    else:
        fs = sorted(D.glob("*.md")) if D.exists() else []
        f = fs[-1] if fs else None
    if not f or not f.exists():
        print(f"（没有找到日报：{f}）")
        print("  试试 --list 看有哪些，或 --gen 现场生成一份")
        return
    print(f.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
