"""改ざんに気づける監査ログ（ハッシュチェーン付きのJSON Lines）。

各行は直前の行のハッシュを含む。途中の行を書き換えたり消したりすると、検証で検出できる。
LLMへの入力と出力は本文を残さず、SHA-256だけを残す（ログ自体から業務データが漏れないようにする）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _last_hash(path: Path) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return GENESIS
    last = path.read_text(encoding="utf-8").strip().splitlines()[-1]
    return json.loads(last)["hash"]


def append(path: str | None, run_id: str, event: str, data: dict) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run_id": run_id,
             "event": event, "data": data, "prev": _last_hash(p)}
    body = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    entry["hash"] = sha256_text(body)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def verify(path: str) -> dict:
    prev = GENESIS
    lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
    for i, line in enumerate(lines, start=1):
        e = json.loads(line)
        h = e.pop("hash")
        if e["prev"] != prev or sha256_text(json.dumps(e, ensure_ascii=False, sort_keys=True)) != h:
            return {"ok": False, "壊れている行": i, "行数": len(lines)}
        prev = h
    return {"ok": True, "行数": len(lines)}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "verify":
        print("使い方：python -m jissoku.audit verify audit/audit.jsonl")
        sys.exit(2)
    r = verify(sys.argv[2])
    print(json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r["ok"] else 1)
