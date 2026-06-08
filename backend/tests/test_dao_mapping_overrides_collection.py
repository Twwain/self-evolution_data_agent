"""
集合名修正 (DAO mapping override) 行为固化测试 — 复现 c_product 字段缺失的真实场景

业务场景:
- Java 类 ProductEntity 标注 @Document("c_product")
- DAO 实际调用: mongoTemplate.find(..., ProductEntity.class, "c_product_tmp")
- Spring MongoTemplate 运行时行为: 第三参 collectionName 优先级 > @Document 注解
- 因此 _scan_dao_collection_mappings 扫出 ProductEntity → c_product_tmp 是正确的

预期行为:
- code_parser 应当用 DAO 映射覆盖 LLM 输出, 把 ProductEntity 字段写到 c_product_tmp
- 同时 ProductIdEntity (无 DAO 映射) 仍走 @Document("c_product") 注解, 写到 c_product
- 结果: c_product 只剩 ProductIdEntity 的字段, c_product_tmp 拿到 ProductEntity 完整 schema

这不是 bug, 是代码-数据真相对齐. 但产生了 "MongoDB 里 c_product 集合存在 + 代码 c_product canonical 字段不全" 的副作用.

本测试固化此行为, 任何修改 _scan_dao_collection_mappings 或 集合名修正逻辑都会触发回归提醒.
"""

from unittest.mock import patch

from app.knowledge.code_parser import _scan_dao_collection_mappings


