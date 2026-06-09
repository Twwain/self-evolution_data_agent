"""
训练管道 — 编排: 克隆 → LLM 解析 → 构建文档 → 灌入引擎 → 质量评估
"""

import asyncio
import json
import re as _re
import time
from typing import Any, Callable, Coroutine

from langfuse import observe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.metadata import async_session
from app.knowledge.code_parser import parse_repository
from app.knowledge.evaluator import evaluate_parse_quality
from app.knowledge.git_manager import clone_or_update
from app.knowledge.parse_result import ParseReport
from app.knowledge.schema_builder import (
    build_ddl_from_jpa,
    build_doc_from_jpa,
)
from app.knowledge.trainer_purge import purge_legacy_for_full_rebuild
from app.logging_config import get_logger, trace_id_var
from app.models import GitRepo, Namespace
from app.models.namespace import DataSource
from app.tracing import get_client as _get_lf_client

log = get_logger("trainer")

# 进度回调类型: async (percent, message) -> None
ProgressCallback = Callable[[int, str], Coroutine[Any, Any, None]]


def _repo_name(url: str) -> str:
    """git@gitlab.com:org/foo-service.git → foo-service"""
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def _sync_trace_id_to_log() -> None:
    """
    将 langfuse @observe 上下文中的 trace_id 同步到 logging contextvar.
    Worker 路径无 HTTP middleware, 必须在 @observe 入口手动同步,
    否则 worker 日志的 trace 字段为 "-".
    """
    lf = _get_lf_client()
    if lf is None:
        return
    try:
        tid = lf.get_current_trace_id()
        if tid:
            trace_id_var.set(tid)
    except Exception:
        pass


# ── 短命 session helpers — 用完即还连接 ──

async def _update_repo_status(repo_id: int, status: str):
    async with async_session() as db:
        repo = await db.get(GitRepo, repo_id)
        if repo:
            repo.parse_status = status
            await db.commit()


async def _update_repo_fields(repo_id: int, **fields):
    async with async_session() as db:
        repo = await db.get(GitRepo, repo_id)
        if repo:
            for k, v in fields.items():
                setattr(repo, k, v)
            await db.commit()



@observe(name="repo_training", as_type="chain")
async def run_training_pipeline(
    db: AsyncSession, ns: Namespace, repo: GitRepo
) -> ParseReport:
    """
    完整训练流程:
    1. 克隆/更新仓库
    2. LLM 统一解析 (替代三个 AST parser)
    3. 构建文档 (schema_builder 不变)
    4. 灌入对应引擎
    5. LLM 质量评估
    6. 存储报告
    """
    _sync_trace_id_to_log()
    start = time.time()
    report = ParseReport(repo_id=repo.id)
    name = _repo_name(repo.url)

    try:
        repo.parse_status = "cloning"
        await db.commit()

        # ── 1. 克隆 ──
        local_path, git_op = clone_or_update(repo.url, repo.branch, repo.id)
        repo.local_path = local_path

        # ── 2. LLM 解析 ──
        code_result, stats = parse_repository(local_path, repo_name=name)
        report.stats = stats

        # ── 3. 构建文档 (schema_builder 零改动) ──
        ddls = build_ddl_from_jpa(code_result.jpa_entities)
        jpa_docs = build_doc_from_jpa(code_result.jpa_entities)

        # ── 4. MySQL RAG 训练路径已删除 ──
        # MySQL schema 信息现在通过 SchemaCanonicalObject + driver introspect 提供,
        # 不再需要 DDL/SQL RAG 训练.
        _ = ddls, jpa_docs  # 保留入参兼容, 不训练

        # ── 5. LLM 质量评估 ──
        all_trained = ddls + jpa_docs
        report.duration_seconds = round(time.time() - start, 2)
        report = evaluate_parse_quality(report, all_trained)

        # ── 6. 存储 ──
        repo.parse_status = "parsed"
        repo.error_message = ""
        from datetime import datetime
        repo.parsed_at = datetime.now()
        repo.parse_report = json.dumps(_report_to_dict(report), ensure_ascii=False)

    except Exception as e:
        repo.parse_status = "error"
        repo.error_message = str(e)
        report.duration_seconds = round(time.time() - start, 2)
        report.evaluation_summary = f"训练失败: {e}"

    await db.commit()
    return report


