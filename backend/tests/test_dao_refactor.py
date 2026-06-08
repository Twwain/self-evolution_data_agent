"""
DAO 重构回归测试
- regex 覆盖 4 种 mongoTemplate 调用形态
- 隐藏 Entity 发现链路
- _filter_mongo_docs_by_annotation 白名单过滤
- _parse_java_batch 接受 hidden_entity_collections 参数
"""
import os
import tempfile
import textwrap

from app.knowledge.code_parser import (
    _filter_mongo_docs_by_annotation,
    _parse_java_batch,
    _scan_dao_collection_mappings,
    _scan_document_classes,
)


def _write(dirpath: str, name: str, content: str) -> str:
    path = os.path.join(dirpath, name)
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))
    return path


# ════════════════════════════════════════════
#  regex: 四种 mongoTemplate 调用形态
# ════════════════════════════════════════════

def test_scan_dao_mappings_four_call_shapes():
    """
    覆盖:
      1. count(query, Cls.class)                         — 无字符串, 仅抓 class
      2. count(query, Cls.class, "c_xxx")                — 带字符串
      3. find(query.with(pageable), Cls.class, "c_xxx")  — 嵌套方法链仍可识别
      4. save(varName, "c_xxx")                          — 变量名反推
    """
    with tempfile.TemporaryDirectory() as d:
        dao = _write(d, "UserDao.java", """
            public class UserDao {
                public long count() {
                    // 形态 1: 无字符串
                    return mongoTemplate.count(query, UserEntity.class);
                }
                public long countByTag() {
                    // 形态 2: 带字符串
                    return mongoTemplate.count(query, UserEntity.class, "c_user");
                }
                public List find() {
                    // 形态 3: 嵌套调用链
                    return mongoTemplate.find(query.with(pageable), UserEntity.class, "c_user");
                }
                public void save(UserEntity userEntity) {
                    // 形态 4: 变量名反推
                    mongoTemplate.save(userEntity, "c_user");
                }
            }
        """)

        mappings, all_refs = _scan_dao_collection_mappings([dao])
        assert "UserEntity" in all_refs, "形态 1 应使 UserEntity 进 all_refs"
        assert mappings.get("UserEntity") == "c_user", \
            "形态 2/3/4 投票应产生 UserEntity → c_user"


# ════════════════════════════════════════════
#  多 Entity 同 DAO 场景 (哥确认的核心)
# ════════════════════════════════════════════

def test_multi_entity_same_dao():
    """一个 DAO 文件引用 >=2 不同 Entity"""
    with tempfile.TemporaryDirectory() as d:
        dao = _write(d, "MixedDao.java", """
            public class MixedDao {
                public void f1() {
                    mongoTemplate.find(q, ProductEntity.class, "c_product");
                    mongoTemplate.find(q, SkuEntity.class, "c_sku");
                }
            }
        """)
        mappings, all_refs = _scan_dao_collection_mappings([dao])
        assert mappings == {"ProductEntity": "c_product", "SkuEntity": "c_sku"}
        assert all_refs == {"ProductEntity", "SkuEntity"}


# ════════════════════════════════════════════
#  同 class 多 collection — 频次投票保留
# ════════════════════════════════════════════

def test_same_class_multi_collection_voting():
    """ProductEntity × c_product(6) vs c_product_tmp(2) → c_product 胜出"""
    with tempfile.TemporaryDirectory() as d:
        main_dao = _write(d, "ProductDao.java",
            "\n".join(f'mongoTemplate.find(q, ProductEntity.class, "c_product");' for _ in range(6)))
        tmp_dao = _write(d, "ProductDaoTmp.java",
            "\n".join(f'mongoTemplate.find(q, ProductEntity.class, "c_product_tmp");' for _ in range(2)))
        mappings, _ = _scan_dao_collection_mappings([main_dao, tmp_dao])
        assert mappings.get("ProductEntity") == "c_product", \
            "多数派 c_product(6) 应胜出 c_product_tmp(2)"


