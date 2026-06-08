"""
统一代码解析器 — LLM 全量解析替代 AST 工具
用 LLM 替换 javalang/lxml, 提高成功率和语义理解深度
知识库构建是低频操作, token 成本可接受, 质量优先

核心流程: scan → pre_filter → ref_resolve → batch → LLM extract → merge
输出格式与 schema_builder 严格兼容, schema_builder 零改动
"""

import logging
import os
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from glob import glob
from typing import Any

from langfuse import observe

from app.config import settings
from app.engine.json_parser import parse_llm_json
from app.engine.llm import chat_completion
from app.knowledge.parse_result import (
    CodeParseResult,
    FileParseResult,
    ParserStats,
)

logger = logging.getLogger(__name__)

# ── 批处理配置 (Stage 2 Task 8: 搬到 IS_ env vars) ──
# 原则: batch_char_limit >= 3 * max_file_chars, 确保 seed + 2 refs 能同批
_BATCH_CHAR_LIMIT = settings.code_parse_batch_char_limit
_MAX_FILE_CHARS = settings.code_parse_max_file_chars
_LLM_MAX_TOKENS = settings.code_parse_llm_max_tokens
_LLM_TEMPERATURE = 0.1
_MAX_CONT_BATCHES_PER_SEED = 4  # 单 seed 最多开 4 个续批 (seed + 续 refs), 防 ref 爆炸

# ── 多轮分层展开 (复杂 Entity 504 治理, 2026-05-23 spec) ──────
# DEBT(multi-round-2026-05-23):
# 阈值分支违反 "消除特殊情况" 原则, 长期目标统一为 "骨架+按需展开" 单管线
# (重写 _JAVA_SYSTEM_PROMPT) 或基础设施侧治本 (nginx proxy_read_timeout=600).
# 见 docs/superpowers/specs/2026-05-23-multi-round-entity-parse/00-overview.md §D2.

_LEAF_TYPES: frozenset[str] = frozenset({
    "String", "int", "long", "short", "byte", "float", "double", "boolean", "char",
    "Integer", "Long", "Short", "Byte", "Float", "Double", "Boolean", "Character",
    "Date", "LocalDate", "LocalDateTime", "Instant",
    "BigDecimal", "BigInteger", "ObjectId", "UUID",
    "byte[]",
})

_RE_STATIC_CLASS = re.compile(r"^\s*public\s+static\s+class\s+(\w+)", re.MULTILINE)
_RE_STATIC_CLASS_BLOCK = re.compile(
    r"public\s+static\s+class\s+(\w+)\s*\{",
)
_RE_FIELD_DECL = re.compile(
    r"^\s*(?:private|protected|public)"
    r"(?:\s+(?:static|final|transient|volatile))*\s+"
    r"([A-Z]\w*(?:\s*<[^>]+>)?(?:\[\])?)\s+"
    r"(\w+)\s*[=;]",
    re.MULTILINE,
)

# ── 预筛关键词 — 命中任一即保留 ──
_JAVA_KEYWORDS = re.compile(
    r"@Entity|@Table|@Document|@Column|@Field"
    r"|@ManyToOne|@OneToMany|@ManyToMany|@OneToOne"
    r"|MongoRepository|CrudRepository|MongoTemplate|@Repository"
)
_XML_KEYWORDS = re.compile(r"<mapper|<select|<insert|<update|<delete")


# ════════════════════════════════════════════
#  公开接口
# ═══════���════════════════════════════════════

@observe(name="code_parse", as_type="chain")
def parse_repository(
    repo_path: str, *, repo_name: str | None = None,
) -> tuple[CodeParseResult, ParserStats]:
    """
    扫描 Java 项目, LLM 提取数据库相关定义
    返回 (CodeParseResult, ParserStats) 兼容 schema_builder 输入格式
    """
    stats = ParserStats()
    result = CodeParseResult()
    name = repo_name or os.path.basename(repo_path).removesuffix(".git")

    # ── 1. 扫描 ──
    java_files, xml_files = _scan_files(repo_path)
    stats.files_scanned = len(java_files) + len(xml_files)
    logger.info("[%s] 扫描完成 java=%d xml=%d 总计=%d",
                name, len(java_files), len(xml_files), stats.files_scanned)

    # ── 2. 预筛 ──
    java_relevant, java_skipped = _pre_filter(java_files, _JAVA_KEYWORDS)
    xml_relevant, xml_skipped = _pre_filter(xml_files, _XML_KEYWORDS)
    logger.info("[%s] 预筛完成 java_relevant=%d xml_relevant=%d skipped=%d",
                name, len(java_relevant), len(xml_relevant), len(java_skipped) + len(xml_skipped))
    for f in java_skipped + xml_skipped:
        stats.files_skipped += 1
        stats.error_details.append(
            FileParseResult(file_path=f, status="skipped", reason="无数据库关键词")
        )

    # ── 2.5 索引: @Document 类 + DAO 引用 + class→file 映射 ──
    annotated_classes = _scan_document_classes(java_files)
    dao_class_to_coll, dao_referenced_classes = _scan_dao_collection_mappings(java_files)
    class_to_file, simple_name_index = _build_class_index(java_files)

    # ── 2.6 隐藏 Entity 发现 — DAO 引用且无 @Document, 且能定位到源码 ──
    # 这类 Entity 需要送 LLM 解析其字段, collection 由 DAO 字面量提供
    hidden_entities: dict[str, str] = {}
    for cls in dao_referenced_classes - annotated_classes:
        coll = dao_class_to_coll.get(cls)
        paths = simple_name_index.get(cls, [])
        if not coll or not paths:
            # 无 collection 字面量的纯 .class 调用 (Map.class / 投影类) 跳过
            # 找不到源码文件的类 (jar 依赖) 跳过
            continue
        hidden_entities[cls] = coll
    if hidden_entities:
        logger.info("[%s] 发现隐藏 Entity %d 个: %s",
                    name, len(hidden_entities), hidden_entities)
    if dao_class_to_coll:
        logger.info("[%s] DAO class→collection 映射 %d 条: %s",
                    name, len(dao_class_to_coll),
                    dict(list(dao_class_to_coll.items())[:5]))

    # ── 2.7 构建 seeds — 仅 Entity 类文件 ──
    # @Document 注解类 + 隐藏 Entity (DAO 发现), 不含 DAO/Controller 等非 Entity
    hidden_seed_files: set[str] = set()
    for cls in hidden_entities:
        for p in simple_name_index.get(cls, []):
            hidden_seed_files.add(p)
    annotated_seed_files: set[str] = set()
    for cls in annotated_classes:
        paths = simple_name_index.get(cls, [])
        if len(paths) == 1:
            annotated_seed_files.add(paths[0])
        elif len(paths) > 1:
            # 同名类多个文件, 全部加入 (罕见)
            annotated_seed_files.update(paths)
    seeds = list(annotated_seed_files | hidden_seed_files)
    logger.info("[%s] seeds: @Document=%d 隐藏Entity=%d 总=%d",
                name, len(annotated_seed_files), len(hidden_seed_files), len(seeds))

    # ── 2.8 引用追踪 — 从 seeds (含隐藏 Entity) 递归发现依赖类型 ──
    all_relevant_java, ref_map = _resolve_references(seeds, java_files, repo_name=name)
    ref_only = [f for f in all_relevant_java if f not in set(seeds)]
    ref_set = set(ref_only)
    logger.info("[%s] 引用追踪 seeds=%d refs=%d total=%d",
                name, len(seeds), len(ref_only), len(all_relevant_java))

    # ── 3. LLM 批量解析 Java (种子 + 引用上下文 + 隐藏 Entity 外部映射) ──
    logger.info("[%s] Java LLM 解析开始 files=%d hidden_overrides=%d",
                name, len(all_relevant_java), len(hidden_entities))
    entities, mongo_docs, query_patterns, java_stats = _parse_java_batch(
        all_relevant_java, ref_set, ref_map=ref_map,
        hidden_entity_collections=hidden_entities,
        repo_name=name,
    )

    # ── 3.1 集合名修正 — 用 DAO 映射修正 LLM 给出的假集合名 ──
    # 对 @Document 类: LLM 可能写错 collection, regex 是真值
    # 对隐藏 Entity: prompt 里已注入正确 collection, 此处兜底确保不被 LLM 改写
    if dao_class_to_coll and mongo_docs:
        corrected = 0
        for doc in mongo_docs:
            cls = doc.get("class_name", "")
            coll = doc.get("collection", "")
            real_coll = dao_class_to_coll.get(cls)
            if real_coll and real_coll != coll:
                logger.info("[%s]   集合名修正: class=%s %s → %s", name, cls, coll, real_coll)
                doc["collection"] = real_coll
                corrected += 1
        if corrected:
            logger.info("[%s] 集合名修正 %d 条 (via DAO mapping)", name, corrected)

    # ── 3.2 白名单过滤 — 消灭 LLM 幻觉 mongo_docs ──
    # 允许的 class = @Document 注解类 ∪ DAO 发现的隐藏 Entity
    allowed_classes = annotated_classes | set(hidden_entities.keys())
    mongo_docs, dropped = _filter_mongo_docs_by_annotation(mongo_docs, allowed_classes)
    if dropped:
        logger.info("[%s] LLM 幻觉 mongo_docs 丢弃 %d 条: %s",
                    name, len(dropped),
                    [d.get("class_name") for d in dropped])

    # ── 3.3 后置补全 sub_fields — 修复 LLM 深层嵌套偷懒 ──
    backfilled = _backfill_sub_fields(mongo_docs)
    if backfilled:
        logger.info("[%s] sub_fields 后置补全 %d 个字段", name, backfilled)

    # ── 3.4 日志: LLM 输出的 mongo_docs 详情 ──
    if mongo_docs:
        logger.info("[%s] LLM 输出 mongo_docs %d 条:", name, len(mongo_docs))
        for d in mongo_docs:
            logger.info("[%s]   class=%s collection=%s fields=%d file=%s",
                        name, d.get("class_name", "?"), d.get("collection", "?"),
                        len(d.get("fields", [])), d.get("file", "?"))

    result.jpa_entities.extend(entities)
    result.mongo_documents.extend(mongo_docs)
    result.mongo_query_patterns.extend(query_patterns)
    stats.files_parsed += java_stats["parsed"]
    stats.files_errored += java_stats["errored"]
    stats.error_details.extend(java_stats["errors"])
    logger.info("[%s] Java 解析完成 parsed=%d errored=%d 实体=%d mongo=%d patterns=%d",
                name, java_stats["parsed"], java_stats["errored"],
                len(entities), len(mongo_docs), len(query_patterns))

    # ── 3.5 enum 专精解析 (Phase 2) ──
    enum_files = _scan_enum_classes(java_files)
    logger.info("[%s] enum 扫描完成 enum_files=%d", name, len(enum_files))
    if enum_files:
        enum_classes, enum_class_index = _parse_enum_classes_batch(enum_files, repo_name=name)
        logger.info("[%s] enum 解析完成 enum_classes=%d index_keys=%d",
                    name, len(enum_classes), len(enum_class_index))
        result.enum_classes.extend(enum_classes)
        # 用 enum_class_index 回填 entity/mongo_doc 字段 (转换为 EnumDef 供完整 4 层匹配)
        enum_def_index: dict[str, EnumDef] = {
            k: EnumDef(
                enum_class=v.get("enum_class", ""),
                fully_qualified_name=v.get("fully_qualified_name", ""),
                values=[
                    EnumValue(
                        name=ev["name"],
                        db_value=ev.get("db_value", ev["name"]),
                        description=ev.get("description"),
                    )
                    for ev in v.get("values", [])
                ],
            )
            for k, v in enum_class_index.items()
        }
        _enrich_entity_fields_with_enum_index_impl(
            result.jpa_entities, result.mongo_documents, enum_def_index
        )
    else:
        logger.info("[%s] 无 enum 文件, 跳过 enum 解析", name)

    # ── 4. LLM 批量解析 XML ──
    logger.info("[%s] XML LLM 解析开始 files=%d", name, len(xml_relevant))
    mybatis, xml_stats = _parse_xml_batch(xml_relevant, repo_name=name)
    result.mybatis_entries.extend(mybatis)
    stats.files_parsed += xml_stats["parsed"]
    stats.files_errored += xml_stats["errored"]
    stats.error_details.extend(xml_stats["errors"])
    logger.info("[%s] XML 解析完成 parsed=%d errored=%d SQL映射=%d",
                name, xml_stats["parsed"], xml_stats["errored"], len(mybatis))

    # ── 5. 汇总统计 ���─
    # ── 5. 汇总统计 ──
    stats.items_extracted = (
        len(result.jpa_entities)
        + len(result.mongo_documents)
        + len(result.mybatis_entries)
        + len(result.mongo_query_patterns)
        + len(result.enum_classes)
    )
    stats.tables_found = (
        [e["table"] for e in result.jpa_entities]
        + [d["collection"] for d in result.mongo_documents]
    )

    # ── 6. 集合名解析 — 将 query_patterns 中的类名映射为实际集合名 ──
    class_to_coll = {d["class_name"]: d["collection"] for d in result.mongo_documents}
    for p in result.mongo_query_patterns:
        if p["collection"] in class_to_coll:
            p["collection"] = class_to_coll[p["collection"]]

    return result, stats


