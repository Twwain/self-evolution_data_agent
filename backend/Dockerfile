FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── 系统依赖 (git: GitPython 克隆仓库; curl: healthcheck) ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl \
    && rm -rf /var/lib/apt/lists/*

# ── 应用代码 (须在 pip install 前: hatchling packages=["app"] 打包时需 app/ 就位) ──
COPY pyproject.toml .
COPY app/ app/
COPY prompts/ prompts/

# ── Python 依赖 (含本包) ──
RUN pip install .

# ── 数据目录 (ChromaDB / repos / backups) ──
RUN mkdir -p /app/data

EXPOSE 8000

# ── 容器健康探针 (compose healthcheck 亦会探, 此为镜像自带兜底) ──
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# 禁止 --reload: ChromaDB PersistentClient 进程级单例, fork 子进程会失联 (见根 CLAUDE.md)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
