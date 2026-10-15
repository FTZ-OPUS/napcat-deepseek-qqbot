#!/usr/bin/env python3
"""query_politics.py —— 查考研政治考点库（命令行）。

用法：
    python3 tools/query_politics.py 矛盾的主要方面      # 按关键词查
    python3 tools/query_politics.py --list 马原         # 列某科目的所有考点
    python3 tools/query_politics.py --chapter 马原 资本主义的本质及规律
    python3 tools/query_politics.py --stats
    python3 tools/query_politics.py --sql "SELECT count(*) FROM points"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "server"))
DB = BASE / "docs" / "考研政治答题工具箱" / "考点库.db"


def _conn() -> sqlite3.Connection:
    if not DB.exists():
        print(f"  🔴 库不存在：{DB}\n     先跑：python3 tools/build_politics_db.py")
        sys.exit(1)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="*", help="关键词")
    ap.add_argument("--list", metavar="科目", help="列某科目全部考点")
    ap.add_argument("--chapter", nargs=2, metavar=("科目", "章节"))
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--sql", metavar="语句")
    a = ap.parse_args()
    con = _conn()
    try:
        if a.sql:
            for r in con.execute(a.sql):
                print("  " + " ｜ ".join(str(x) for x in tuple(r)))
            return
        if a.stats:
            print("  ── 按科目 ──")
            for s, n in con.execute("SELECT subject,COUNT(*) FROM points GROUP BY subject ORDER BY 2 DESC"):
                print(f"    {s:10}{n:>5}")
            print("\n  ── 按章节 ──")
            for s, c, n in con.execute("SELECT subject,chapter,COUNT(*) FROM points GROUP BY 1,2 ORDER BY 1,2"):
                print(f"    {s:8}{c[:30]:32}{n:>4}")
            return
        if a.list:
            for r in con.execute("SELECT chapter,point FROM points WHERE subject=? ORDER BY chapter,id", (a.list,)):
                print(f"    [{r['chapter']}] {r['point']}")
            return
        if a.chapter:
            for r in con.execute("SELECT * FROM points WHERE subject=? AND chapter=? ORDER BY id", tuple(a.chapter)):
                print(f"\n  ▸ {r['point']}")
                if r["content"]:
                    print(f"    核心：{r['content']}")
                if r["confusable"]:
                    print(f"    ⚠️ 易混：{r['confusable']}")
                if r["exam"]:
                    print(f"    考法：{r['exam']}")
            return
        if a.query:
            import polib
            rows = polib.relevant(" ".join(a.query), limit=8)
            if not rows:
                print("  （没找到相关考点）")
            for r in rows:
                print("\n  " + polib.render_one(r).replace("\n", "\n  "))
            return
        ap.print_help()
    finally:
        con.close()


if __name__ == "__main__":
    main()
