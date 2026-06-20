"""验证 db_types 常量从 DRIVERS 注册表正确派生."""


def test_paradigm_map_from_drivers():
    """PARADIGM_MAP 每个 entry 对应 DRIVERS 中一个已注册 driver."""
    from app.engine.db_types import PARADIGM_MAP
    from app.engine.drivers import DRIVERS

    assert len(PARADIGM_MAP) == len(DRIVERS), (
        f"PARADIGM_MAP ({len(PARADIGM_MAP)}) 与 DRIVERS ({len(DRIVERS)}) 数量不一致"
    )
    for db_type, driver in DRIVERS.items():
        assert PARADIGM_MAP[db_type] == driver.paradigm, (
            f"PARADIGM_MAP[{db_type!r}]={PARADIGM_MAP[db_type]!r} "
            f"!= driver.{db_type}.paradigm={driver.paradigm!r}"
        )


def test_sql_db_types_are_relational():
    """SQL_DB_TYPES 中的所有 db_type 的 paradigm 都是 'relational'."""
    from app.engine.db_types import SQL_DB_TYPES, PARADIGM_MAP

    for db_type in SQL_DB_TYPES:
        assert PARADIGM_MAP[db_type] == "relational", (
            f"SQL_DB_TYPES 中 {db_type!r} 的 paradigm={PARADIGM_MAP[db_type]!r}, 预期 'relational'"
        )


def test_document_db_types_are_document():
    """DOCUMENT_DB_TYPES 中的所有 db_type 的 paradigm 都是 'document'."""
    from app.engine.db_types import DOCUMENT_DB_TYPES, PARADIGM_MAP

    for db_type in DOCUMENT_DB_TYPES:
        assert PARADIGM_MAP[db_type] == "document", (
            f"DOCUMENT_DB_TYPES 中 {db_type!r} 的 paradigm={PARADIGM_MAP[db_type]!r}, 预期 'document'"
        )


def test_sql_and_document_disjoint():
    """SQL_DB_TYPES ∪ DOCUMENT_DB_TYPES = SUPPORTED_DB_TYPES, 两集合不交."""
    from app.engine.db_types import DOCUMENT_DB_TYPES, SQL_DB_TYPES, SUPPORTED_DB_TYPES

    assert SQL_DB_TYPES.isdisjoint(DOCUMENT_DB_TYPES), (
        f"SQL_DB_TYPES={SQL_DB_TYPES} 与 DOCUMENT_DB_TYPES={DOCUMENT_DB_TYPES} 有交集"
    )
    assert SQL_DB_TYPES | DOCUMENT_DB_TYPES == SUPPORTED_DB_TYPES, (
        f"SQL ∪ Document = {SQL_DB_TYPES | DOCUMENT_DB_TYPES} "
        f"!= SUPPORTED = {SUPPORTED_DB_TYPES}"
    )


def test_supported_db_types_eq_drivers_keys():
    """SUPPORTED_DB_TYPES 与 DRIVERS.keys() 完全一致."""
    from app.engine.db_types import SUPPORTED_DB_TYPES
    from app.engine.drivers import DRIVERS

    assert SUPPORTED_DB_TYPES == frozenset(DRIVERS.keys()), (
        f"SUPPORTED_DB_TYPES={SUPPORTED_DB_TYPES} != DRIVERS.keys()={set(DRIVERS.keys())}"
    )


def test_valid_paradigms_from_drivers():
    """VALID_PARADIGMS 是 DRIVERS 中所有 driver.paradigm 的去重集合."""
    from app.engine.db_types import VALID_PARADIGMS
    from app.engine.drivers import DRIVERS

    expected = frozenset({d.paradigm for d in DRIVERS.values()})
    assert VALID_PARADIGMS == expected, (
        f"VALID_PARADIGMS={VALID_PARADIGMS} != expected={expected}"
    )


def test_startup_assertion_paradigm_present():
    """验证所有已注册 driver 都有 paradigm 属性且值为合法值 (模拟启动期自检)."""
    from app.engine.drivers import DRIVERS

    for db_type, driver in DRIVERS.items():
        p = getattr(driver, "paradigm", None)
        assert p is not None, f"driver '{db_type}' 缺 paradigm 属性"
        assert p in ("relational", "document"), (
            f"driver '{db_type}'.paradigm={p!r}, 预期 'relational' 或 'document'"
        )


def test_all_drivers_have_list_object_names():
    """验证所有已注册 driver 都有 list_object_names 方法."""
    from app.engine.drivers import DRIVERS

    for db_type, driver in DRIVERS.items():
        assert hasattr(driver, "list_object_names"), (
            f"driver '{db_type}' 缺 list_object_names 方法"
        )
        assert callable(driver.list_object_names), (
            f"driver '{db_type}'.list_object_names 不是 callable"
        )
