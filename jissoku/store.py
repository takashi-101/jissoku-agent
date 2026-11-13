"""検証済みの記録をSQLiteに取り込み、SQLの集計とPythonの集計を突き合わせる（データの検算）。

SQLはすべてプレースホルダで値を渡し、文字列の連結でSQLを組み立てない。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

from . import audit, tools

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  rows INTEGER NOT NULL,
  loaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
  date TEXT NOT NULL CHECK (date GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  task TEXT NOT NULL,
  area TEXT NOT NULL,
  received INTEGER NOT NULL CHECK (received >= 0),
  processed INTEGER NOT NULL CHECK (processed >= 0),
  conditions TEXT NOT NULL DEFAULT '',
  source_id INTEGER NOT NULL REFERENCES sources(id),
  UNIQUE (date, task, area)
);
CREATE INDEX IF NOT EXISTS idx_records_date ON records(date);
"""


def connect(db: str) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def load_csv(db: str, csv_path: str) -> dict:
    """CSVを検証してから取り込む。同じ内容のファイルは二重に取り込まない。"""
    digest = audit.sha256_file(csv_path)
    data = tools.load_records(csv_path)
    with connect(db) as con:
        if con.execute("SELECT 1 FROM sources WHERE sha256 = ?", (digest,)).fetchone():
            return {"取り込み": 0, "理由": "同じ内容のファイルは取り込み済み", "読めなかった行": data["errors"]}
        cur = con.execute("INSERT INTO sources(path, sha256, rows, loaded_at) VALUES (?, ?, ?, ?)",
                          (csv_path, digest, len(data["records"]), datetime.now(timezone.utc).isoformat(timespec="seconds")))
        sid = cur.lastrowid
        con.executemany(
            "INSERT INTO records(date, task, area, received, processed, conditions, source_id) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date, task, area) DO UPDATE SET received = excluded.received, processed = excluded.processed, "
            "conditions = excluded.conditions, source_id = excluded.source_id",
            [(r["日付"], r["業務"], r["区分"], r["受付件数"], r["処理件数"], ";".join(r["条件"]), sid) for r in data["records"]])
    return {"取り込み": len(data["records"]), "読めなかった行": data["errors"], "sha256": digest}


def read_records(db: str) -> list:
    with connect(db) as con:
        rows = con.execute("SELECT date, task, area, received, processed, conditions FROM records ORDER BY date, task, area").fetchall()
    return [{"日付": d, "業務": t, "区分": a, "受付件数": r, "処理件数": p, "条件": [c for c in cond.split(";") if c]}
            for d, t, a, r, p, cond in rows]


def reconcile(db: str) -> dict:
    """日ごとの受付件数の合計と、業務ごとの受付・処理の合計を、SQLとPythonの両方で出して突き合わせる。"""
    recs = read_records(db)
    with connect(db) as con:
        sql_daily = dict(con.execute("SELECT date, SUM(received) FROM records GROUP BY date").fetchall())
        sql_task = {t: (r, p) for t, r, p in con.execute("SELECT task, SUM(received), SUM(processed) FROM records GROUP BY task")}
    py_daily = tools._daily_totals(recs, "受付件数")
    py_task = {}
    for r in recs:
        a, b = py_task.get(r["業務"], (0, 0))
        py_task[r["業務"]] = (a + r["受付件数"], b + r["処理件数"])
    diffs = [d for d in set(sql_daily) | set(py_daily) if sql_daily.get(d) != py_daily.get(d)]
    diffs += [t for t in set(sql_task) | set(py_task) if sql_task.get(t) != py_task.get(t)]
    return {"一致": not diffs, "食い違い": sorted(diffs), "日数": len(py_daily), "業務数": len(py_task)}


def main() -> None:
    p = argparse.ArgumentParser(description="記録をSQLiteに取り込み、検算する")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("load")
    a.add_argument("csv")
    a.add_argument("--db", required=True)
    b = sub.add_parser("reconcile")
    b.add_argument("--db", required=True)
    args = p.parse_args()
    out = load_csv(args.db, args.csv) if args.cmd == "load" else reconcile(args.db)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if out.get("一致", True) else 1)


if __name__ == "__main__":
    main()
