"""要約の数値を「指標ID」で参照させ、数値はコードが差し込む仕組み（数値のグラウンディング）。

LLMには数値を直接書かせず、{M3} のような指標IDで参照させる。検証では次の3点を見る。
  1. 指標IDの外に、数字が直接書かれていないか（数値の捏造を防ぐ）
  2. 参照した指標IDが実在するか
  3. 参照した文に、その指標を表す語（業務名・「期末」「最大」など）が含まれているか（指標の取り違えを防ぐ）
すべて通った要約だけを、集計結果の値で描画する。
"""
from __future__ import annotations

import re

REF = re.compile(r"\{(M\d+)\}")
DIGIT = re.compile(r"[0-9０-９]")
KANJI_NUM = re.compile(r"[〇一二三四五六七八九十百千万]+(?:件|人|日|倍|割|分|時間|週|%|％)")
# 数字を含むが、数値の主張ではない決まった言い回し
FIXED_PHRASES = ("1日あたり", "1件あたり", "1人1日", "1人あたり")
SENTENCE_END = re.compile(r"(?<=[。\n])")


def _fmt(v) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{int(v):,}" if v.is_integer() else f"{v:,}"
    return str(v)


def build_catalog(results: dict) -> list:
    """集計結果から、要約で参照できる指標の一覧（ID・名称・値・単位・確認語）を作る。"""
    cat: list = []

    def add(path, label, value, unit, keywords):
        cat.append({"id": f"M{len(cat) + 1}", "path": path, "label": label, "value": value,
                    "unit": unit, "keywords": keywords, "text": _fmt(value) + unit})

    for t, b in results.get("未処理の推移", {}).items():
        if b["最大の未処理件数"] == 0 and b["期首の未処理件数"] == 0:
            continue
        base = ("未処理の推移", t)
        add(base + ("期首の未処理件数",), f"{t}の期首の未処理件数", b["期首の未処理件数"], "件", [t, "期首"])
        add(base + ("期末の未処理件数",), f"{t}の期末の未処理件数", b["期末の未処理件数"], "件", [t, "期末"])
        add(base + ("最大の未処理件数",), f"{t}の最大の未処理件数", b["最大の未処理件数"], "件", [t, "最大"])
        add(base + ("最大になった日",), f"{t}の未処理が最大になった日", b["最大になった日"], "", [t, "最大"])
        add(base + ("未処理が増えた日数",), f"{t}の未処理が増えた日数", b["未処理が増えた日数"], "日", [t, "増えた日"])

    overall_done = False
    for key in ("条件で比べる", "曜日で比べる", "区分で比べる"):
        s = results.get(key)
        if not s or not s.get("全体"):
            continue
        if key == "区分で比べる":
            add((key, "全体", "中央値"), "区分ごと・日ごとの受付件数の全体の中央値", s["全体"]["中央値"], "件", ["区分", "全体", "中央値"])
        elif not overall_done:
            add((key, "全体", "中央値"), "1日あたりの受付件数の全体の中央値", s["全体"]["中央値"], "件", ["1日あたり", "全体", "中央値"])
            overall_done = True
        for g in s["グループ"]:
            name = g["グループ"]
            kw = {"条件で比べる": name.strip("（）"), "曜日で比べる": f"{name}曜", "区分で比べる": name}[key]
            disp = {"条件で比べる": f"条件「{name}」の日", "曜日で比べる": f"{name}曜日", "区分で比べる": f"区分「{name}」"}[key]
            add((key, name, "中央値"), f"{disp}の1日あたりの受付件数の中央値", g["中央値"], "件", [kw, "中央値"])
            add((key, name, "倍率"), f"{disp}の、全体の中央値に対する倍率", g["全体の中央値に対する倍率"], "倍", [kw, "全体"])

    # 指標名に数字を入れない（数字の直書きの検出と両立させるため）。順位は語で区別する
    for rank, w in zip(("最も", "次に", "三番目に"), results.get("週ごとの詰まり", [])[:3], strict=False):
        base = ("週ごとの詰まり", w["週"], w["業務"])
        add(base + ("週",), f"{w['業務']}の未処理が{rank}増えた週", w["週"], "", [w["業務"], "週", rank])
        add(base + ("増えた件数",), f"{w['業務']}の未処理が{rank}増えた週の増加件数", w["増えた件数"], "件", [w["業務"], "増え", rank])

    s = results.get("必要人数の見積もり")
    if s:
        add(("必要人数の見積もり", "営業日数"), "営業日数", s["営業日数"], "日", ["営業日"])
        add(("必要人数の見積もり", "合計の1日あたりの必要人数"), "合計の1日あたりの必要人数", s["合計の1日あたりの必要人数"], "人", ["合計", "1日あたり"])
        for x in s["業務ごと"]:
            t = x["業務"]
            add(("必要人数の見積もり", t, "1日あたりの必要人数"), f"{t}の1日あたりの必要人数", x["1日あたりの必要人数"], "人", [t, "1日あたり"])
            if x["未処理を片付けるのに要る人日"] > 0:
                add(("必要人数の見積もり", t, "未処理を片付けるのに要る人日"), f"{t}の未処理を片付けるのに要る人日",
                    x["未処理を片付けるのに要る人日"], "人日", [t, "未処理"])
    return cat


def catalog_for_prompt(cat: list) -> list:
    return [{"id": m["id"], "指標": m["label"], "値": m["text"]} for m in cat]


def check(draft: str, cat: list) -> dict:
    """指標IDで書かれた要約を検証し、通れば値を差し込んだ文と根拠の一覧を返す。"""
    by_id = {m["id"]: m for m in cat}
    violations, evidence = [], []
    rendered_parts = []
    n = 0
    for sent in SENTENCE_END.split(draft):
        if not sent.strip():
            rendered_parts.append(sent)
            continue
        n += 1
        bare = REF.sub("", sent)
        for p in FIXED_PHRASES:
            bare = bare.replace(p, "")
        if DIGIT.search(bare):
            violations.append({"文": n, "種類": "数字の直書き", "内容": sent.strip()})
        if KANJI_NUM.search(bare):
            violations.append({"文": n, "種類": "漢数字での数値", "内容": sent.strip()})
        rendered = REF.sub(lambda m: by_id[m.group(1)]["text"] if m.group(1) in by_id else m.group(0), sent)
        for mid in REF.findall(sent):
            m = by_id.get(mid)
            if not m:
                violations.append({"文": n, "種類": "存在しない指標ID", "内容": mid})
                continue
            missing = [k for k in m["keywords"] if k not in rendered]
            if missing:
                violations.append({"文": n, "種類": "指標の取り違えの疑い", "内容": f"{mid}（{m['label']}）を参照した文に「{'」「'.join(missing)}」がない"})
            evidence.append({"文": n, "数値": m["text"], "指標": m["label"], "集計結果の場所": " / ".join(map(str, m["path"]))})
        rendered_parts.append(rendered)
    return {"ok": not violations, "違反": violations, "根拠": evidence,
            "描画した要約": "".join(rendered_parts), "参照した指標の数": len(evidence)}