# ════════════════════════════════════════════
#  内部: 扫描 + 预筛
# ════════════════════════════════════════════

def _scan_files(repo_path: str) -> tuple[list[str], list[str]]:
    """递归扫描 .java 和 .xml 文件, 排除测试目录"""
    java_files = [
        f for f in glob(os.path.join(repo_path, "**", "*.java"), recursive=True)
        if "/src/test/" not in f
    ]
    xml_files = [
        f for f in glob(os.path.join(repo_path, "**", "*.xml"), recursive=True)
        if "/src/test/" not in f
    ]
    return java_files, xml_files


def _scan_dao_collection_mappings(java_files: list[str]) -> tuple[dict[str, str], set[str]]:
    """
    从 DAO 文件中提取 class → collection 映射, 按调用频次投票.

    返回: (mappings, all_referenced_classes)
      - mappings: 能确定 collection 的 class → collection (频次投票赢家)
      - all_referenced_classes: 所有被 mongoTemplate 引用过的 class 名集合
        (不含字符串 collection 的调用也计入, 用于隐藏 Entity 发现)

    识别模式:
    1. mongoTemplate.<method>(..., XxxEntity.class)                    — class 无字符串
    2. mongoTemplate.<method>(..., XxxEntity.class, "c_xxx")           — class + collection
    3. mongoTemplate.(save|insert)(varName, "c_xxx")                   — 变量名反推 class

    模式 1 不产生 collection 映射 (只有 @Document 注解才有), 但会把 class 记入
    all_referenced_classes, 使 parse_repository 能识别 "哥要找的隐藏 Entity".

    投票规则:
    - 统计同一 class 被调用各 collection 的次数
    - 取频次最高的 collection 作为映射
    - 频次相同时取字母序最小的 (稳定结果, 避免文件遍历顺序影响)

    历史 bug (已修复 2026-04-22):
    - 旧实现用 first-seen, 一个类在多个 DAO 被调用不同 collection 时丢失主路径
    - 例: ProductEntity 在 ProductDaoImpl × 6 用 c_product,
      在 ProductDaoTmpImpl × 2 用 c_product_tmp
      glob 顺序让 Tmp 先扫 → 误映射 c_product_tmp
    - 现改为频次投票, 多数派赢
    """
    from collections import Counter

    counters: dict[str, Counter[str]] = {}
    all_classes: set[str] = set()

    # 通用变量名黑名单 (模式 3 从变量名反推类名时排除)
    _GENERIC_VARS = {
        "null", "new", "this", "list", "map", "result",
        "data", "obj", "item", "entity", "doc", "record",
    }

    for path in java_files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue
        if "mongoTemplate" not in content:
            continue

        # ── 模式 A: 含 .class 的调用 (覆盖 1 + 2) ──
        # 匹配 mongoTemplate.<method>(..., XxxEntity.class [, "c_xxx"])
        # 用 (?: ..., "([^"]+)")? 把 collection 字符串做成可选组
        for m in re.finditer(
            r'mongoTemplate\.\w+\([^;]*?(\w+)\.class'
            r'(?:\s*,\s*"([^"]+)")?',
            content, re.DOTALL,
        ):
            cls = m.group(1)
            coll = m.group(2)  # None 当无字符串字面量
            if cls == "Object":
                continue
            all_classes.add(cls)
            if coll:
                counters.setdefault(cls, Counter())[coll] += 1

        # ── 模式 B: mongoTemplate.(save|insert)(varName, "c_xxx") ──
        # 从变量名反推类名 (xxxEntity → XxxEntity)
        for m in re.finditer(
            r'mongoTemplate\.(?:save|insert)\(\s*(\w+)\s*,\s*"([^"]+)"', content
        ):
            var_name, coll = m.group(1), m.group(2)
            if var_name in _GENERIC_VARS:
                continue
            cls = var_name[0].upper() + var_name[1:] if var_name else ""
            if not cls or cls == "Object":
                continue
            # 验证: 在文件中查找该类名的声明或 import (防把 DTO 变量当 Entity)
            if re.search(rf'\b{cls}\b', content):
                all_classes.add(cls)
                counters.setdefault(cls, Counter())[coll] += 1

    # 频次投票: 取 count 最高的, count 相同时取字母序最小 (稳定)
    mappings: dict[str, str] = {}
    for cls, counter in counters.items():
        best_coll, _ = max(
            counter.items(),
            key=lambda kv: (kv[1], -sum(ord(c) for c in kv[0])),
        )
        mappings[cls] = best_coll

    return mappings, all_classes


# ── @Document 注解扫描 — 快速 regex, 不调 LLM ──
_RE_DOCUMENT_CLASS = re.compile(
    r"@Document\b.*?\n"           # @Document 注解行
    r"(?:@\w+.*?\n)*"             # 可能有其他注解 (@Getter, @Setter 等)
    r"\s*(?:public\s+)?class\s+(\w+)",  # class 声明
    re.DOTALL,
)


def _scan_document_classes(java_files: list[str]) -> set[str]:
    """
    扫描所有 Java 文件, 提取有 @Document 注解的顶层类名
    只匹配紧跟 @Document 注解的 class 声明, 不包含内部类
    """
    classes: set[str] = set()
    for path in java_files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue
        for m in _RE_DOCUMENT_CLASS.finditer(content):
            classes.add(m.group(1))
    return classes


# ── enum 类扫描 — 委托 enum_extractor ──
from app.knowledge.enum_extractor import (
    EnumDef,
    EnumValue,
    scan_enum_classes as _scan_enum_classes_impl,
    enrich_entity_fields_with_enum_index as _enrich_entity_fields_with_enum_index_impl,
)

_RE_PUBLIC_ENUM = re.compile(r"\bpublic\s+enum\s+\w+")


def _scan_enum_classes(java_files: list[str]) -> list[str]:
    """
    扫描所有 Java 文件, 返回包含 `public enum` 声明的文件路径列表.
    纯 regex, 不调 LLM. 委托 enum_extractor.scan_enum_classes.
    """
    return _scan_enum_classes_impl(java_files)


