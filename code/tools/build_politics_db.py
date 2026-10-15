#!/usr/bin/env python3
"""build_politics_db.py —— 把考点 JSON 灌进 SQLite（考研政治答题工具箱）。

**为什么用 SQL 而不是一堆 Markdown**：
  · 可以按科目/章节/关键词**精确查询** —— 机器人答题时只取相关的那几条
  · 结构化，好维护好扩展
  · 群友也能用任何 SQLite 工具自己查

用法：
    python3 tools/build_politics_db.py            # 用默认路径
    python3 tools/build_politics_db.py --check    # 只看统计，不重建
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "docs" / "考研政治答题工具箱" / "考点数据.json"
DB = BASE / "docs" / "考研政治答题工具箱" / "考点库.db"

SCHEMA = """
DROP TABLE IF EXISTS points;
CREATE TABLE points (
    id         INTEGER PRIMARY KEY,
    subject    TEXT NOT NULL,          -- 马原 / 毛中特 / 史纲 / 思修法基 / 当代
    chapter    TEXT NOT NULL,
    point      TEXT NOT NULL,          -- 考点名
    content    TEXT,                   -- 核心内容
    confusable TEXT,                   -- 易混点
    exam       TEXT,                   -- 常见考法
    keywords   TEXT                    -- 空格分隔，检索用
);
CREATE INDEX idx_subject ON points(subject);
CREATE INDEX idx_chapter ON points(chapter);
"""


def build(data_path: Path = DATA, db_path: Path = DB, quiet: bool = False) -> int:
    raw = json.loads(data_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("points") or raw.get("data") or []
    rows = []
    for it in raw:
        if not isinstance(it, dict) or not it.get("point"):
            continue
        kw = it.get("keywords") or []
        if isinstance(kw, str):
            kw = kw.replace("，", " ").replace(",", " ").split()
        rows.append((
            str(it.get("subject", "")).strip(),
            str(it.get("chapter", "")).strip(),
            str(it.get("point", "")).strip(),
            str(it.get("content", "")).strip(),
            str(it.get("confusable", "")).strip(),
            str(it.get("exam", "")).strip(),
            " ".join(str(k).strip() for k in kw if str(k).strip()),
        ))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO points(subject,chapter,point,content,confusable,exam,keywords)"
            " VALUES(?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    if not quiet:
        print(f"  ✅ 已建库：{db_path}")
        print(f"     {len(rows)} 个考点")
        report(db_path)
    return len(rows)


def report(db_path: Path = DB) -> None:
    con = sqlite3.connect(db_path)
    try:
        print("\n  ── 按科目 ──")
        for s, n in con.execute(
                "SELECT subject, COUNT(*) FROM points GROUP BY subject ORDER BY 2 DESC"):
            print(f"    {s:10} {n:>4} 个考点")
        print("\n  ── 按章节（前 12）──")
        for s, c, n in con.execute(
                "SELECT subject, chapter, COUNT(*) FROM points"
                " GROUP BY subject, chapter ORDER BY 3 DESC LIMIT 12"):
            print(f"    {s:6} {c[:26]:28} {n:>3}")
        tot_kw = con.execute("SELECT COUNT(*) FROM points WHERE keywords != ''").fetchone()[0]
        print(f"\n    有关键词的: {tot_kw} 条")
    finally:
        con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只看统计，不重建")
    a = ap.parse_args()
    if a.check:
        if not DB.exists():
            print(f"  🔴 库不存在：{DB}")
            sys.exit(1)
        report()
    else:
        if not DATA.exists():
            print(f"  🔴 数据文件不存在：{DATA}")
            sys.exit(1)
        build()
