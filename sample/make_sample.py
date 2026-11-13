"""見本データ（架空の「みなと市 市民課」の8週間）を作る。乱数の種を固定しているので、毎回同じものができる。"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

rnd = random.Random(7)  # noqa: S311  見本データ用（暗号用途ではない）
HOLIDAYS = {date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 10, 12)}
start, end = date(2026, 9, 7), date(2026, 10, 30)
days = [start + timedelta(n) for n in range((end - start).days + 1)]
days = [d for d in days if d.weekday() < 5 and d not in HOLIDAYS]

def month_end(d):
    same = [x for x in days if x.month == d.month]
    return d in same[-3:]

after_holiday = {date(2026, 9, 24), date(2026, 10, 13)}
rain = {d for d in days if rnd.random() < 0.25}

# 業務：（1日あたりの基本件数、処理できる上限、区分の割合（本庁））
TASKS = {
    "住民票の写し": (60, 999, 0.7),
    "印鑑登録証明書": (30, 999, 0.7),
    "郵送請求": (18, 20, 1.0),
    "転入届": (8, 999, 0.8),
}
rows = []
rest = {"郵送請求": 10}
for d in days:
    tags = []
    if rain.__contains__(d):
        tags.append("雨")
    if month_end(d):
        tags.append("月末")
    if d in after_holiday:
        tags.append("連休明け")
    for task, (base, cap, honcho) in TASKS.items():
        k = 1.0
        if "雨" in tags and task != "郵送請求":
            k *= 0.8
        if "月末" in tags:
            k *= 1.5 if task in ("郵送請求", "転入届") else 1.2
        if "連休明け" in tags:
            k *= 1.6
        if d.weekday() == 0:
            k *= 1.15
        n = max(0, round(base * k * rnd.uniform(0.85, 1.15)))
        for area, share in (("本庁", honcho), ("支所", 1 - honcho)):
            if share <= 0:
                continue
            rec = round(n * share)
            if task == "郵送請求":
                r0 = rest.get(task, 0)
                done = min(r0 + rec, cap)
                rest[task] = r0 + rec - done
            else:
                done = rec
            rows.append([d.isoformat(), task, area, rec, done, ";".join(tags)])

out = Path(__file__).with_name("minato_shiminka.csv")
with open(out, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["日付", "業務", "区分", "受付件数", "処理件数", "条件"])
    w.writerows(rows)
print(out, len(rows), "行", len(days), "日")