def _report_to_dict(report: ParseReport) -> dict:
    """ParseReport dataclass → 可序列化 dict"""
    return {
        "repo_id": report.repo_id,
        "duration_seconds": report.duration_seconds,
        "stats": {
            "files_scanned": report.stats.files_scanned,
            "files_parsed": report.stats.files_parsed,
            "files_skipped": report.stats.files_skipped,
            "files_errored": report.stats.files_errored,
            "items_extracted": report.stats.items_extracted,
            "tables_found": report.stats.tables_found,
        },
        "ddls_trained": report.ddls_trained,
        "docs_trained": report.docs_trained,
        "sqls_trained": report.sqls_trained,
        "query_patterns_trained": report.query_patterns_trained,
        "completeness_score": report.completeness_score,
        "evaluation_summary": report.evaluation_summary,
    }


# ════════════════════════════════════════════
#  带进度追踪的训练管道 (Phase 3)
# ════════════════════════════════════════════

def _build_docs(code_result):
    """同步构建 schema 文档 — 纯计算"""
    return (
        build_ddl_from_jpa(code_result.jpa_entities),
        build_doc_from_jpa(code_result.jpa_entities),
    )


def _collect_referenced_mysql_tables(
    mybatis_entries: list[dict],
    jpa_entities: list[dict],
    coll_to_db: dict[str, str],
) -> set[str]:
    """聚合本 repo 引用的 mysql 表名 — 给 refresh_mysql_canonicals 收窄 introspect 范围.

    信号:
        - mybatis: SELECT entry 的 SQL 中 FROM/JOIN 命中的表
        - jpa: @Table 关联的实体表
    coll_to_db 用于剔除非 mysql ds 的表名 (mongo collection 同名混入会被 dict 校验剔除).
    返回空集 → 本 repo 与 mysql 无关, refresh_mysql_canonicals 早返 0 noop.
    """
    tables: set[str] = set()
    for entry in mybatis_entries or []:
        if (entry.get("type") or "").lower() != "select":
            continue
        sql = entry.get("canonical_sql") or entry.get("sql") or ""
        if not sql:
            continue
        for tbl in _re.findall(r"\b(?:FROM|JOIN)\s+([\w_]+)", sql, _re.IGNORECASE):
            if tbl in coll_to_db:
                tables.add(tbl)
    for entity in jpa_entities or []:
        tbl = entity.get("table_name") or entity.get("table") or ""
        if tbl and tbl in coll_to_db:
            tables.add(tbl)
    return tables