def _write_java(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_dao_mapping_overrides_document_annotation(tmp_path):
    """
    复现 products-service c_product 场景:
      ProductEntity @Document("c_product") + DAO 用 c_product_tmp → 修正后写到 c_product_tmp
      ProductIdEntity @Document("c_product") + 无 DAO → 保留 c_product
    """
    # ── 1. 构造两个 Entity 类 + 一个 DAO 实现 ──
    entity_dir = tmp_path / "entity"
    dao_dir = tmp_path / "dao"

    _write_java(entity_dir / "ProductEntity.java", """
@Document("c_product")
public class ProductEntity extends BaseEntity {
    @Id
    private String id;
    private String docId;
    private String name;
}
""")

    _write_java(entity_dir / "ProductIdEntity.java", """
@Document("c_product")
public class ProductIdEntity {
    private String id;
}
""")

    _write_java(dao_dir / "ProductDaoImpl.java", """
@Repository
public class ProductDaoImpl {
    public ProductEntity findOne(String id) {
        Query q = new Query(Criteria.where("_id").is(id));
        return mongoTemplate.findOne(q, ProductEntity.class, "c_product_tmp");
    }

    public List<ProductEntity> list() {
        return mongoTemplate.find(new Query(), ProductEntity.class, "c_product_tmp");
    }
}
""")

    java_files = [
        str(entity_dir / "ProductEntity.java"),
        str(entity_dir / "ProductIdEntity.java"),
        str(dao_dir / "ProductDaoImpl.java"),
    ]

    # ── 2. _scan_dao_collection_mappings 应抓到 ProductEntity → c_product_tmp ──
    mapping, _ = _scan_dao_collection_mappings(java_files)
    assert mapping.get("ProductEntity") == "c_product_tmp", \
        f"DAO 扫描应识别 ProductEntity → c_product_tmp, 实际: {mapping}"
    # ProductIdEntity 没有 DAO 调用, 不在 mapping 里
    assert "ProductIdEntity" not in mapping


def test_collection_name_correction_logic():
    """
    单独验证 code_parser.py:87-99 的修正分支:
    - LLM 输出 mongo_doc.collection = "c_product" (来自 @Document 注解)
    - dao_class_to_coll = {"ProductEntity": "c_product_tmp"}
    - 修正后 mongo_doc.collection 应变为 "c_product_tmp"
    """
    # 直接模拟那段逻辑
    mongo_docs = [
        {"class_name": "ProductEntity", "collection": "c_product", "fields": [
            {"field": "id", "type": "String"},
            {"field": "docId", "type": "String"},
        ]},
        {"class_name": "ProductIdEntity", "collection": "c_product", "fields": [
            {"field": "id", "type": "String"},
        ]},
    ]
    dao_class_to_coll = {"ProductEntity": "c_product_tmp"}

    # 复现修正逻辑
    for doc in mongo_docs:
        cls = doc.get("class_name", "")
        coll = doc.get("collection", "")
        real_coll = dao_class_to_coll.get(cls)
        if real_coll and real_coll != coll:
            doc["collection"] = real_coll

    # ── 验证修正结果 ──
    product_entity_doc = next(d for d in mongo_docs if d["class_name"] == "ProductEntity")
    product_id_doc = next(d for d in mongo_docs if d["class_name"] == "ProductIdEntity")

    assert product_entity_doc["collection"] == "c_product_tmp", \
        "ProductEntity 应被 DAO 映射覆盖到 c_product_tmp"
    assert product_id_doc["collection"] == "c_product", \
        "ProductIdEntity 无 DAO, 保留 @Document 注解的 c_product"

    # ── 关键副作用: c_product 收集到的字段只剩 ProductIdEntity 的 ──
    c_product_fields = sum(
        len(d["fields"]) for d in mongo_docs if d["collection"] == "c_product"
    )
    c_product_tmp_fields = sum(
        len(d["fields"]) for d in mongo_docs if d["collection"] == "c_product_tmp"
    )
    assert c_product_fields == 1, f"c_product 应只剩 1 字段, 实际 {c_product_fields}"
    assert c_product_tmp_fields == 2, f"c_product_tmp 应有 2 字段, 实际 {c_product_tmp_fields}"


def test_full_pipeline_with_dao_override(tmp_path, monkeypatch):
    """
    端到端: parse_repository 实际跑一遍, 验证 mongo_docs 修正
    用 mock LLM 返回固定结构, 不依赖真实 LLM 调用
    """
    from app.knowledge import code_parser

    # ── 构造 mini repo ──
    src = tmp_path / "src/main/java/com/x"
    _write_java(src / "ProductEntity.java", """package com.x;
import org.springframework.data.mongodb.core.mapping.Document;
@Document("c_product")
public class ProductEntity {
    private String id;
    private String name;
}
""")
    _write_java(src / "ProductIdEntity.java", """package com.x;
import org.springframework.data.mongodb.core.mapping.Document;
@Document("c_product")
public class ProductIdEntity {
    private String id;
}
""")
    _write_java(src / "ProductDaoImpl.java", """package com.x;
public class ProductDaoImpl {
    public void run() {
        mongoTemplate.find(query, ProductEntity.class, "c_product_tmp");
    }
}
""")

    # ── mock LLM: 返回 LLM 解析的两个 mongo_doc, collection 都是 c_product ──
    fake_llm_response = """{
        "entities": [],
        "mongo_docs": [
            {"class_name": "ProductEntity", "collection": "c_product",
             "fields": [{"field": "id", "type": "String"}, {"field": "name", "type": "String"}],
             "file": "ProductEntity.java"},
            {"class_name": "ProductIdEntity", "collection": "c_product",
             "fields": [{"field": "id", "type": "String"}],
             "file": "ProductIdEntity.java"}
        ],
        "mongo_query_patterns": []
    }"""

    with patch("app.knowledge.code_parser.chat_completion", return_value=fake_llm_response):
        result, stats = code_parser.parse_repository(str(tmp_path))

    # ── 验证 ──
    by_coll: dict[str, list[dict]] = {}
    for d in result.mongo_documents:
        by_coll.setdefault(d["collection"], []).append(d)

    # ProductEntity 已被 DAO 映射重定向到 c_product_tmp
    assert "c_product_tmp" in by_coll, f"修正后应有 c_product_tmp, 实际 keys: {list(by_coll.keys())}"
    assert any(d["class_name"] == "ProductEntity" for d in by_coll["c_product_tmp"]), \
        "ProductEntity 应被重定向到 c_product_tmp"

    # c_product 只剩 ProductIdEntity (无 DAO 覆盖)
    assert "c_product" in by_coll
    assert all(d["class_name"] == "ProductIdEntity" for d in by_coll["c_product"]), \
        "c_product 应只剩 ProductIdEntity"
    c_product_field_count = sum(len(d["fields"]) for d in by_coll["c_product"])
    assert c_product_field_count == 1, \
        f"c_product 应只剩 1 字段 (ProductIdEntity 的 id), 实际 {c_product_field_count}"
