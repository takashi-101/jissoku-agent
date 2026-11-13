"""業務量の実測エージェントを動かす。

例：
  python run_agent.py sample/minato_shiminka.csv --settings sample/settings.json
  python run_agent.py sample/minato_shiminka.csv --settings sample/settings.json --model qwen2.5:7b --question "郵送請求が滞る原因を知りたい"
  python run_agent.py data/jissoku.db --settings sample/settings.json   （SQLiteに取り込んだ記録から）
"""
import argparse
import sys
from pathlib import Path

from jissoku.agent import build


def ask_console(state):
    c = state["check"]
    print("\n----- 要約 -----")
    print(c["描画した要約"])
    print("\n----- 要約の根拠（文ごとの参照先） -----")
    for e in c["根拠"]:
        print(f"文{e['文']}：{e['数値']} ← {e['指標']}")
    ans = input("\nこの要約で報告書を作りますか？ [y=承認 / n=差し戻し] ").strip().lower()
    if ans == "y":
        return "承認", ""
    note = input("差し戻しの理由（任意）：").strip()
    return "差し戻し", note


def main():
    p = argparse.ArgumentParser(description="業務量の実測データを分析し、報告書を作る")
    p.add_argument("csv", help="日々の件数を記録したCSV（列：日付,業務,区分,受付件数,処理件数,条件）")
    p.add_argument("--settings", help="1件あたりの時間などを書いたJSON")
    p.add_argument("--model", help="手元のOllamaのモデル名。指定しなければAIを使わない")
    p.add_argument("--question", default="", help="知りたいこと（AIが集計を選ぶのに使う）")
    p.add_argument("--out", default="report.md", help="報告書の保存先")
    p.add_argument("--yes", action="store_true", help="確認を省いて承認する（試すとき用）")
    p.add_argument("--audit", default="audit/audit.jsonl", help="監査ログの保存先（空文字で記録しない）")
    a = p.parse_args()

    ask = (lambda s: ("承認", "確認を省いて承認（--yes）")) if a.yes else ask_console
    app = build(ask)
    final = app.invoke({"csv_path": a.csv, "settings_path": a.settings, "model": a.model,
                        "question": a.question, "audit_path": a.audit or None, "log": []})
    if final["approval"] != "承認":
        print("差し戻されたため、報告書は保存していません。")
        sys.exit(1)
    Path(a.out).write_text(final["report"], encoding="utf-8")
    print(f"報告書を保存しました：{a.out}")


if __name__ == "__main__":
    main()
