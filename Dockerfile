# 実行時に外部通信を必要としない構成。docker run --network none で動かせる（LLMを使わないルールモード）。
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY jissoku ./jissoku
COPY run_agent.py mcp_server.py ./
COPY sample ./sample
USER app
ENTRYPOINT ["python", "run_agent.py"]
CMD ["sample/minato_shiminka.csv", "--settings", "sample/settings.json", "--yes", "--out", "/tmp/report.md", "--audit", "/tmp/audit.jsonl"]