@observe(name="repo_training", as_type="chain")
async def run_training_pipeline_with_progress(
    repo_id: int, ns_id: int, ns_slug: str,
    repo_url: str, repo_branch: str,
    on_progress: ProgressCallback,
) -> ParseReport:
    """
    带进度回调的训练管道 — session-per-operation 模式
    每个 DB 写操作独立 session, 长时 I/O 阶段不持有连接

    阶段权重: 克隆 10% → 解析 30% → 构建 10% → 训练 30% → 评估 20%
    """
    _sync_trace_id_to_log()
    start = time.time()
    report = ParseReport(repo_id=repo_id)
    name = _repo_name(repo_url)
    log.info("[%s] 训练管道启动 repo_id=%d branch=%s",
             name, repo_id, repo_branch)

    # ── 1. 克隆 (0-10%) — to_thread 避免阻塞事件循环 ──
    await _update_repo_status(repo_id, "cloning")
    await on_progress(2, "克隆仓库...")
    t_clone = time.time()
    local_path, git_op = await asyncio.to_thread(clone_or_update, repo_url, repo_branch, repo_id)
    await _update_repo_fields(repo_id, local_path=local_path)
    log.info("[%s] %s 完成 耗时 %.1fs", name, git_op, time.time() - t_clone)
    await on_progress(10, "克隆完成")

    # ── 1.5 全量清场 (spec: 2026-05-21-git-source-full-purge) ──
    await on_progress(11, "清场中...")
    async with async_session() as purge_db:
        purge_result = await purge_legacy_for_full_rebuild(purge_db, repo_id, ns_id, repo_name=name)
        await purge_db.commit()
    log.info(
        "[%s] purge 完成 ke_deleted=%d open_tc=%d",
        name, purge_result.get("ke_deleted", 0), purge_result.get("term_conflicts", 0),
    )

    # ── 2. LLM 解析 (10-40%) — to_thread 避免阻塞事件循环 ──
    await _update_repo_status(repo_id, "parsing")
    await on_progress(12, "LLM 解析代码...")
    t_parse = time.time()
    code_result, stats = await asyncio.to_thread(parse_repository, local_path, repo_name=name)
    report.stats = stats
    log.info(
        "[%s] 解析完成 耗时 %.1fs 实体=%d SQL映射=%d Mongo文档=%d",
        name, time.time() - t_parse,
        len(code_result.jpa_entities),
        len(code_result.mybatis_entries),
        len(code_result.mongo_documents),
    )
    await on_progress(40, "代码解析完成")

    # ── 3. 构建文档 (40-50%) — 纯计算, to_thread 保险 ──
    await on_progress(42, "构建 Schema 文档...")
    ddls, jpa_docs = await asyncio.to_thread(
        _build_docs, code_result
    )
    log.info("[%s] Schema 构建 DDL=%d 文档=%d",
             name, len(ddls), len(jpa_docs))
    await on_progress(50, "文档构建完成")

    # ── 4.5 写入 schema canonical candidates (新) ──
    await on_progress(56, "写入 schema 候选...")
    from app.knowledge.extraction_writer import write_canonical_candidates_from_parse

    # 构建 collection/table → database 反查表 (实时连接 DataSource, 同时刷新 snapshot)
    coll_to_db: dict[str, str] = {}
    async with async_session() as ds_db:
        ds_rows = list((await ds_db.execute(
            select(DataSource).where(
                DataSource.namespace_id == ns_id,
            )
        )).scalars().all())
        for ds in ds_rows:
            try:
                if ds.db_type == "mongodb":
                    from pymongo import MongoClient
                    client = MongoClient(
                        host=ds.host, port=ds.port,
                        username=ds.username, password=ds.password,
                        authSource="admin",
                        serverSelectionTimeoutMS=settings.datasource_connect_timeout_ms,
                    )
                    colls = sorted(client[ds.database].list_collection_names())
                    client.close()
                    new_snap = json.dumps({"collections": colls})
                    if ds.schema_snapshot_json != new_snap:
                        ds.schema_snapshot_json = new_snap
                        log.info("[%s] MongoDB snapshot 刷新 ds_id=%d collections=%d",
                                 name, ds.id, len(colls))
                    for coll in colls:
                        if coll not in coll_to_db:
                            coll_to_db[coll] = ds.database
                elif ds.db_type == "mysql":
                    import pymysql
                    conn = pymysql.connect(
                        host=ds.host, port=ds.port, database=ds.database,
                        user=ds.username, password=ds.password,
                        connect_timeout=settings.datasource_connect_timeout_ms // 1000,  # noqa: hardcode
                    )
                    with conn.cursor() as cur:
                        cur.execute("SHOW TABLES")
                        tables = sorted(row[0] for row in cur.fetchall())
                    conn.close()
                    new_snap = json.dumps({"tables": tables})
                    if ds.schema_snapshot_json != new_snap:
                        ds.schema_snapshot_json = new_snap
                        log.info("[%s] MySQL snapshot 刷新 ds_id=%d tables=%d",
                                 name, ds.id, len(tables))
                    for tbl in tables:
                        if tbl not in coll_to_db:
                            coll_to_db[tbl] = ds.database
            except Exception as e:
                log.warning("[%s] snapshot 刷新失败 ds_id=%d db_type=%s: %s",
                            name, ds.id, ds.db_type, e)
                # fallback: 读旧 snapshot
                if ds.schema_snapshot_json:
                    try:
                        snap = json.loads(ds.schema_snapshot_json)
                        names = snap.get("collections", snap.get("tables", []))
                        for n in names:
                            if n not in coll_to_db:
                                coll_to_db[n] = ds.database
                    except Exception:
                        pass
        await ds_db.commit()
    if coll_to_db:
        log.info("[%s] collection→db 反查表 %d 条", name, len(coll_to_db))

    await write_canonical_candidates_from_parse(
        namespace_id=ns_id,
        repo_id=repo_id,
        jpa_entities=code_result.jpa_entities,
        mongo_documents=code_result.mongo_documents,
        enum_classes=code_result.enum_classes,
        relationships=code_result.relationships,
        where_evidence=code_result.where_evidence,
        coll_to_db=coll_to_db or None,
        repo_name=name,
    )

    # ── 4.5b upsert EnumDictionary (enum-knowledge-binding) ──
    if code_result.enum_classes:
        from app.knowledge.enum_dictionary_writer import upsert_enum_dictionary_from_code
        from app.knowledge.enum_extractor import EnumDef, EnumValue

        async with async_session() as enum_db:
            for ec in code_result.enum_classes:
                values = ec.get("values") or []
                enum_def = EnumDef(
                    enum_class=ec.get("enum_class", ""),
                    fully_qualified_name=ec.get("fully_qualified_name", ""),
                    values=[
                        EnumValue(
                            name=v["name"],
                            db_value=v.get("db_value", v["name"]),
                            description=v.get("description"),
                        )
                        for v in values
                    ],
                )
                if enum_def.enum_class:
                    await upsert_enum_dictionary_from_code(enum_db, ns_id, enum_def)
            await enum_db.commit()

    # ── 4.6 写入 knowledge entries proposed (新) ──
    await on_progress(60, "写入知识候选...")
    from app.knowledge.extraction_writer import extract_and_write_knowledge
    async with async_session() as ke_db:
        await extract_and_write_knowledge(
            ke_db,
            namespace_id=ns_id,
            repo_id=repo_id,
            mybatis_entries=code_result.mybatis_entries,
            business_terms=code_result.business_terms_candidates,
            business_rules=code_result.business_rules_candidates,
            repo_name=name,
        )
        await ke_db.commit()

    await on_progress(80, "候选写入完成")

    # ── 5. 质量评估 (80-95%) — to_thread 避免阻塞事件循环 ──
    await on_progress(82, "LLM 质量评估...")
    t_eval = time.time()
    all_trained = ddls + jpa_docs
    report.duration_seconds = round(time.time() - start, 2)
    report = await asyncio.to_thread(evaluate_parse_quality, report, all_trained)
    log.info("[%s] 评估完成 耗时 %.1fs score=%d",
             name, time.time() - t_eval, report.completeness_score)

    # ── 6. 存储结果 — 短命 session ──
    from datetime import datetime
    await _update_repo_fields(
        repo_id,
        parse_status="parsed",
        error_message="",
        parsed_at=datetime.now(),
        parse_report=json.dumps(_report_to_dict(report), ensure_ascii=False),
    )

    # ── 6.5 触发 promote ──
    # 必须在 step 6 (parse_status='parsed' commit) 之后:
    #   maybe_trigger_promote 闸门检查 all(r.parse_status=='parsed'), 若早于
    #   step 6 则当前 repo 自身永远拿不到最新状态, 全量重建场景下闸门永不通过.
    # 必须在 step 7 (terminology) 之前:
    #   terminology_refresher._load_canonicals 读 SchemaCanonicalObject, 若 SCO
    #   为空则整 ns 术语抽取直接 skip (reason=no_canonicals).
    # ── 6.4 MySQL introspect → mysql introspect candidate (Stage B B1) ──
    # 收窄到本 repo 引用过的表 (mybatis FROM/JOIN + jpa @Table). 跨 repo 并集
    # 由 candidate value_hash 幂等 + ns 级累积自然形成. 未引用的表永不入 candidate.
    referenced_tables = _collect_referenced_mysql_tables(
        code_result.mybatis_entries, code_result.jpa_entities, coll_to_db,
    )
    await on_progress(93, "MySQL Schema introspect...")
    async with async_session() as introspect_db:
        try:
            from app.knowledge.schema_canonical import refresh_mysql_canonicals
            ds_count = await refresh_mysql_canonicals(
                introspect_db, ns_id, ns_slug,
                referenced_tables=referenced_tables,
                repo_name=name,
                # 不在此内部 promote: 汇聚交给 step 6.5 maybe_trigger_promote 统一
                # 处理 (与 MongoDB 候选路径对齐), 避免同一训练内重复全量 promote.
                trigger_promote=False,
            )
            await introspect_db.commit()
            log.info("[%s] MySQL introspect 完成, 处理表数=%d", name, ds_count)
        except Exception as e:
            log.warning(
                "[%s] MySQL introspect 失败 (best-effort): %s", name, e,
            )
            await introspect_db.rollback()

    # ── 6.5 触发 promote ──
    await on_progress(95, "汇聚候选...")
    from app.knowledge.canonical_promote import maybe_trigger_promote
    async with async_session() as promo_db:
        await maybe_trigger_promote(promo_db, ns_id)
        await promo_db.commit()

    # ── 6.6 补充索引信息 (indexes_json + field indexed) ──
    from app.knowledge.schema_canonical import backfill_indexes_from_driver
    async with async_session() as idx_db:
        await backfill_indexes_from_driver(idx_db, ns_id)
        await idx_db.commit()

    # ── 7. 业务术语刷新已从训练管道移除 ──
    # 术语抽取改为用户手动触发 (POST /api/namespaces/{ns_id}/terminology/refresh),
    # 解决 SCO description 冲突未解决时术语质量低的时序问题.

    await on_progress(100, "完成")  # noqa: hardcode

    total = time.time() - start
    log.info("[%s] 训练管道完成 repo_id=%d 总耗时 %.1fs score=%d",
             name, repo_id, total, report.completeness_score)

    return report


# ════════════════════════════════════════════
#  Phase 2 Task 2.1 — 全量解析前置清场
# ════════════════════════════════════════════
# purge_legacy_for_full_rebuild 实现已迁移至 app.knowledge.trainer_purge;
# 文件顶部已 re-export 该名字以保持外部 import 兼容.
