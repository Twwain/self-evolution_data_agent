"""Phase 5 Task 1: 真实 repo 端到端 benchmark.

输出:
- enum 提取率: extracted_enum_count / expected_enum_count (人工标注)
- EXPLAIN 通过率: passed / total / connection_error
- 端到端耗时
- candidates 总数 / KE 总数

用法:
    cd backend
    python -m scripts. \
        --repo-url https://... --namespace_id 1 --expected-enum-count 50 \
        --output benchmark_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from sqlalchemy import func, select

from app.db.metadata import async_session
from app.knowledge.trainer import run_training_pipeline_with_progress
from app.models import (
    ExtractionFailureLog,
    GitRepo,
    KnowledgeEntry,
    Namespace,
    SchemaCanonicalCandidate,
)

logger = logging.getLogger(__name__)


async def benchmark_async(
    repo_url: str,
    namespace_id: int,
    expected_enum_count: int,
) -> dict:
    """跑完整训练管道, 收集统计指标."""
    started = time.monotonic()
    progress_log: list[tuple[int, str]] = []

    async def _on_progress(percent: int, message: str) -> None:
        progress_log.append((percent, message))

    # 获取 namespace
    async with async_session() as db:
        ns = await db.get(Namespace, namespace_id)
        if not ns:
            raise SystemExit(f"namespace {namespace_id} not found")
        ns_slug = ns.slug

        # 查找或创建 repo 记录
        existing = (await db.execute(
            select(GitRepo).where(
                GitRepo.namespace_id == namespace_id,
                GitRepo.url == repo_url,
            )
        )).scalar_one_or_none()
        if existing:
            repo_id = existing.id
            repo_branch = existing.branch or "master"
        else:
            repo = GitRepo(
                namespace_id=namespace_id,
                url=repo_url,
                branch="master",
                progress=0,
                progress_message="",
            )
            db.add(repo)
            await db.commit()
            await db.refresh(repo)
            repo_id = repo.id
            repo_branch = "master"

    # 跑训练管道
    await run_training_pipeline_with_progress(
        repo_id=repo_id,
        ns_id=namespace_id,
        ns_slug=ns_slug,
        repo_url=repo_url,
        repo_branch=repo_branch,
        on_progress=_on_progress,
    )
    total_secs = time.monotonic() - started

    # 统计
    async with async_session() as db:
        n_cand = await db.scalar(
            select(func.count(SchemaCanonicalCandidate.id)).where(
                SchemaCanonicalCandidate.namespace_id == namespace_id,
                SchemaCanonicalCandidate.repo_id == repo_id,
            )
        ) or 0

        n_enum = await db.scalar(
            select(func.count(SchemaCanonicalCandidate.id)).where(
                SchemaCanonicalCandidate.namespace_id == namespace_id,
                SchemaCanonicalCandidate.repo_id == repo_id,
                SchemaCanonicalCandidate.candidate_kind == "enum_values",
            )
        ) or 0

        n_ke = await db.scalar(
            select(func.count(KnowledgeEntry.id)).where(
                KnowledgeEntry.namespace_id == namespace_id,
                KnowledgeEntry.repo_id == repo_id,
            )
        ) or 0

        failures = (await db.execute(
            select(ExtractionFailureLog).where(
                ExtractionFailureLog.namespace_id == namespace_id,
                ExtractionFailureLog.repo_id == repo_id,
            )
        )).scalars().all()

    explain_failures = [f for f in failures if f.extraction_kind == "mybatis_example"]
    connection_errors = [f for f in failures if f.failure_type == "connection_error"]
    n_explain_total = n_ke + len(explain_failures)
    explain_pass_rate = n_ke / n_explain_total if n_explain_total > 0 else 0.0
    enum_extraction_rate = n_enum / expected_enum_count if expected_enum_count > 0 else 0.0

    report = {
        "repo": repo_url,
        "namespace_id": namespace_id,
        "total_seconds": round(total_secs, 1),
        "candidates_total": n_cand,
        "candidates_enum": n_enum,
        "expected_enum": expected_enum_count,
        "enum_extraction_rate": round(enum_extraction_rate, 3),
        "knowledge_entries": n_ke,
        "explain_failures": len(explain_failures),
        "connection_errors": len(connection_errors),
        "explain_pass_rate": round(explain_pass_rate, 3),
        "G6_enum_pass": enum_extraction_rate >= 0.80,
        "perf_pass": total_secs <= 30 * 60,
        "stages": [
            {"percent": p, "message": m} for p, m in progress_log
        ],
    }
    return report


def main() -> None:
    p = argparse.ArgumentParser(
        description="Schema 知识冷启动端到端 benchmark — 跑真实 repo 统计抽取率与性能",
    )
    p.add_argument("--repo-url", required=True, help="Git repo URL")
    p.add_argument("--namespace_id", type=int, required=True, help="Namespace ID")
    p.add_argument("--expected-enum-count", type=int, required=True,
                   help="人工标注的 enum 类数量 (用于计算提取率)")
    p.add_argument("--output", type=Path, default=Path("benchmark_report.json"),
                   help="输出 JSON 报告路径 (默认 benchmark_report.json)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    report = asyncio.run(
        benchmark_async(args.repo_url, args.namespace_id, args.expected_enum_count)
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    logger.info("benchmark report → %s", args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
