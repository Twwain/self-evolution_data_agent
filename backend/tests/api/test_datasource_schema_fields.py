"""DataSourceCreate 收 description; DataSourceOut 出 description + db_profile."""
from app.schemas import DataSourceCreate, DataSourceOut


def test_create_accepts_description_optional():
    """description 非必填, 缺省为空串."""
    c = DataSourceCreate(
        db_type="mysql", host="h", port=3306,
        database="d", username="u", password="p",
    )
    assert c.description == ""
    c2 = DataSourceCreate(
        db_type="mysql", host="h", port=3306,
        database="d", username="u", password="p", description="电力库",
    )
    assert c2.description == "电力库"


def test_out_exposes_description_and_profile_not_password():
    """DataSourceOut 暴露 description + db_profile (dict), 不暴露 password."""
    fields = set(DataSourceOut.model_fields.keys())
    assert "description" in fields
    assert "db_profile" in fields
    assert "password" not in fields
