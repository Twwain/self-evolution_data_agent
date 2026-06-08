FROM python:3.11-slim

WORKDIR /app

# ── 系统依赖 ──
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# ── Python 依赖 ──
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# ── 应用代码 ──
COPY app/ app/

# ── 数据目录 ──
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