# ════════════════════════════════════════════
#  @Document 扫描
# ════════════════════════════════════════════

def test_scan_document_classes():
    with tempfile.TemporaryDirectory() as d:
        entity = _write(d, "UserEntity.java", """
            @Document(collection = "c_user")
            @Data
            public class UserEntity {
                private String id;
            }
        """)
        nodoc = _write(d, "PictureEntity.java", """
            @Data
            public class PictureEntity {
                private String id;
            }
        """)
        classes = _scan_document_classes([entity, nodoc])
        assert classes == {"UserEntity"}


# ════════════════════════════════════════════
#  隐藏 Entity 发现 (哥的主打场景)
# ════════════════════════════════════════════

def test_hidden_entity_discovery():
    """无 @Document 的 PictureEntity 被 DAO 引用 + 带字符串 collection
       → 应被识别为隐藏 Entity (all_refs - annotated) ∩ mappings.keys()
    """
    with tempfile.TemporaryDirectory() as d:
        _write(d, "PictureEntity.java", """
            @Data
            public class PictureEntity {
                private String id;
                private String picName;
            }
        """)
        _write(d, "PictureDao.java", """
            public class PictureDao {
                public void f() {
                    mongoTemplate.find(q, PictureEntity.class, "c_picture");
                }
            }
        """)
        import glob as _g
        files = sorted(_g.glob(os.path.join(d, "*.java")))
        annotated = _scan_document_classes(files)
        mappings, all_refs = _scan_dao_collection_mappings(files)

        hidden = {c for c in all_refs - annotated if c in mappings}
        assert hidden == {"PictureEntity"}
        assert mappings["PictureEntity"] == "c_picture"


# ════════════════════════════════════════════
#  白名单过滤 (_filter_mongo_docs_by_annotation 接通)
# ════════════════════════════════════════════

def test_filter_mongo_docs_by_whitelist():
    """LLM 幻觉输出 3 个 mongo_docs, 仅 2 个在白名单内"""
    docs = [
        {"class_name": "UserEntity", "collection": "c_user"},
        {"class_name": "PictureEntity", "collection": "c_picture"},
        {"class_name": "FakeRequestDto", "collection": "c_fake"},  # LLM 幻觉
    ]
    allowed = {"UserEntity", "PictureEntity"}
    kept, dropped = _filter_mongo_docs_by_annotation(docs, allowed)
    assert {d["class_name"] for d in kept} == {"UserEntity", "PictureEntity"}
    assert {d["class_name"] for d in dropped} == {"FakeRequestDto"}


# ════════════════════════════════════════════
#  _parse_java_batch 接受 hidden_entity_collections 参数
# ════════════════════════════════════════════

def test_parse_java_batch_accepts_hidden_param():
    """参数存在且空列表走短路路径"""
    entities, mongo_docs, patterns, stats = _parse_java_batch(
        files=[],
        hidden_entity_collections={"PictureEntity": "c_picture"},
    )
    assert entities == [] and mongo_docs == [] and patterns == []
    assert stats["parsed"] == 0


# ════════════════════════════════════════════
#  Object / 通用变量黑名单
# ════════════════════════════════════════════

def test_generic_variable_blacklist():
    """Object.class / obj / entity 等不应被当成 Entity 类名"""
    with tempfile.TemporaryDirectory() as d:
        dao = _write(d, "BadDao.java", """
            public class BadDao {
                public void f() {
                    mongoTemplate.find(q, Object.class, "c_junk");
                    mongoTemplate.save(obj, "c_junk");
                    mongoTemplate.save(entity, "c_junk");
                }
            }
        """)
        mappings, all_refs = _scan_dao_collection_mappings([dao])
        assert "Object" not in all_refs and "Object" not in mappings
        assert "Obj" not in mappings and "Entity" not in mappings
