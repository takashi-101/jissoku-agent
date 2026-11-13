"""業務量の実測データを分析するエージェント（LangGraphで工程をつなぐ）。

工程：読み込み → 計画 → 集計 → 指標の一覧化 → 要約の下書き → 検証
      →（違反があればやり直し・1回まで。それでも違反があれば決まった文型に切り替え）→ 人の承認 → 報告書
数値は集計の関数だけが出す。LLMは集計の選択と、指標IDで数値を参照する要約の下書きだけを受け持つ。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Callable, TypedDict

from langgraph.graph import END, StateGraph

from . import audit, grounding, guard, llm, store, tools


class State(TypedDict, total=False):
    question: str
    csv_path: str
    settings_path: str | None
    model: str | None
    audit_path: str | None
    run_id: str
    use_ai: bool
    records: list
    load_errors: list
    settings: dict
    plan: list
    plan_note: str
    results: dict
    catalog: list
    draft: str
    draft_by: str
    check: dict
    retries: int
    fallback_note: str
    approval: str
    approver_note: str
    report: str
    log: list


def _log(state: State, msg: str) -> list:
    return state.get("log", []) + [f"{datetime.now():%H:%M:%S} {msg}"]


def _audit(state: State, event: str, data: dict) -> None:
    audit.append(state.get("audit_path"), state["run_id"], event, data)


def node_load(state: State) -> State:
    path = state["csv_path"]
    if path.endswith((".db", ".sqlite", ".sqlite3")):
        data = {"records": store.read_records(path), "errors": []}
    else:
        data = tools.load_records(path)
    settings = tools.load_settings(state.get("settings_path"))
    use_ai = llm.available(state.get("model"))
    run_id = state.get("run_id") or uuid.uuid4().hex[:12]
    st = {**state, "run_id": run_id}
    _audit(st, "load", {"input": path, "input_sha256": audit.sha256_file(path), "rows": len(data["records"]),
                        "rejected_rows": len(data["errors"]), "llm": state.get("model") if use_ai else None})
    msg = f"読み込み：{len(data['records'])}行、受け付けなかった行{len(data['errors'])}件。LLM：{'使う（' + state['model'] + '）' if use_ai else '使わない'}"
    return {"run_id": run_id, "records": data["records"], "load_errors": data["errors"], "settings": settings,
            "use_ai": use_ai, "retries": 0, "log": _log(state, msg)}


def node_plan(state: State) -> State:
    names = list(tools.ANALYSES)
    pii = guard.find_pii(state.get("question") or "")
    if pii:
        _audit(state, "pii_blocked", {"stage": "plan", "kinds": pii})
    if not state.get("use_ai") or pii:
        plan = names
        note = (f"質問に{pii}が含まれていたためLLMに送らず、すべての集計を行う" if pii
                else "LLMを使わないため、すべての集計を行う")
    else:
        system = "あなたは自治体の業務量の分析を手伝う。使える集計の名前だけを、JSONの配列で返す。説明や前置きは書かない。"
        user = f"質問：{state.get('question') or '業務量の状況を知りたい'}\n使える集計：{json.dumps(names, ensure_ascii=False)}"
        try:
            raw = llm.chat(state["model"], system, user)
            picked = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
            plan = [p for p in picked if p in names]
            dropped = [p for p in picked if p not in names]
        except (ValueError, KeyError, OSError, llm.EndpointNotAllowed) as e:
            plan, dropped = [], [f"（LLMの返答を読めなかった：{type(e).__name__}）"]
        note = ("LLMが質問に合わせて選んだ" + (f"。使えない名前を外した：{dropped}" if dropped else "")) if plan \
            else "LLMの選んだ集計が使えなかったため、すべての集計を行う"
        plan = plan or names
    _audit(state, "plan", {"plan": plan, "note": note})
    return {"plan": plan, "plan_note": note, "log": _log(state, f"計画：{plan}")}


def node_analyze(state: State) -> State:
    res = {name: tools.ANALYSES[name](state["records"], state["settings"]) for name in state["plan"]}
    cat = grounding.build_catalog(res)
    return {"results": res, "catalog": cat, "log": _log(state, f"集計：{len(res)}種類、参照できる指標{len(cat)}個")}


def template_draft(cat: list) -> str:
    """LLMを使わないときの下書き。数値は指標IDで参照するだけ。"""
    by = {m["path"]: m["id"] for m in cat}
    lines = []
    for m in cat:
        p = m["path"]
        if p[0] == "未処理の推移" and p[2] == "最大の未処理件数":
            t = p[1]
            lines.append(f"{t}は、未処理が{{{by[('未処理の推移', t, '最大になった日')]}}}に最大の{{{m['id']}}}になり、"
                         f"期末は{{{by[('未処理の推移', t, '期末の未処理件数')]}}}でした。")
        if p[0] in ("条件で比べる", "曜日で比べる", "区分で比べる") and p[2] == "倍率" and m["value"] and (m["value"] >= tools.HIGH or m["value"] <= tools.LOW):
            judge = "多い" if m["value"] >= tools.HIGH else "少ない"
            name = p[1]
            subj = {"条件で比べる": f"「{name.strip('（）')}」の日", "曜日で比べる": f"{name}曜日", "区分で比べる": f"区分「{name}」"}[p[0]]
            lines.append(f"{subj}は、1日あたりの受付件数の中央値が{{{by[(p[0], name, '中央値')]}}}で、全体の{{{m['id']}}}（{judge}）でした。")
    weeks = [m for m in cat if m["path"][0] == "週ごとの詰まり" and m["path"][3] == "増えた件数"]
    if weeks:
        w = weeks[0]
        lines.append(f"未処理が最も増えた週は{{{by[w['path'][:3] + ('週',)]}}}の{w['path'][2]}で、{{{w['id']}}}増えました。")
    if ("必要人数の見積もり", "合計の1日あたりの必要人数") in by:
        lines.append(f"受付件数と1件あたりの時間から見ると、合計で1日あたり{{{by[('必要人数の見積もり', '合計の1日あたりの必要人数')]}}}分の作業がありました。")
        for m in cat:
            if m["path"][0] == "必要人数の見積もり" and m["path"][-1] == "未処理を片付けるのに要る人日":
                lines.append(f"{m['path'][1]}の未処理を片付けるには、{{{m['id']}}}が要ります。")
    return "\n".join(lines) or "目立った増減はありませんでした。"


def node_draft(state: State) -> State:
    cat = state["catalog"]
    if not state.get("use_ai"):
        text = template_draft(cat)
        _audit(state, "draft", {"by": "template", "draft_sha256": audit.sha256_text(text)})
        return {"draft": text, "draft_by": "決まった文型", "log": _log(state, "下書き：決まった文型")}
    payload = json.dumps(grounding.catalog_for_prompt(cat), ensure_ascii=False)
    pii = guard.find_pii(payload + (state.get("question") or ""))
    if pii:
        _audit(state, "pii_blocked", {"kinds": pii})
        return {"draft": template_draft(cat), "draft_by": f"決まった文型（LLMに送る内容に{pii}が含まれていたため送信を止めた）",
                "log": _log(state, f"個人情報の可能性（{pii}）があるためLLMに送らない")}
    feedback = ""
    if state.get("check", {}).get("違反"):
        feedback = "\n前の下書きには次の違反があった。直して書き直すこと：" + json.dumps(state["check"]["違反"], ensure_ascii=False)
    system = ("あなたは自治体の係長に、業務量の分析結果を報告する。数値は絶対に直接書かず、指標の一覧のIDを {M3} のように"
              "波かっこで囲んで参照する（値と単位はシステムが差し込む）。IDを参照した文には、その指標名に含まれる業務名と、"
              "「期末」「最大」「中央値」などの語を必ず入れる。日本語の平易な文で、5〜8文で書く。")
    user = f"質問：{state.get('question') or '業務量の状況を知りたい'}\n指標の一覧：{payload}{feedback}"
    try:
        text = llm.chat(state["model"], system, user).strip()
        by = f"LLM（{state['model']}）"
    except (OSError, llm.EndpointNotAllowed) as e:
        text, by = template_draft(cat), f"決まった文型（LLMの呼び出しに失敗：{type(e).__name__}）"
    _audit(state, "draft", {"by": by, "prompt_sha256": audit.sha256_text(system + user), "draft_sha256": audit.sha256_text(text)})
    return {"draft": text, "draft_by": by, "log": _log(state, f"下書き：{by}")}


def node_verify(state: State) -> State:
    c = grounding.check(state["draft"], state["catalog"])
    _audit(state, "verify", {"ok": c["ok"], "violations": len(c["違反"]), "refs": c["参照した指標の数"]})
    return {"check": c, "log": _log(state, f"検証：参照した指標{c['参照した指標の数']}個、違反{len(c['違反'])}件")}


def route_after_verify(state: State) -> str:
    if state["check"]["ok"]:
        return "approve"
    if state.get("use_ai") and state.get("retries", 0) < 1:
        return "redo"
    return "fallback"


def node_retry(state: State) -> State:
    return {"retries": state.get("retries", 0) + 1, "log": _log(state, "違反を伝えて下書きをやり直す")}


def node_fallback(state: State) -> State:
    """やり直しても違反が残ったLLMの要約は使わず、決まった文型に切り替える。"""
    draft = template_draft(state["catalog"])
    c = grounding.check(draft, state["catalog"])
    note = f"LLMの要約に違反が残ったため採用せず、決まった文型に切り替えた（違反{len(state['check']['違反'])}件）"
    _audit(state, "fallback", {"violations": state["check"]["違反"]})
    return {"draft": draft, "draft_by": "決まった文型", "check": c, "fallback_note": note, "log": _log(state, note)}


def make_node_approve(ask: Callable[[State], tuple[str, str]]):
    def node_approve(state: State) -> State:
        decision, note = ask(state)
        _audit(state, "approval", {"decision": decision, "note": note})
        return {"approval": decision, "approver_note": note, "log": _log(state, f"承認：{decision}")}
    return node_approve


def _table(rows: list, cols: list) -> str:
    if not rows:
        return "（該当なし）\n"
    head = "| " + " | ".join(cols) + " |\n| " + " | ".join("---" for _ in cols) + " |\n"
    return head + "".join("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n" for r in rows)


def node_report(state: State) -> State:
    r, c = state["results"], state["check"]
    md = ["# 業務量の実測報告\n",
          f"- データ：{state['csv_path']}（{len(state['records'])}行）",
          f"- 質問：{state.get('question') or '（指定なし）'}",
          f"- 集計の選び方：{state['plan_note']}",
          f"- 要約の書き手：{state['draft_by']}" + (f"。{state['fallback_note']}" if state.get("fallback_note") else ""),
          f"- 承認：{state['approval']}" + (f"（{state['approver_note']}）" if state.get("approver_note") else ""),
          f"- 実行ID：{state['run_id']}", ""]
    if state.get("load_errors"):
        md += ["## 受け付けなかった行", *[f"- {e}" for e in state["load_errors"]], ""]
    md += ["## 要約", c["描画した要約"], "",
           f"> 要約の数値{c['参照した指標の数']}個は、すべて集計結果の指標を参照して差し込んだものです。下の「要約の根拠」で、文ごとに参照先を確認できます。", "",
           "## 要約の根拠", _table(c["根拠"], ["文", "数値", "指標", "集計結果の場所"])]
    if "未処理の推移" in r:
        rows = [{"業務": t, **{k: v for k, v in b.items() if k != "日ごと"}} for t, b in r["未処理の推移"].items()]
        md += ["## 未処理の推移", _table(rows, ["業務", "期首の未処理件数", "最大の未処理件数", "最大になった日", "期末の未処理件数", "未処理が増えた日数"])]
    for key in ("条件で比べる", "曜日で比べる", "区分で比べる"):
        if key in r:
            s = r[key]
            md += [f"## {key}（1日あたりの受付件数）",
                   f"全体：{s['全体'].get('日数')}日、平均{s['全体'].get('平均')}件、中央値{s['全体'].get('中央値')}件。{s.get('目安', '')}", "",
                   _table(s["グループ"], ["グループ", "日数", "平均", "中央値", "最頻値", "全体の中央値に対する倍率", "判定"])]
    if "週ごとの詰まり" in r:
        md += ["## 週ごとの詰まり（未処理が増えた週）",
               _table(r["週ごとの詰まり"], ["週", "業務", "受付件数", "処理件数", "週初めの未処理件数", "週末の未処理件数", "増えた件数"])]
    if "必要人数の見積もり" in r:
        s = r["必要人数の見積もり"]
        md += ["## 必要人数の見積もり",
               f"営業日数{s['営業日数']}日、1人1日の作業時間{s['1人1日の作業時間（分）']}分で計算。合計の1日あたりの必要人数：{s['合計の1日あたりの必要人数']}人", "",
               _table(s["業務ごと"], ["業務", "受付件数", "1件あたりの時間（分）", "必要な作業時間（時間）", "1日あたりの必要人数", "期末の未処理件数", "未処理を片付けるのに要る人日"])]
        if s["1件あたりの時間が未設定の業務"]:
            md += [f"1件あたりの時間が未設定のため計算していない業務：{'、'.join(s['1件あたりの時間が未設定の業務'])}", ""]
    md += ["## 処理の記録", *[f"- {x}" for x in state["log"]], ""]
    report = "\n".join(md)
    _audit(state, "report", {"report_sha256": audit.sha256_text(report)})
    return {"report": report, "log": _log(state, "報告書を作成")}


def build(ask: Callable[[State], tuple[str, str]]):
    g = StateGraph(State)
    for name, fn in (("load", node_load), ("plan", node_plan), ("analyze", node_analyze), ("draft", node_draft),
                     ("verify", node_verify), ("retry", node_retry), ("fallback", node_fallback),
                     ("approve", make_node_approve(ask)), ("report", node_report)):
        g.add_node(name, fn)
    g.set_entry_point("load")
    g.add_edge("load", "plan")
    g.add_edge("plan", "analyze")
    g.add_edge("analyze", "draft")
    g.add_edge("draft", "verify")
    g.add_conditional_edges("verify", route_after_verify, {"redo": "retry", "fallback": "fallback", "approve": "approve"})
    g.add_edge("retry", "draft")
    g.add_edge("fallback", "approve")
    g.add_edge("approve", "report")
    g.add_edge("report", END)
    return g.compile()