_ENUM_EXTRACTION_SYSTEM_PROMPT = """\
你是 Java 枚举类分析专家。分析以下 Java enum 源码, 提取枚举定义。

【db_value 推断规则】
- 枚举有构造参数 (如 `CREATED(1, "已创建")`) → 取首位 int 作 db_value
- 无构造参数 (纯名字枚举) → db_value = name (字符串)

【description 推断顺序】
1. 枚举常量上方 Javadoc 注释
2. 构造参数末位 String 参数
3. 无法推断 → null

返回严格 JSON (不要包含 markdown 代码块标记):
{
  "enum_class": "OrderStatus",
  "fully_qualified_name": "com.example.OrderStatus",
  "values": [
    {"name": "CREATED", "db_value": 1, "description": "已创建"},
    {"name": "PAID", "db_value": 2, "description": "已支付"}
  ]
}

【校验规则】
- values 非空数组
- 每个 value 的 name 非空字符串
- db_value 类型一致 (全 int 或全 string)
- fully_qualified_name 从 package 声明推断"""


def _extract_enum_blocks(content: str, file_path: str) -> list[tuple[str, str]]:
    """从 Java 文件中提取 enum 代码块.

    返回 [(enum_snippet, package_hint), ...]:
    - enum_snippet: 从 `public enum Xxx {` 到对应闭合 `}` 的完整代码
    - package_hint: 文件的 package 声明 (用于推断 fully_qualified_name)

    对于内嵌 enum (在 class 内部), 只提取 enum 块本身, 不包含外层 class 代码.
    """
    # 提取 package 声明
    package_match = re.search(r"^\s*package\s+([\w.]+)\s*;", content, re.MULTILINE)
    package_hint = package_match.group(1) if package_match else ""

    blocks: list[tuple[str, str]] = []

    # 找所有 public enum 声明的位置
    for m in _RE_PUBLIC_ENUM.finditer(content):
        start = m.start()
        # 向前找到行首 (可能有注解/注释)
        line_start = content.rfind("\n", 0, start) + 1
        # 向前最多取 5 行 (注解 + Javadoc)
        prefix_start = line_start
        for _ in range(5):
            prev_line = content.rfind("\n", 0, prefix_start - 1)
            if prev_line < 0:
                break
            candidate_line = content[prev_line + 1:prefix_start].strip()
            if candidate_line.startswith("@") or candidate_line.startswith("*") or candidate_line.startswith("//") or candidate_line.startswith("/**"):
                prefix_start = prev_line + 1
            else:
                break

        # 找 enum 体的开始 `{`
        brace_start = content.find("{", m.end())
        if brace_start < 0:
            continue

        # 用括号计数找闭合 `}`
        depth = 1
        pos = brace_start + 1
        while pos < len(content) and depth > 0:
            ch = content[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1

        if depth != 0:
            continue  # 未闭合, 跳过

        enum_snippet = content[prefix_start:pos].strip()
        blocks.append((enum_snippet, package_hint))

    return blocks


def _parse_enum_classes_batch(
    enum_files: list[str], repo_name: str = "",
) -> tuple[list[dict], dict[str, dict]]:
    """
    逐个 enum 文件提取 enum 代码块, 再调 LLM 解析枚举定义.

    改进: 不送整个文件给 LLM, 而是提取 `public enum Xxx { ... }` 代码块,
    避免大型 Service 文件中的业务代码干扰 LLM 判断.
    一个文件可能包含多个 enum (内嵌类), 每个 enum 独立送 LLM.

    返回 (enum_classes_list, enum_class_index)
    - enum_classes_list: 所有成功解析的 enum dict
    - enum_class_index: 双索引 (simple_name + fully_qualified_name → enum dict)
    """
    enum_classes: list[dict] = []
    enum_class_index: dict[str, dict] = {}

    for path in enum_files:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            logger.warning("[%s] enum 文件读取失败: %s", repo_name, path)
            continue

        # 提取 enum 代码块 (不送整个文件)
        blocks = _extract_enum_blocks(content, path)
        if not blocks:
            logger.debug("[%s] enum 文件无可提取 enum 块, 跳过: %s", repo_name, path)
            continue

        for enum_snippet, package_hint in blocks:
            # 构造 LLM 输入: package 上下文 + enum 代码块
            llm_input = f"package {package_hint};\n\n{enum_snippet}" if package_hint else enum_snippet

            # 截断过长 enum (极端情况)
            if len(llm_input) > _MAX_FILE_CHARS:
                llm_input = llm_input[:_MAX_FILE_CHARS] + "\n// ... 已截断"

            messages = [
                {"role": "system", "content": _ENUM_EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": llm_input},
            ]
            try:
                raw = chat_completion(
                    messages=messages,
                    temperature=_LLM_TEMPERATURE,
                    max_tokens=4096,  # noqa: hardcode
                )
                data = _parse_json(raw)
                if not data:
                    logger.warning("[%s] enum LLM 输出 JSON 解析失败, 跳过: %s", repo_name, path)
                    continue

                # 校验 values 非空
                values = data.get("values")
                if not values or not isinstance(values, list):
                    logger.warning("[%s] enum values 为空或非数组, 跳过: %s", repo_name, path)
                    continue

                # 校验每个 value.name 非空
                valid = True
                for v in values:
                    if not v.get("name"):
                        valid = False
                        break
                if not valid:
                    logger.warning("[%s] enum value.name 为空, 跳过: %s", repo_name, path)
                    continue

                enum_classes.append(data)

                # 双索引: simple_name + fully_qualified_name
                simple_name = data.get("enum_class", "")
                fqn = data.get("fully_qualified_name", "")
                if simple_name:
                    enum_class_index[simple_name] = data
                if fqn:
                    enum_class_index[fqn] = data

            except Exception as e:
                logger.warning("[%s] enum LLM 解析异常, 跳过 %s: %s", repo_name, path, e)
                continue

    return enum_classes, enum_class_index


def _filter_mongo_docs_by_annotation(
    mongo_docs: list[dict], document_classes: set[str],
) -> tuple[list[dict], list[dict]]:
    """
    过滤 LLM 输出的 mongo_docs — 只保留 class_name 在 @Document 类集合中的

    返回 (kept, dropped)
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for doc in mongo_docs:
        class_name = doc.get("class_name", "")
        if class_name in document_classes:
            kept.append(doc)
        else:
            dropped.append(doc)
    return kept, dropped


def _pre_filter(
    files: list[str], pattern: re.Pattern
) -> tuple[list[str], list[str]]:
    """快速 regex 预筛, 只保留包含关键词的文件"""
    relevant, skipped = [], []
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            if pattern.search(content):
                relevant.append(f)
            else:
                skipped.append(f)
        except Exception:
            skipped.append(f)
    return relevant, skipped


# ════════════════════════════════════════════
#  内部: 引用追踪 — 从种子文件 import 递归发现项目依赖
# ════════════════════════════════════════════

_RE_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_RE_IMPORT = re.compile(r"^\s*import\s+([\w.]+)\s*;", re.MULTILINE)


def _build_class_index(java_files: list[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """
    构建两级索引: FQN → path, simple_name → [paths]
    读前 30 行提取 package 声明, 拼出 FQN
    """
    fqn_index: dict[str, str] = {}
    simple_index: dict[str, list[str]] = defaultdict(list)

    for path in java_files:
        basename = os.path.basename(path)
        class_name = basename.removesuffix(".java")
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                head = "".join(f.readline() for _ in range(30))
        except Exception:
            continue

        m = _RE_PACKAGE.search(head)
        if m:
            fqn = f"{m.group(1)}.{class_name}"
            fqn_index[fqn] = path
        simple_index[class_name].append(path)

    return fqn_index, simple_index


def _extract_imports(path: str) -> list[str]:
    """提取文件中所有 import 的 FQN (排除 static import 和通配符)"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return []

    imports = []
    for m in _RE_IMPORT.finditer(content):
        fqn = m.group(1)
        # 跳过 static import (上层 regex 已排除 `import static`)
        # 和通配符 import (regex 只匹配 [\w.]+, 不含 *)
        imports.append(fqn)
    return imports


def _resolve_references(
    seed_files: list[str],
    all_java: list[str],
    max_depth: int = 3,
    repo_name: str = "",
) -> tuple[list[str], dict[str, list[str]]]:
    """
    BFS 从种子文件出发, 通过 import + 同 package 类型引用 递归发现引用文件
    JDK/框架类自然被过滤 (repo 里没有对应 .java)

    返回 (all_relevant_files, seed_to_refs_map)
    seed_to_refs_map: 每个种子文件 → 引用的非种子文件列表
    """
    fqn_index, simple_index = _build_class_index(all_java)
    # 反向索引: package → [FQN, ...]
    pkg_index: dict[str, list[str]] = defaultdict(list)
    for fqn in fqn_index:
        pkg = fqn.rsplit(".", 1)[0]
        pkg_index[pkg].append(fqn)

    seed_set = set(seed_files)

    visited: set[str] = set(seed_files)
    frontier: list[str] = list(seed_files)
    seed_refs: dict[str, list[str]] = defaultdict(list)
    ref_to_seeds: dict[str, set[str]] = {}  # ref → 归属的 seed 集合
    depth = 0

    def _resolve_from_file(fpath: str) -> list[str]:
        """从一个文件中发现所有引用的项目内类 (import + 同 package)"""
        resolved_paths: list[str] = []

        try:
            with open(fpath, encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            return resolved_paths

        # 提取类体 (import 区域之后) 中实际使用的 PascalCase 类名
        # 用于过滤未使用的 import, 避免拉入无关 ref
        last_import_end = 0
        for m in _RE_IMPORT.finditer(content):
            last_import_end = m.end()
        body = content[last_import_end:]
        body_types = set(re.findall(r"\b([A-Z][a-zA-Z0-9]+)\b", body))

        # ── 1. import 引用 (跳过类体中未使用的 import) ──
        for imp_fqn in _extract_imports(fpath):
            simple_name = imp_fqn.rsplit(".", 1)[-1]
            if simple_name not in body_types:
                continue
            resolved = fqn_index.get(imp_fqn)
            if not resolved:
                candidates = simple_index.get(simple_name, [])
                if len(candidates) == 1:
                    resolved = candidates[0]
            if resolved:
                resolved_paths.append(resolved)

        # ── 2. 同 package 类型引用 (Java 同包不需要 import) ──
        pkg_match = _RE_PACKAGE.search(content)
        if not pkg_match:
            return resolved_paths
        pkg = pkg_match.group(1)

        for fqn in pkg_index.get(pkg, []):
            class_name = fqn.rsplit(".", 1)[-1]
            if class_name in body_types:
                resolved = fqn_index.get(fqn)
                if resolved and resolved != fpath:
                    resolved_paths.append(resolved)

        return resolved_paths

    while frontier and depth < max_depth:
        next_frontier: list[str] = []
        for fpath in frontier:
            # fpath 归属哪些 seed?
            if fpath in seed_set:
                owner_seeds = {fpath}
            else:
                owner_seeds = ref_to_seeds.get(fpath, set())

            for resolved in _resolve_from_file(fpath):
                if resolved not in visited:
                    visited.add(resolved)
                    next_frontier.append(resolved)
                    ref_to_seeds[resolved] = set(owner_seeds)
                elif resolved not in seed_set and resolved in ref_to_seeds:
                    # 已访问的 ref 被多条 seed 链路发现, 合并归属
                    ref_to_seeds[resolved] |= owner_seeds

        frontier = next_frontier
        depth += 1

    # 构建 seed → refs 映射 (所有层级)
    ref_only = visited - seed_set
    for ref_path, owners in ref_to_seeds.items():
        if ref_path in ref_only:
            for seed in owners:
                seed_refs[seed].append(ref_path)

    all_relevant = list(seed_files) + sorted(ref_only)

    # ── 日志: 引用追踪结果 ──
    if ref_only:
        logger.info("[%s] 引用追踪发现 %d 个依赖文件 (import + 同 package):",
                    repo_name, len(ref_only))
        for f in sorted(ref_only)[:20]:
            logger.info("[%s]   ref: %s", repo_name, os.path.basename(f))
        if len(ref_only) > 20:
            logger.info("[%s]   ... +%d more", repo_name, len(ref_only) - 20)

    return all_relevant, dict(seed_refs)


# ════════════════════════════════════════════
#  内部: LLM 批量解析
# ════════════════════════════════════════════

def _read_file_content(path: str, slim: bool = False) -> str:
    """读取文件, 可选瘦身 (剥离方法体), 超长截断"""
    with open(path, encoding="utf-8", errors="ignore") as f:
        content = f.read()
    if slim and len(content) > _SLIM_THRESHOLD:
        content = _slim_java_content(content)
    if len(content) > _MAX_FILE_CHARS:
        content = content[:_MAX_FILE_CHARS] + "\n// ... 文件已截断"
    return content


_SLIM_THRESHOLD = settings.code_parse_slim_threshold  # ref 文件超过此字符量时剥离方法体


def _slim_java_content(content: str) -> str:
    """
    剥离 Java 方法体, 只保留 schema 相关信息:
    - package / import
    - 类/枚举/接口声明行
    - 字段声明 + 上方 JavaDoc / 注解
    - 内部类 (static class) 的声明和字段
    方法体 (构造函数、普通方法) 的 { ... } 整体替换为注释.
    """
    lines = content.split("\n")
    out: list[str] = []
    i = 0
    # 跟踪类层级的大括号深度 (0 = 文件顶层)
    brace_depth = 0
    skip_until_depth: int | None = None  # 正在跳过方法体, 等待回到此深度

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── 正在跳过方法体 ──
        if skip_until_depth is not None:
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= skip_until_depth:
                out.append(f"{'    ' * (skip_until_depth + 1)}// ... 方法体已省略")
                out.append(line)  # 闭合的 } 行
                skip_until_depth = None
            i += 1
            continue

        # ── 更新大括号深度 (在判断前) ──
        opens = stripped.count("{")
        closes = stripped.count("}")

        # ── 判断是否是方法签名 ──
        # 方法: 含 ( 且不以 ; 结尾 (排除字段声明和 import)
        # 排除: class/interface/enum 声明, 注解, 注释行
        is_method = False
        if "(" in stripped and not stripped.endswith(";"):
            # 排除类声明
            if not re.match(r".*\b(class|interface|enum)\s+\w+", stripped):
                # 排除注解 (如 @Document("xxx"))
                if not stripped.startswith("@"):
                    # 排除注释行 (JavaDoc 中的 @see Xxx#method())
                    if not stripped.startswith("*") and not stripped.startswith("//") and not stripped.startswith("/*"):
                        is_method = True

        if is_method and opens > 0:
            # 方法签名 + { 在同一行, 开始跳过
            target_depth = brace_depth  # 方法体结束后应回到的深度
            brace_depth += opens - closes
            skip_until_depth = target_depth
            i += 1
            continue
        elif is_method and opens == 0:
            # 方法签名, { 在下一行
            # 跳过签名行, 找到 { 开始跳过
            j = i + 1
            while j < len(lines) and "{" not in lines[j]:
                j += 1
            if j < len(lines):
                target_depth = brace_depth
                brace_depth += lines[j].count("{") - lines[j].count("}")
                skip_until_depth = target_depth
                i = j + 1
                continue
            # 找不到 { (抽象方法等), 保留原行
            out.append(line)
            i += 1
            continue

        # ── 普通行: 保留 ──
        out.append(line)
        brace_depth += opens - closes
        i += 1

    return "\n".join(out)


def _make_batches(
    files: list[str], ref_set: set[str] | None = None,
) -> list[list[tuple[str, str, bool]]]:
    """
    按字符量分批: 每批累计 ≤ _BATCH_CHAR_LIMIT
    每个元素为 (file_path, content, is_ref) 元组
    is_ref=True 的文件是引用上下文, 超限时优先截断
    """
    ref_set = ref_set or set()
    batches: list[list[tuple[str, str, bool]]] = []
    current_batch: list[tuple[str, str, bool]] = []
    current_size = 0

    for path in files:
        content = _read_file_content(path)
        is_ref = path in ref_set
        file_size = len(content)

        # 累计超限, 切批
        if current_batch and current_size + file_size > _BATCH_CHAR_LIMIT:
            batches.append(current_batch)
            current_batch = []
            current_size = 0

        current_batch.append((path, content, is_ref))
        current_size += file_size

    if current_batch:
        batches.append(current_batch)

    return batches


# ════════════════════════════════════════════
#  亲和分批 — 种子和其 direct refs 同 batch (修复 sub_fields 跨文件丢失)
# ════════════════════════════════════════════

def _make_affinity_batches(
    seed_files: list[str],
    ref_map: dict[str, list[str]],
    all_ref_files: set[str],
    *,
    batch_char_limit: int = _BATCH_CHAR_LIMIT,
    max_cont_per_seed: int = _MAX_CONT_BATCHES_PER_SEED,
) -> list[list[tuple[str, str, bool]]]:
    """
    种子亲和分批 — 保证 seed 的 direct refs 与 seed 同 batch.

    算法:
      1. 每个 seed 开一个 batch (seed 必入)
      2. 该 seed 的 direct refs 按文件大小升序贪心塞入 (小的先, 提高填充率)
      3. 塞不下时关闭当前 batch, 开同 seed 续批 (seed 重复出现)
      4. 续批超过 max_cont_per_seed, 余下 refs 降级到 orphan
      5. orphan refs (未被任何 seed 携带的 ref) 走原 _make_batches 贪心装箱

    不变量: 任何 seed 的任何 direct ref, 至少在该 seed 的某个 batch 中出现一次.

    参数 batch_char_limit / max_cont_per_seed 显式暴露便于测试时压缩验证拆分逻辑.
    """
    content_cache: dict[tuple[str, bool], str] = {}

    def _load(p: str, slim: bool = False) -> str:
        key = (p, slim)
        if key not in content_cache:
            content_cache[key] = _read_file_content(p, slim=slim)
        return content_cache[key]

    batches: list[list[tuple[str, str, bool]]] = []
    refs_covered: set[str] = set()

    for seed in seed_files:
        seed_content = _load(seed)
        seed_size = len(seed_content)
        # 小 ref 优先, 最大化同 batch 塞入数
        direct_refs = sorted(
            ref_map.get(seed, []),
            key=lambda p: len(_load(p, slim=True)),
        )

        current: list[tuple[str, str, bool]] = [(seed, seed_content, False)]
        current_size = seed_size
        cont_count = 0

        for ref in direct_refs:
            ref_content = _load(ref, slim=True)
            ref_size = len(ref_content)
            # ref 单文件就超限 → 单独成批 (跟 seed 同批) 仍不可能, 放入 orphan
            if seed_size + ref_size > batch_char_limit:
                continue

            if current_size + ref_size > batch_char_limit:
                batches.append(current)
                if cont_count >= max_cont_per_seed:
                    # 续批上限到了, 不再开新续批, 余下 ref 降级 orphan
                    current = []
                    break
                cont_count += 1
                # 开续批: seed 再放一次, 保证后续 ref 仍能锚定到 seed
                current = [(seed, seed_content, False)]
                current_size = seed_size

            current.append((ref, ref_content, True))
            current_size += ref_size
            refs_covered.add(ref)

        if current:
            batches.append(current)

    # 收尾: orphan refs 独立装箱
    orphan = [r for r in all_ref_files if r not in refs_covered]
    if orphan:
        orphan_batch: list[tuple[str, str, bool]] = []
        orphan_size = 0
        for ref in orphan:
            c = _load(ref, slim=True)
            cs = len(c)
            if orphan_batch and orphan_size + cs > batch_char_limit:
                batches.append(orphan_batch)
                orphan_batch = []
                orphan_size = 0
            orphan_batch.append((ref, c, True))
            orphan_size += cs
        if orphan_batch:
            batches.append(orphan_batch)

    return batches


_JAVA_SYSTEM_PROMPT = """你是 Java 代码分析专家。分析以下 Java 源码文件, 提取数据库相关定义。

对每个文件, 识别:
1. JPA 实体 (@Entity/@Table): 表名、列定义(名称/类型/注释)、关联关系(@ManyToOne/OneToMany等)
2. MongoDB 文档 (@Document): 集合名、字段定义(@Field 映射)
3. 字段业务含义: 从注释、命名、枚举值中推断

【关键约束】mongo_docs 的收录规则:
- 有 @Document(collection="xxx") → collection 用注解值
- 有 @Document 但无 collection 参数 → collection 用类名的蛇形命名 (如 OrderEntity → order_entity)
- 无 @Document 注解的类, 若在【外部 collection 映射】中出现 → 使用映射值, 不要自己猜测
- 无 @Document 注解且不在【外部 collection 映射】中 → 不要输出到 mongo_docs
  (此类通常为纯 DTO/Request/Response/VO, 即便文件中提到某 collection 字符串也不要收录)
- collection 字段必须是 MongoDB 中的真实集合名, 不要用 Java 类名作为 collection

返回严格 JSON (不要包含 markdown 代码块标记):
{
  "entities": [
    {
      "class_name": "User",
      "table": "users",
      "columns": [
        {"name": "id", "type": "Long", "column": "id"},
        {"name": "status", "type": "Integer", "column": "status", "enum_class_hint": "OrderStatus"}
      ],
      "relations": [{"type": "ManyToOne", "target": "Department", "field": "dept"}],
      "file": "User.java"
    }
  ],
  "mongo_docs": [
    {
      "class_name": "Order",
      "collection": "orders",
      "description": "订单记录",
      "purpose_detail": "存储每笔订单的完整生命周期, 包括下单、支付、发货及状态流转",
      "fields": [
        {"field": "orderNo", "type": "String", "description": "订单号"},
        {"field": "status", "type": "OrderStatus", "description": "订单状态", "enum_class_hint": "OrderStatus"},
        {"field": "address", "type": "Address", "description": "收货地址", "sub_fields": [
          {"field": "city", "type": "String", "description": "城市"},
          {"field": "contact", "type": "Contact", "sub_fields": [
            {"field": "phone", "type": "String"},
            {"field": "name", "type": "String"}
          ]}
        ]}
      ],
      "file": "Order.java"
    }
  ]
}

【重要】mongo_docs 每个对象必须包含 "class_name"、"collection"、"fields",
语义字段 "description" 和 "purpose_detail" 的提取规则:
- "description" (集合一句话用途, 10-20 字):
  * 类上有 JavaDoc (如 /** @des: 订单表 */) → 必须填, 抽取原文关键语义
  * 类上无 JavaDoc 但类名语义明确 (如 UserEntity → 用户信息) → 填推断
  * 完全无依据 → 省略
- "purpose_detail" (集合详细业务语义, 30-80 字):
  * 类 JavaDoc 含多行描述 / DAO 有调用说明 → 必须填, 汇总字段组合和业务场景
  * 仅字段能推断整体业务 (如 orderId + paymentTime + status 组合) → 填推断
  * 完全无依据 → 省略

mongo_docs.fields 每个对象必须包含 "field" 和 "type",
可选 "sub_fields" 和 "description":
- "field": MongoDB 中的实际字段名 (有 @Field 注解用注解值, 否则用 Java 属性名)
- "type": Java 类型名 (泛型保留外层, 如 List<Tag> 写 "List<Tag>")
- "sub_fields": 可选, 嵌套类型内部字段数组, 格式同 fields (可递归)
- "description" (字段业务含义, 10-30 字) 提取规则:
  * 字段上方有 JavaDoc 块 (/** xxx */) → 必须填, 抽取原文 (如 "订单编号")
  * 字段后有行尾注释 (// xxx) → 必须填, 抽取原文
  * 枚举常量名语义明确 (如 PENDING/ACTIVE/CLOSED) → 可填 "待处理/活跃/已关闭"
  * 字段名高度自解释 (如 id, createdAt) → 可省略
  * 其他情况 → 省略, 禁止编造
  嵌套类型的 sub_fields 也按同样规则独立提取 description.
禁止使用 "name" 代替 "field", 禁止省略 "field" 或 "type"。

【嵌套类型展开】当字段类型是同文件/同项目内定义的内部类(static class)、嵌入对象、或 DTO 时:
- 在该 field 对象中增加 "sub_fields" 数组
- sub_fields 内的字段若仍为嵌套类型, 继续递归展开直到叶子节点为基本类型
- 循环引用(A 引用 B, B 又引用 A)时, 第二次遇到已展开的类型直接写类型名, 不再展开
- JDK 标准类型(String, Integer, List, Map, Date 等)和第三方框架类型不展开
- 泛型容器(List<Tag>, Set<Option>)展开内部元素类型的字段, type 保留完整泛型写法
- **重要**: 若类型定义在 [参考上下文] 文件中 (例如 List<BaseOrderInfo>, BaseOrderInfo 在另一个 [参考上下文] 文件), 也必须展开 sub_fields, 与同文件内嵌类同等待遇.
- **重要**: 同一类型在多个字段被引用 (如 content:Content 和 subContents:List<Content>), 每个字段都要独立展开 sub_fields, 不要因为前面展开过就省略.

【枚举类关联推断 enum_class_hint (可选字段)】
当字段类型为基础类型 (Integer / Long / String / Short 等), 但 Javadoc / 行尾注释直接命名了
某 enum 类时, 输出 "enum_class_hint" 为该 enum 类名 (simple name).

触发特征 (任一命中即输出):
1. 字段 Javadoc 或行尾注释含 "@see XxxEnum" / "取值见 XxxEnum"
   / "参考 XxxStatus" / "对应 XxxType" — enum 类名直接来自注释文本
2. 字段类型本身就是 enum 类 (如 type: "OrderStatus") → enum_class_hint 必填且等于 type

填写规则:
- 输出 simple name (类名), 不带包路径
- 注释提到的 enum 类是否存在于 [参考上下文] 不影响输出 (后端兜底匹配, LLM 无须验证)
- 无任一触发特征 → 省略 enum_class_hint 字段, 禁止凭字段名硬猜

通用示例:

输入 (注释直引):
  /** 订单状态, @see OrderStatus */
  private Integer status;
输出: {"field": "status", "type": "Integer", "description": "订单状态", "enum_class_hint": "OrderStatus"}

输入 (强类型枚举, 字段类型即 enum 类):
  private OrderStatus status;
输出: {"field": "status", "type": "OrderStatus", "description": "订单状态", "enum_class_hint": "OrderStatus"}

输入 (无注释提示, 普通基础类型字段):
  private Integer paymentStatus;
输出: {"field": "paymentStatus", "type": "Integer"}

输入 (字段名通用, 无类型线索):
  private Integer count;
输出: {"field": "count", "type": "Integer"}

【字段排除】以下字段不要输出:
- @Transient 或 Java transient 修饰的字段 (不存储在 MongoDB 中)
- @BsonIgnore 标记的字段
- 纯计算属性 (无对应存储字段)

如果文件不包含任何数据库实体定义, 返回空数组。

标记为 [参考上下文] 的文件是实体的依赖类型 (枚举、嵌入对象、DTO 等).
不要为参考上下文文件生成独立的 entity/mongo_doc 条目,
但当主文件的字段类型引用了这些参考类时, 必须把参考类的字段结构展开为该字段的 sub_fields,
同时也提取语义信息 (如枚举值含义、嵌入对象字段) 补充到引用该类型的实体描述中."""

_XML_SYSTEM_PROMPT = """你是 MyBatis XML 分析专家。分析以下 MyBatis mapper XML 文件, 提取 SQL 映射。

对每个文件, 识别:
1. namespace (mapper 接口全限定名)
2. 所有 SQL 语句 (select/insert/update/delete)
3. 每条 SQL 引用的表名
4. 动态 SQL 标签(if/choose/foreach)展开为最完整形态
5. WHERE 子句中的字面量值 (区分字面量 vs 占位符)
6. 动态 SQL 的全分支枚举 (每个条件组合渲染为完整 SQL)

返回严格 JSON (不要包含 markdown 代码块标记):
{
  "mybatis_entries": [
    {
      "id": "selectUserById",
      "type": "select",
      "sql": "SELECT id, name FROM user WHERE id = :id",
      "tables": ["user"],
      "file": "UserMapper.xml",
      "where_evidence": [
        {
          "table": "user",
          "column": "status",
          "observed_db_values": [1, 2],
          "observed_string_values": [],
          "occurrence_count": 3,
          "method_ids": ["selectByStatus", "selectActive"]
        }
      ],
      "dynamic_branches": [
        {
          "sql": "SELECT id, name FROM user WHERE status = ? AND age > ?",
          "branch_conditions": ["status 非空", "age 非空"],
          "nl_hint": "按状态和年龄筛选用户"
        }
      ]
    }
  ]
}

【WHERE 字面量识别规则】
- 字面量: status = 1, type = 'PAID', is_deleted = 0 → 收集到 observed_db_values 或 observed_string_values
- 占位符: status = #{status}, id = ${id} → 不收集
- IN 列表中的字面量: status IN (1, 2, 3) → 收集 [1, 2, 3]
- BETWEEN 字面量: amount BETWEEN 100 AND 500 → 收集 [100, 500]
- 同一 (table, column) 在多条 SQL 中出现时, 合并到一条 where_evidence, occurrence_count 累加

【动态分支枚举规则】
- 对含 <if>/<choose>/<foreach> 的 <select>, 枚举主要条件组合
- 每个组合渲染为完整 SQL (动态参数用 ? 替代)
- 上限 32 个分支; 超过时选最有业务代表性的组合
- branch_conditions 用中文描述该分支的触发条件
- nl_hint 用一句中文描述该分支的查询意图
- 无动态标签的 SQL, dynamic_branches 为空数组
- where_evidence 和 dynamic_branches 均可为空数组 (无字面量/无动态标签时)"""


def _validate_mongo_fields(fields: list[dict], path: str, depth: int = 0) -> list[str]:
    """递归校验 mongo field 结构, depth 上限防御循环引用"""
    errors = []
    for j, f in enumerate(fields):
        fp = f"{path}[{j}]"
        if "field" not in f:
            errors.append(f"{fp} 缺少 'field' (有 keys: {list(f.keys())})")
        if "type" not in f:
            errors.append(f"{fp} 缺少 'type'")
        ev = f.get("enum_values")
        if ev is not None and not isinstance(ev, list):
            errors.append(f"{fp} enum_values 应为数组")
        if depth < 5 and f.get("sub_fields"):
            errors.extend(_validate_mongo_fields(
                f["sub_fields"], f"{fp}.sub_fields", depth + 1
            ))
    return errors


def _validate_mongo_query_patterns(patterns: list[dict]) -> list[str]:
    """校验 mongo_query_patterns 结构, 返回错误描述列表"""
    errors = []
    for i, p in enumerate(patterns):
        for key in ("collection", "method", "pattern_type"):
            if key not in p:
                errors.append(f"mongo_query_patterns[{i}] 缺少 '{key}'")
    return errors


def _validate_mongo_docs(docs: list[dict]) -> list[str]:
    """校验 mongo_docs 结构, 返回错误描述列表"""
    errors = []
    for i, d in enumerate(docs):
        for key in ("class_name", "collection", "fields"):
            if key not in d:
                errors.append(f"mongo_docs[{i}] 缺少 '{key}'")
        errors.extend(_validate_mongo_fields(
            d.get("fields", []), f"mongo_docs[{i}].fields"
        ))
    return errors


# ── sub_fields 缺失告警 (warn, 不 fail) ──
_JDK_LEAF_TYPES = frozenset({
    # 大写 Wrapper
    "String", "Integer", "Long", "Boolean", "Double", "Float", "Short",
    "Byte", "Character", "BigDecimal", "BigInteger",
    # Java 基本类型 (Java 源码原生写法)
    "int", "long", "boolean", "double", "float", "short", "byte", "char",
    # 时间/标识
    "Date", "LocalDate", "LocalDateTime", "LocalTime", "Instant",
    "Object", "ObjectId", "UUID",
})
# 容器泛型若内层是 leaf 则不需展开; 这里用单独 regex 匹配
_JDK_CONTAINER_LEAF_RE = re.compile(
    r"^(?:List|Set|Collection|ArrayList|LinkedList|HashSet)<\s*(\w+)\s*>$"
)
_JDK_MAP_RE = re.compile(
    r"^(?:Map|HashMap|LinkedHashMap|TreeMap|ConcurrentHashMap)<"
)
# 框架类型 — 无法展开, 算正常叶子
_FRAMEWORK_OPAQUE = frozenset({"DBRef", "JSONObject", "JSONArray", "Document", "BsonDocument"})


def _is_leaf_type(type_str: str) -> bool:
    """type 是基本/容器-叶子/框架类型 → 无需展开"""
    t = type_str.strip()
    if not t or t in _JDK_LEAF_TYPES or t in _FRAMEWORK_OPAQUE:
        return True
    m = _JDK_CONTAINER_LEAF_RE.match(t)
    if m and m.group(1) in _JDK_LEAF_TYPES | _FRAMEWORK_OPAQUE:
        return True
    if _JDK_MAP_RE.match(t):
        return True
    return False


def _warn_missing_subfields(fields: list[dict], path: str = "") -> list[str]:
    """递归找出疑似未展开 sub_fields 的字段 (非致命, 返回告警列表)"""
    warns: list[str] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = f.get("field", "?")
        t = f.get("type", "")
        fp = f"{path}.{name}" if path else name
        has_sub = bool(f.get("sub_fields"))
        has_enum = bool(f.get("enum_values"))

        if t and not _is_leaf_type(t) and not has_sub and not has_enum:
            warns.append(f"{fp} type={t} 疑似未展开 sub_fields")
        if has_sub:
            warns.extend(_warn_missing_subfields(f["sub_fields"], fp))
    return warns


def _backfill_sub_fields(docs: list[dict]) -> int:
    """
    后置补全: 从 LLM 输出的 mongo_docs 中收集已展开的类型模板,
    填充到同类型但缺失 sub_fields 的位置.
    返回补全的字段数.
    """
    import copy

    # 提取泛型内层类型名 (List<AudioInfo> → AudioInfo, AudioInfo → AudioInfo)
    def _type_key(type_str: str) -> str:
        m = re.search(r"<(\w+)>", type_str)
        return m.group(1) if m else type_str

    # 第一遍: 收集所有已展开的 type → sub_fields 模板
    templates: dict[str, list[dict]] = {}

    def _collect(fields: list[dict]):
        for f in fields:
            t = f.get("type", "")
            subs = f.get("sub_fields")
            if subs:
                key = _type_key(t)
                # 保留字段数最多的模板 (更完整)
                if key not in templates or len(subs) > len(templates[key]):
                    templates[key] = subs
                _collect(subs)

    for doc in docs:
        _collect(doc.get("fields", []))

    if not templates:
        return 0

    # 第二遍: 填充缺失的 sub_fields
    filled = 0

    def _fill(fields: list[dict], _ancestors: frozenset = frozenset()):
        """
        _ancestors: 当前递归路径上已展开过的 type key.
        防止自引用类型 (如 TreeNode 的 children: List<TreeNode>) 无限展开:
        同一个类型在自己的祖先链上只展开一次.
        """
        nonlocal filled
        for f in fields:
            t = f.get("type", "")
            key = _type_key(t) if t else ""
            if not f.get("sub_fields") and not f.get("enum_values") and t:
                # 仅当 key 不在祖先链上时才展开 (切断自引用循环)
                if key in templates and key not in _ancestors:
                    f["sub_fields"] = copy.deepcopy(templates[key])
                    filled += 1
            if f.get("sub_fields"):
                new_ancestors = _ancestors | {key} if key else _ancestors
                _fill(f["sub_fields"], new_ancestors)

    for doc in docs:
        _fill(doc.get("fields", []))

    return filled


# ════════════════════════════════════════════════════════════════
#  多轮分层展开 — 复杂 Entity 504 治理 (2026-05-23 spec)
# ════════════════════════════════════════════════════════════════


def _extract_class_block(content: str, start: int) -> str:
    """从 '{' 位置开始, 用花括号配对找到匹配的 '}', 返回类体内容."""
    depth = 0
    for i in range(start, len(content)):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start + 1:i]
    return content[start + 1:]


@dataclass(frozen=True)
class BatchComplexity:
    file_score: dict[str, float] = field(default_factory=dict)
    total: float = 0.0

    @property
    def is_complex(self) -> bool:
        return self.total > settings.code_parse_complex_threshold


def _extract_inner_type(java_type: str) -> str:
    """List<Foo> → Foo; Map<String, Bar> → Bar; Foo[] → Foo; Foo → Foo."""
    t = java_type.strip()
    if "<" in t:
        inner = t[t.index("<") + 1:t.rindex(">")]
        if "," in inner:
            inner = inner.rsplit(",", 1)[1]
        return _extract_inner_type(inner.strip())
    if t.endswith("[]"):
        return t[:-2].strip()
    return t


def _estimate_file_complexity(content: str) -> float:
    """单文件复杂度: 1.0 + 0.5*static_class + 0.1*non_leaf_field + 0.5*max_depth."""
    static_count = len(_RE_STATIC_CLASS.findall(content))

    # 找出 inner class 的 span, 用于区分顶层字段和内部类字段
    inner_class_spans: list[tuple[str, int, int]] = []
    for m in _RE_STATIC_CLASS_BLOCK.finditer(content):
        brace_pos = content.find("{", m.end() - 1)
        if brace_pos < 0:
            continue
        body = _extract_class_block(content, brace_pos)
        inner_class_spans.append((m.group(1), brace_pos + 1, brace_pos + 1 + len(body)))

    def _in_any_inner(pos: int) -> bool:
        return any(s <= pos < e for _name, s, e in inner_class_spans)

    # 顶层非叶子字段
    top_level_types: list[str] = []
    for m in _RE_FIELD_DECL.finditer(content):
        if _in_any_inner(m.start()):
            continue
        top_level_types.append(_extract_inner_type(m.group(1)))

    # 每个 inner class 的字段类型 (用于 BFS 走深)
    type_to_fields: dict[str, list[str]] = {"<root>": []}
    for t in top_level_types:
        if t not in _LEAF_TYPES:
            type_to_fields["<root>"].append(t)
    for inner_name, _s, _e in inner_class_spans:
        body = content[_s:_e]
        node_children: list[str] = []
        for m in _RE_FIELD_DECL.finditer(body):
            outer = _extract_inner_type(m.group(1))
            if outer not in _LEAF_TYPES:
                node_children.append(outer)
        type_to_fields[inner_name] = node_children

    non_leaf = sum(len(v) for v in type_to_fields.values())

    # BFS 计算最大嵌套深度
    inner_classes = set(_RE_STATIC_CLASS.findall(content))
    visited: set[str] = set()
    max_depth = 0
    queue: list[tuple[str, int]] = [("<root>", 0)]
    while queue:
        node, depth = queue.pop(0)
        if depth > max_depth:
            max_depth = depth
        if node in visited:
            continue
        visited.add(node)
        for child in type_to_fields.get(node, []):
            if child in inner_classes and child not in visited:
                queue.append((child, depth + 1))

    return 1.0 + 0.5 * static_count + 0.1 * non_leaf + 0.5 * max_depth


def _estimate_batch_complexity(
    batch: list[tuple[str, str, bool]],
) -> BatchComplexity:
    """batch = [(filepath, content, is_ref), ...]; ref 文件也参与打分."""
    scores: dict[str, float] = {}
    for filepath, content, _is_ref in batch:
        scores[filepath] = _estimate_file_complexity(content)
    return BatchComplexity(file_score=scores, total=sum(scores.values()))


# ── Round 2 输出拼接 ────────────────────────────────────────────────


def _lookup_expanded(
    type_name: str,
    expanded: dict[str, list[dict]],
    batch_classes: set[str] | None = None,
) -> list[dict] | None:
    """按 4 级匹配优先级查 expanded:
       1. 精确命中
       2. 限定名 -> 短名 (Outer.Inner -> Inner)
       3. 短名 -> 单一限定名命中 (target=Inner, key=X.Inner 唯一)
       4. 多个限定名结尾匹配 -> 歧义返回 None
    """
    if type_name in expanded:
        return expanded[type_name]
    if "." in type_name:
        short = type_name.rsplit(".", 1)[1]
        if short in expanded:
            return expanded[short]
    qualified = [k for k in expanded if "." in k and k.rsplit(".", 1)[1] == type_name]
    if len(qualified) == 1:
        return expanded[qualified[0]]
    return None


def _fill_sub_fields(
    fields: list[dict],
    expanded: dict[str, list[dict]],
    *,
    depth: int = 0,
    visited: frozenset[str] = frozenset(),
    batch_classes: set[str] | None = None,
) -> None:
    """就地填充. 深度上限 settings.code_parse_expansion_max_depth, 自引用切断."""
    max_depth = settings.code_parse_expansion_max_depth
    batch_classes = batch_classes or set()

    if depth >= max_depth:
        return

    for f in fields:
        if not f.get("needs_expansion"):
            continue
        if f.get("sub_fields"):
            continue
        type_name = _extract_inner_type(f.get("type", ""))
        if type_name in visited:
            f["needs_expansion"] = True
            continue
        sub = _lookup_expanded(type_name, expanded, batch_classes)
        if sub is None:
            continue
        copied = deepcopy(sub)
        f["sub_fields"] = copied
        del f["needs_expansion"]
        _fill_sub_fields(
            copied,
            expanded,
            depth=depth + 1,
            visited=visited | {type_name},
            batch_classes=batch_classes,
        )


# ── Round 1 / Round 2 LLM 调用 ─────────────────────────────────────


def _build_round_user_content(
    batch: list[tuple[str, str, bool]],
    extra_block: str = "",
) -> str:
    """与单轮路径同形, 仅多注入 extra_block (例如 target_classes 列表)."""
    body = "\n\n".join(
        f"=== 文件: {os.path.basename(p)}"
        f"{' [参考上下文]' if is_ref else ''} ===\n{c}"
        for p, c, is_ref in batch
    )
    return (extra_block + "\n\n" + body) if extra_block else body


def _call_round1_skeleton(
    batch: list[tuple[str, str, bool]],
) -> dict | None:
    """Round 1 调用; None 表示整批失败."""
    from app.knowledge.extraction_prompts import load_prompt_or_fallback

    system = load_prompt_or_fallback("09-java-skeleton-extract")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _build_round_user_content(batch)},
    ]
    try:
        raw = chat_completion(
            messages,
            max_tokens=settings.code_parse_round1_max_tokens,
            temperature=_LLM_TEMPERATURE,
        )
    except Exception as e:
        logger.warning("Round 1 LLM 调用失败: %s", e)
        return None
    data = parse_llm_json(raw)
    if not isinstance(data, dict) or "mongo_docs" not in data:
        logger.error("Round 1 输出不合法 JSON 或缺 mongo_docs 字段")
        return None
    return data


