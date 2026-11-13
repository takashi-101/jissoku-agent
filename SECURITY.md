# Security Policy

## 脆弱性の報告
GitHubの「Security」タブの Private vulnerability reporting から報告してください。公開のIssueには書かないでください。

## このリポジトリの安全上の前提
| 項目 | 内容 |
|---|---|
| 外部通信 | 実行時の通信は、許可したホスト（既定は 127.0.0.1 / localhost / ::1）のOllamaへの接続のみ。HTTPの転送（リダイレクト）は追わない |
| 有料API | 使わない。LLMは手元のOllama、MCPは標準入出力で、使用料のかかる呼び出しはない |
| LLMに渡すデータ | 業務名・区分名・集計結果の指標のみ。メールアドレス・電話番号・12桁の番号を含む場合は送信しない |
| 入力の検証 | 日付・件数の型、指示文とみられる語（プロンプトインジェクション）、使えない文字、最大行数を検査し、満たさない行は受け付けない |
| MCPサーバー | 読み取り専用。読めるのは `JISSOKU_DATA_DIR` の中の .csv / .json / .db だけで、5MBを超えるファイルと1行100KBを超える要求は受け付けない |
| 監査 | 各実行の入力・計画・下書き・検証・承認・報告書をハッシュチェーン付きのログに記録（LLMへの入力と出力は本文を残さずSHA-256のみ） |
| 依存関係 | `langgraph` のみ。版を固定し、CIで pip-audit、bandit、CodeQL を実行。Dependabotで更新を通知 |

詳しくは [docs/threat-model.md](docs/threat-model.md) を参照してください。
