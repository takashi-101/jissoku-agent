"""業務量の集計を、AIアプリ（Claude Desktop、LM Studioなど）から呼べるようにするMCPサーバー（読み取り専用）。

標準入出力でやりとりし、外部への通信はしない。標準ライブラリとこのリポジトリの関数だけで動く。
読めるファイルは JISSOKU_DATA_DIR（既定はこのリポジトリの sample/）の中の .csv / .json / .db に限り、5MBを超えるものは読まない。
"""
import json
import os
import sys
from pathlib import Path

from jissoku import grounding, store, tools

PROTOCOL = "2025-06-18"
DATA_DIR = Path(os.environ.get("JISSOKU_DATA_DIR", Path(__file__).resolve().parent / "sample")).resolve()
ALLOWED_SUFFIX = {".csv", ".json", ".db", ".sqlite", ".sqlite3"}
MAX_BYTES = 5 * 1024 * 1024

COMMON = {
    "csv_path": {"type": "string", "description": "記録のファイル名（JISSOKU_DATA_DIR の中。CSVまたはSQLite）"},
    "settings_path": {"type": "string", "description": "1件あたりの時間などを書いたJSONのファイル名（任意）"},
}
TOOLS = [
    {"name": "backlog", "description": "業務ごとに、日ごとの未処理件数（前日の残り＋受付−処理）と、最大・期末の値を出す",
     "inputSchema": {"type": "object", "properties": COMMON, "required": ["csv_path"]}},
    {"name": "stratify", "description": "条件・曜日・区分ごとに、1日あたりの受付件数の平均・中央値・最頻値を比べ、多い・少ないの印を付ける",
     "inputSchema": {"type": "object", "properties": {**COMMON, "by": {"type": "string", "enum": ["条件", "曜日", "区分"]},
                                                      "task": {"type": "string"}}, "required": ["csv_path"]}},
    {"name": "weekly_bottleneck", "description": "週ごとに、未処理件数が増えた業務を、増えた件数の多い順に並べる",
     "inputSchema": {"type": "object", "properties": COMMON, "required": ["csv_path"]}},
    {"name": "staffing", "description": "受付件数と1件あたりの時間から、1日あたりの必要人数と、未処理を片付けるのに要る人日を出す",
     "inputSchema": {"type": "object", "properties": COMMON, "required": ["csv_path", "settings_path"]}},
    {"name": "metric_catalog", "description": "要約で参照できる指標の一覧（ID・名称・値）を返す。要約では数値を直接書かず {M3} のようにIDで参照する",
     "inputSchema": {"type": "object", "properties": COMMON, "required": ["csv_path"]}},
    {"name": "render_summary", "description": "IDで数値を参照した要約を検証し、違反（数字の直書き・存在しないID・指標の取り違えの疑い）が無ければ値を差し込んで返す",
     "inputSchema": {"type": "object", "properties": {**COMMON, "draft": {"type": "string"}}, "required": ["csv_path", "draft"]}},
]


class Rejected(ValueError):
    pass


def safe_path(name: str | None) -> str | None:
    if not name:
        return None
    p = (DATA_DIR / name).resolve()
    if p != DATA_DIR and DATA_DIR not in p.parents:
        raise Rejected("JISSOKU_DATA_DIR の外のファイルは読めません")
    if p.suffix.lower() not in ALLOWED_SUFFIX:
        raise Rejected(f"読めるのは {sorted(ALLOWED_SUFFIX)} だけです")
    if not p.is_file():
        raise Rejected("ファイルがありません")
    if p.stat().st_size > MAX_BYTES:
        raise Rejected("5MBを超えるファイルは読めません")
    return str(p)


def _records(path: str) -> dict:
    if path.endswith((".db", ".sqlite", ".sqlite3")):
        return {"records": store.read_records(path), "errors": []}
    return tools.load_records(path)


def call(name, args):
    data = _records(safe_path(args.get("csv_path")))
    recs, s = data["records"], tools.load_settings(safe_path(args.get("settings_path")))
    if name == "backlog":
        out = tools.backlog(recs, s)
    elif name == "stratify":
        out = tools.stratify(recs, args.get("by", "条件"), args.get("task"))
    elif name == "weekly_bottleneck":
        out = tools.weekly_bottleneck(recs, s)
    elif name == "staffing":
        out = tools.staffing(recs, s)
    elif name in ("metric_catalog", "render_summary"):
        cat = grounding.build_catalog({k: f(recs, s) for k, f in tools.ANALYSES.items()})
        if name == "metric_catalog":
            out = grounding.catalog_for_prompt(cat)
        else:
            out = grounding.check(str(args.get("draft", ""))[:20000], cat)
    else:
        raise Rejected(f"知らない道具です：{name}")
    if data["errors"]:
        out = {"受け付けなかった行": data["errors"], "結果": out}
    return out


def handle(msg):
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                                                         "serverInfo": {"name": "jissoku-agent", "version": "0.2.0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = msg.get("params", {})
        try:
            text = json.dumps(call(p.get("name"), p.get("arguments", {})), ensure_ascii=False)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        except (OSError, ValueError, KeyError) as e:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"失敗：{e}"}], "isError": True}}
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"知らないメソッドです：{method}"}}


def main():
    for line in sys.stdin:
        if len(line) > 100_000:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "要求が大きすぎます"}}) + "\n")
            sys.stdout.flush()
            continue
        line = line.strip()
        if not line:
            continue
        try:
            res = handle(json.loads(line))
        except json.JSONDecodeError:
            res = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSONとして読めません"}}
        if res is not None:
            sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