def _call_round2_expand(
    batch: list[tuple[str, str, bool]],
    target_classes: list[str],
    *,
    max_tokens: int | None = None,
) -> dict:
    """Round 2; 抛 TimeoutError / Exception 由调用方重试或降级."""
    from app.knowledge.extraction_prompts import load_prompt_or_fallback

    system = load_prompt_or_fallback("10-java-type-expand")
    extra = "【target_classes】" + ", ".join(target_classes)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _build_round_user_content(batch, extra)},
    ]
    raw = chat_completion(
        messages,
        max_tokens=max_tokens or settings.code_parse_round2_max_tokens,
        temperature=_LLM_TEMPERATURE,
    )
    data = parse_llm_json(raw)
    if not isinstance(data, dict) or "expanded_classes" not in data:
        raise ValueError("Round 2 输出非 JSON 或缺 expanded_classes")
    return data


def _chunked(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _parse_complex_batch_multi_round(
    batch: list[tuple[str, str, bool]],
) -> dict:
    """多轮路径主入口.
    返回: {"mongo_docs": [...], "errored": bool, "partial": bool}
    """
    skeleton = _call_round1_skeleton(batch)
    if skeleton is None:
        return {"mongo_docs": [], "errored": True, "partial": False}

    mongo_docs = skeleton.get("mongo_docs", [])
    types_to_expand = list(dict.fromkeys(skeleton.get("types_to_expand", [])))

    if not types_to_expand:
        return {"mongo_docs": mongo_docs, "errored": False, "partial": False}

    expanded: dict[str, list[dict]] = {}
    partial = False
    chunk_size = settings.code_parse_round2_classes_per_call
    fallback_size = settings.code_parse_round2_classes_per_call_fallback

    for chunk in _chunked(types_to_expand, chunk_size):
        try:
            data = _call_round2_expand(batch, chunk)
        except Exception as e:
            logger.warning(
                "Round 2 chunk %s 首次失败: %s, 降级到 %d 个/批",
                chunk, e, fallback_size,
            )
            sub_chunks = _chunked(chunk, fallback_size)
            chunk_partial = False
            for sub in sub_chunks:
                try:
                    data2 = _call_round2_expand(batch, sub)
                except Exception as e2:
                    logger.error("Round 2 降级 chunk %s 失败: %s", sub, e2)
                    chunk_partial = True
                    continue
                for entry in data2.get("expanded_classes", []):
                    if entry.get("not_found"):
                        continue
                    expanded[entry["class_name"]] = entry.get("fields", [])
            if chunk_partial:
                partial = True
            continue
        for entry in data.get("expanded_classes", []):
            if entry.get("not_found"):
                continue
            expanded[entry["class_name"]] = entry.get("fields", [])

    # 拼接 sub_fields
    batch_classes = {
        os.path.basename(p).removesuffix(".java")
        for p, _, is_ref in batch if not is_ref
    }
    for d in mongo_docs:
        _fill_sub_fields(d.get("fields", []), expanded, batch_classes=batch_classes)

    return {"mongo_docs": mongo_docs, "errored": False, "partial": partial}


def _parse_java_batch(
    files: list[str],
    ref_set: set[str] | None = None,
    ref_map: dict[str, list[str]] | None = None,
    hidden_entity_collections: dict[str, str] | None = None,
    repo_name: str = "",
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """
    批量 LLM 解析 Java 文件
    ref_set 中的文件标记为 [参考上下文], LLM 仅从中提取语义补充
    ref_map 给定时启用亲和分批 (seed 和 direct refs 同 batch)
    hidden_entity_collections: 无 @Document 注解的隐藏 Entity 的 collection 映射,
                               每批前缀注入到 user message, 让 LLM 强制采用而非自己猜
    返回 (jpa_entities, mongo_documents, query_patterns, stats_dict)
    """
    ref_set = ref_set or set()
    hidden_entity_collections = hidden_entity_collections or {}
    entities: list[dict] = []
    mongo_docs: list[dict] = []
    query_patterns: list[dict] = []
    stats = {"parsed": 0, "errored": 0, "errors": []}

    if not files:
        return entities, mongo_docs, query_patterns, stats

    # 有 ref_map 走亲和分批 (保证 seed 与 direct refs 同 batch); 否则回退贪心装箱
    if ref_map:
        seed_files = [f for f in files if f not in ref_set]
        batches = _make_affinity_batches(seed_files, ref_map, ref_set)
        logger.info(
            "[%s] 亲和分批 seeds=%d refs=%d → batches=%d",
            repo_name, len(seed_files), len(ref_set), len(batches),
        )
    else:
        batches = _make_batches(files, ref_set)
    # 批次队列 (不再支持拆分重试 — 拆分会破坏跨文件引用上下文)
    pending: list[list[tuple[str, str, bool]]] = list(batches)
    batch_idx = 0

    while pending:
        batch = pending.pop(0)
        batch_idx += 1
        batch_files = [os.path.basename(p) for p, _, _ in batch]
        logger.info("[%s] Java batch %d files=%s", repo_name, batch_idx, batch_files)

        # ── 复杂度估算: 超阈值走多轮路径 ──
        complexity = _estimate_batch_complexity(batch)
        if complexity.is_complex:
            logger.info(
                "[%s] Java batch %d 复杂度 %.2f > 阈值, 走多轮路径",
                repo_name, batch_idx, complexity.total,
            )
            multi = _parse_complex_batch_multi_round(batch)
            if multi["errored"]:
                stats["errored"] += len(batch)
                for p, _, _ in batch:
                    stats["errors"].append(
                        FileParseResult(
                            file_path=p, status="error",
                            reason="多轮 Round 1 失败",
                        )
                    )
            else:
                mongo_docs.extend(multi["mongo_docs"])
                stats["parsed"] += sum(1 for _, _, is_ref in batch if not is_ref)
                if multi["partial"]:
                    logger.warning(
                        "[%s] Java batch %d 多轮路径部分降级 (Round 2 chunk 失败)",
                        repo_name, batch_idx,
                    )
            continue

        # ── 隐藏 Entity 外部映射注入 ──
        # 仅注入本批 seed 中出现的 hidden entity, 避免 prompt 膨胀
        seed_class_names = {
            os.path.basename(p).removesuffix(".java")
            for p, _, is_ref in batch if not is_ref
        }
        batch_overrides = {
            cls: coll for cls, coll in hidden_entity_collections.items()
            if cls in seed_class_names
        }
        override_block = ""
        if batch_overrides:
            lines = ["【外部 collection 映射】以下类无 @Document 注解,"
                     " 由 DAO 调用提取得到 collection, 请直接采用:"]
            for cls, coll in sorted(batch_overrides.items()):
                lines.append(f"  - {cls} → collection={coll!r}")
            override_block = "\n".join(lines) + "\n\n"

        # 种子文件和引用文件使用不同标记
        user_content = override_block + "\n\n".join(
            f"=== 文件: {os.path.basename(p)}{' [参考上下文]' if is_ref else ''} ===\n{content}"
            for p, content, is_ref in batch
        )
        messages = [
            {"role": "system", "content": _JAVA_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            data = _call_and_validate_java(messages)
            if data:
                entities.extend(data.get("entities", []))
                docs_in_batch = data.get("mongo_docs", [])
                mongo_docs.extend(docs_in_batch)
                # ── sub_fields 缺失告警 (warn-only) ──
                for d in docs_in_batch:
                    warns = _warn_missing_subfields(
                        d.get("fields", []),
                        path=d.get("class_name", "?"),
                    )
                    if warns:
                        logger.warning(
                            "[%s] sub_fields 疑似漏展开 batch=%d class=%s: %s",
                            repo_name, batch_idx, d.get("class_name", "?"), warns,
                        )
                # 提取查询模式 + 校验
                raw_patterns = data.get("mongo_query_patterns", [])
                pattern_errors = _validate_mongo_query_patterns(raw_patterns)
                if pattern_errors:
                    logger.warning("[%s] mongo_query_patterns 校验失败, 忽略: %s",
                                   repo_name, pattern_errors)
                else:
                    query_patterns.extend(raw_patterns)
                stats["parsed"] += sum(1 for _, _, is_ref in batch if not is_ref)
            elif data is None:
                # LLM 输出无法解析 (json_repair + LLM 重试都救不了).
                # 不做 batch 拆分 — 拆分会破坏跨文件引用上下文,
                # 导致 sub_fields 无法展开、交叉引用丢失, 结果不再准确.
                # 整批标记为 errored, 等人工或下次重跑.
                logger.error(
                    "[%s] Java batch 解析失败, 整批标记为 errored (batch_size=%d files=%s)",
                    repo_name, len(batch), batch_files,
                )
                stats["errored"] += len(batch)
                for p, _, _ in batch:
                    stats["errors"].append(
                        FileParseResult(
                            file_path=p, status="error",
                            reason="LLM JSON 解析失败 (json_repair + LLM 重试均无效)"
                        )
                    )
        except Exception as e:
            logger.warning("[%s] Java batch parse failed: %s, files: %s", repo_name, e, batch_files)
            stats["errored"] += len(batch)
            for p, _, _ in batch:
                stats["errors"].append(
                    FileParseResult(file_path=p, status="error", reason=str(e))
                )

    return entities, mongo_docs, query_patterns, stats


def _call_and_validate_java(messages: list[dict], max_retries: int = 1) -> dict | None:
    """
    调用 LLM 解析 Java, 校验 mongo_docs 结构.

    失败兜底链 (代价递增):
    1. json.loads → json_repair 本地修复 (见 _parse_json)
    2. 结构校验失败 → 携带具体错误���息让 LLM 重试
    3. JSON 完全修不好 → 同样让 LLM 重试 (prompt 提示转义规则)
    """
    for attempt in range(1 + max_retries):
        raw = chat_completion(
            messages=messages,
            temperature=_LLM_TEMPERATURE,
            max_tokens=_LLM_MAX_TOKENS,
        )
        data = _parse_json(raw)

        # ── Case A: JSON 语法完全修不好 ──
        if data is None:
            if attempt < max_retries:
                logger.warning("LLM 输出 JSON 语法错误且 json_repair 无法修复, 重试中")
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": (
                        "你上次的输出不是合法 JSON。请严格重新输出:\n"
                        "- 字符串值中的双引号 \" 必须转义为 \\\"\n"
                        "- 反斜杠 \\ 必须转义为 \\\\\n"
                        "- 不要有尾随逗号, 不要用单引号\n"
                        "- 不要在 JSON 外输出任何解释文字"
                    )},
                ]
                continue
            logger.error("LLM JSON 语法错误, 重试后仍无法修复")
            return None

        # ── Case B: JSON 合法但结构校验失败 ──
        validation_errors = _validate_mongo_docs(data.get("mongo_docs", []))
        if not validation_errors:
            return data

        if attempt < max_retries:
            logger.warning("mongo_docs 结构校验失败, 重试中: %s", validation_errors)
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": (
                    "你的输出存在结构错误:\n"
                    + "\n".join(f"- {e}" for e in validation_errors)
                    + "\n\n请严格按照要求重新输出完整 JSON。"
                    "mongo_docs.fields 每个对象必须包含 \"field\" 和 \"type\","
                    " 可选 \"sub_fields\"(嵌套字段)。"
                )},
            ]
        else:
            logger.error("mongo_docs 结构校验重试后仍失败: %s", validation_errors)
            return None

    return None


