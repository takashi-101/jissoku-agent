"""業務量の実測データを集計する道具。

数字はすべてこのファイルの関数が計算する。AI（LLM）は数字を作らない。
標準ライブラリだけで動く。
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from datetime import date, datetime

from .guard import check_name

WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
COLUMNS = ["日付", "業務", "区分", "受付件数", "処理件数", "条件"]
HIGH, LOW = 1.2, 0.8  # 全体の中央値に対する倍率の目安


MAX_ROWS = 200_000


def load_records(csv_path: str) -> dict:
    """CSVを読み込み、行ごとの記録と、読めなかった行の理由を返す。

    業務・区分・条件の名前は、LLMに渡る可能性があるため、指示文とみられる語や使えない文字を含む行を受け付けない。
    """
    records, errors = [], []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            return {"records": [], "errors": [f"列がありません：{'、'.join(missing)}"]}
        for n, row in enumerate(reader, start=2):
            if n - 1 > MAX_ROWS:
                errors.append(f"{MAX_ROWS}行を超えたため、{n}行目以降は読み込んでいません")
                break
            try:
                d = datetime.strptime(row["日付"].strip(), "%Y-%m-%d").date()
                rec = {
                    "日付": d.isoformat(),
                    "業務": row["業務"].strip(),
                    "区分": row["区分"].strip() or "（区分なし）",
                    "受付件数": int(row["受付件数"]),
                    "処理件数": int(row["処理件数"]),
                    "条件": [t.strip() for t in (row["条件"] or "").split(";") if t.strip()],
                }
                if rec["受付件数"] < 0 or rec["処理件数"] < 0:
                    raise ValueError("件数が負の数です")
                for label, v in (("業務", rec["業務"]), ("区分", rec["区分"]), *(("条件", t) for t in rec["条件"])):
                    why = check_name(v)
                    if why:
                        raise ValueError(f"{label}「{v[:20]}」：{why}")
                records.append(rec)
            except (ValueError, KeyError) as e:
                errors.append(f"{n}行目：{e}")
    records.sort(key=lambda r: (r["日付"], r["業務"], r["区分"]))
    return {"records": records, "errors": errors}


def load_settings(path: str | None) -> dict:
    s = {"1件あたりの時間（分）": {}, "1人1日の作業時間（分）": 360, "期首の未処理件数": {}}
    if path:
        with open(path, encoding="utf-8") as f:
            s.update(json.load(f))
    return s


def _daily_totals(records, key="受付件数", task=None):
    tot = defaultdict(int)
    for r in records:
        if task and r["業務"] != task:
            continue
        tot[r["日付"]] += r[key]
    return dict(sorted(tot.items()))


def _r1(x):
    return round(float(x), 1)


def backlog(records: list, settings: dict) -> dict:
    """業務ごとに、日ごとの未処理件数（前日の残り＋受付−処理）を出す。"""
    init = settings.get("期首の未処理件数", {})
    by_task = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in records:
        v = by_task[r["業務"]][r["日付"]]
        v[0] += r["受付件数"]
        v[1] += r["処理件数"]
    out = {}
    for task, days in sorted(by_task.items()):
        rest = int(init.get(task, 0))
        series = []
        for d in sorted(days):
            rec, done = days[d]
            rest = rest + rec - done
            series.append({"日付": d, "受付件数": rec, "処理件数": done, "未処理件数": rest})
        peak = max(series, key=lambda x: x["未処理件数"])
        out[task] = {
            "期首の未処理件数": int(init.get(task, 0)),
            "期末の未処理件数": series[-1]["未処理件数"],
            "最大の未処理件数": peak["未処理件数"],
            "最大になった日": peak["日付"] if peak["未処理件数"] > 0 else "（未処理なし）",
            "未処理が増えた日数": sum(1 for i, x in enumerate(series)
                                if x["未処理件数"] > (series[i - 1]["未処理件数"] if i else int(init.get(task, 0)))),
            "日ごと": series,
        }
    return out


def stratify(records: list, by: str = "条件", task: str | None = None) -> dict:
    """条件・曜日・区分ごとに、1日あたりの受付件数の代表値を比べる。"""
    totals = _daily_totals(records, "受付件数", task)
    if not totals:
        return {"対象": task or "すべての業務", "全体": {}, "グループ": []}
    base_values = list(totals.values())
    groups = defaultdict(list)
    if by == "曜日":
        for d, v in totals.items():
            groups[WEEKDAYS[date.fromisoformat(d).weekday()]].append(v)
    elif by == "区分":
        per = defaultdict(lambda: defaultdict(int))
        for r in records:
            if task and r["業務"] != task:
                continue
            per[r["区分"]][r["日付"]] += r["受付件数"]
        for g, days in per.items():
            groups[g] = list(days.values())
        # 区分どうしを比べるため、基準は「区分ごと・日ごとの件数」全体の中央値にする
        base_values = [v for vals in groups.values() for v in vals]
    else:  # 条件
        tags_by_day = defaultdict(set)
        for r in records:
            for t in r["条件"]:
                tags_by_day[r["日付"]].add(t)
        for d, v in totals.items():
            tags = tags_by_day.get(d) or {"（条件なし）"}
            for t in tags:
                groups[t].append(v)
    overall_median = statistics.median(base_values)
    rows = []
    for g, vals in groups.items():
        med = statistics.median(vals)
        ratio = med / overall_median if overall_median else 0
        rows.append({
            "グループ": g,
            "日数": len(vals),
            "平均": _r1(statistics.mean(vals)),
            "中央値": _r1(med),
            "最頻値": _r1(statistics.mode(vals)) if len(vals) > 1 else _r1(vals[0]),
            "全体の中央値に対する倍率": round(ratio, 2),
            "判定": "多い" if ratio >= HIGH else ("少ない" if ratio <= LOW else ""),
        })
    rows.sort(key=lambda x: -x["全体の中央値に対する倍率"])
    return {
        "対象": task or "すべての業務",
        "比べ方": by,
        "全体": {"日数": len(totals), "平均": _r1(statistics.mean(base_values)),
               "中央値": _r1(overall_median)},
        "目安": (f"中央値が全体の{HIGH}倍以上を「多い」、{LOW}倍以下を「少ない」とする"
                 + ("（区分の比べ方では、区分ごと・日ごとの件数を全体とする）" if by == "区分" else "")),
        "グループ": rows,
    }


def weekly_bottleneck(records: list, settings: dict) -> list:
    """週ごとに、未処理件数が増えた業務を、増えた件数の多い順に並べる。"""
    bl = backlog(records, settings)
    out = []
    for task, b in bl.items():
        weeks = defaultdict(list)
        for x in b["日ごと"]:
            y, w, _ = date.fromisoformat(x["日付"]).isocalendar()
            weeks[(y, w)].append(x)
        prev_end = b["期首の未処理件数"]
        for _key, xs in sorted(weeks.items()):
            end = xs[-1]["未処理件数"]
            out.append({
                "週": f"{xs[0]['日付']}〜{xs[-1]['日付']}",
                "業務": task,
                "受付件数": sum(x["受付件数"] for x in xs),
                "処理件数": sum(x["処理件数"] for x in xs),
                "週初めの未処理件数": prev_end,
                "週末の未処理件数": end,
                "増えた件数": end - prev_end,
            })
            prev_end = end
    return sorted([x for x in out if x["増えた件数"] > 0], key=lambda x: -x["増えた件数"])


def staffing(records: list, settings: dict) -> dict:
    """受付件数と1件あたりの時間から、必要な人数と、未処理を片付けるのに要る人日を出す。"""
    per_case = settings.get("1件あたりの時間（分）", {})
    day_min = float(settings.get("1人1日の作業時間（分）", 360))
    days = len({r["日付"] for r in records})
    bl = backlog(records, settings)
    rows, missing = [], []
    for task in sorted({r["業務"] for r in records}):
        if task not in per_case:
            missing.append(task)
            continue
        m = float(per_case[task])
        rec = sum(r["受付件数"] for r in records if r["業務"] == task)
        total_min = rec * m
        rows.append({
            "業務": task,
            "受付件数": rec,
            "1件あたりの時間（分）": m,
            "必要な作業時間（時間）": _r1(total_min / 60),
            "1日あたりの必要人数": round(total_min / (day_min * days), 2) if days else 0,
            "期末の未処理件数": bl[task]["期末の未処理件数"],
            "未処理を片付けるのに要る人日": round(max(bl[task]["期末の未処理件数"], 0) * m / day_min, 2),
        })
    return {
        "営業日数": days,
        "1人1日の作業時間（分）": day_min,
        "業務ごと": rows,
        "合計の1日あたりの必要人数": round(sum(x["1日あたりの必要人数"] for x in rows), 2),
        "1件あたりの時間が未設定の業務": missing,
    }


ANALYSES = {
    "未処理の推移": lambda recs, s: backlog(recs, s),
    "条件で比べる": lambda recs, s: stratify(recs, "条件"),
    "曜日で比べる": lambda recs, s: stratify(recs, "曜日"),
    "区分で比べる": lambda recs, s: stratify(recs, "区分"),
    "週ごとの詰まり": lambda recs, s: weekly_bottleneck(recs, s),
    "必要人数の見積もり": lambda recs, s: staffing(recs, s),
}