def _parse_xml_batch(files: list[str], repo_name: str = "") -> tuple[list[dict], dict]:
    """
    批量 LLM 解析 MyBatis XML 文件
    返回 (mybatis_entries, stats_dict)
    """
    entries: list[dict] = []
    stats = {"parsed": 0, "errored": 0, "errors": []}

    if not files:
        return entries, stats

    batches = _make_batches(files)
    pending: list[list[tuple[str, str, bool]]] = list(batches)
    batch_idx = 0

    while pending:
        batch = pending.pop(0)
        batch_idx += 1
        batch_files = [os.path.basename(p) for p, _, _ in batch]
        logger.info("[%s] XML batch %d files=%s", repo_name, batch_idx, batch_files)
        user_content = "\n\n".join(
            f"=== 文件: {os.path.basename(p)} ===\n{content}"
            for p, content, _ in batch
        )
        try:
            raw = chat_completion(
                messages=[
                    {"role": "system", "content": _XML_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=_LLM_TEMPERATURE,
                max_tokens=_LLM_MAX_TOKENS,
            )
            data = _parse_json(raw)
            if data:
                entries.extend(data.get("mybatis_entries", []))
                stats["parsed"] += len(batch)
            elif len(batch) > 1:
                mid = len(batch) // 2
                logger.warning("[%s] XML batch 解析失败, 拆分重试 %d → %d+%d",
                               repo_name, len(batch), mid, len(batch) - mid)
                pending.insert(0, batch[mid:])
                pending.insert(0, batch[:mid])
            else:
                stats["errored"] += 1
                stats["errors"].append(
                    FileParseResult(
                        file_path=batch[0][0], status="error",
                        reason="JSON 解析失败 (拆分后仍无法解析)"
                    )
                )
        except Exception as e:
            logger.warning("[%s] XML batch parse failed: %s, files: %s", repo_name, e, batch_files)
            stats["errored"] += len(batch)
            for p, _, _ in batch:
                stats["errors"].append(
                    FileParseResult(file_path=p, status="error", reason=str(e))
                )

    return entries, stats


# ════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════

def _parse_json(raw: str) -> dict | None:
    """从 LLM 响应中提取 JSON — 委托统一解析器."""
    return parse_llm_json(raw, expect="dict")


# ════════════════════════════════════════════
#  WHERE 字面量证据抽取 (Phase 2 Task 4)
# ════════════════════════════════════════════


async def parse_xml_for_where_evidence(
    xml_text: str, *, mapper_namespace: str,
    db=None, namespace_id: int | None = None,
) -> list[dict[str, Any]]:
    """LLM 抽 WHERE 字面量, 区分字面量 vs 占位符.

    Returns list of where_evidence dicts:
    {table, column, observed_db_values, observed_string_values, occurrence_count, method_ids}
    """
    import asyncio

    from app.knowledge.extraction_prompts import load_prompt_or_fallback
    from app.knowledge.llm_retry import with_retry

    template_body = load_prompt_or_fallback("03-mybatis-where-evidence")
    prompt = template_body.replace(
        "${xml_source}", xml_text
    ).replace(
        "${mapper_namespace}", mapper_namespace
    )

    try:
        raw = await with_retry(
            lambda: asyncio.to_thread(chat_completion, [{"role": "user", "content": prompt}]),
            template_name="mybatis_where_evidence",
            input_meta={"mapper": mapper_namespace},
            prompt_full=prompt,
            extraction_kind="where_evidence",
            db=db,
            namespace_id=namespace_id,
        )
        parsed = parse_llm_json(raw, expect="dict")
        if parsed is None:
            logger.warning("where_evidence JSON 解析失败 mapper=%s", mapper_namespace)
            return []
        return parsed.get("where_evidence", [])
    except Exception as e:
        logger.warning("where_evidence 抽取失败 mapper=%s: %s", mapper_namespace, e)
        return []
