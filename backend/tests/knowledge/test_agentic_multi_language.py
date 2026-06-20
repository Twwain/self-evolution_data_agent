"""多语言零引导验证: Python SQLAlchemy + Go GORM, world-knowledge-only (live LLM)."""
import pytest

from app.knowledge.extraction_agent import run_extraction_agent

pytestmark = pytest.mark.asyncio


@pytest.mark.live_llm
async def test_python_sqlalchemy_zero_guidance(tmp_path):
    """纯 Python SQLAlchemy 仓库, 无 profile → agent 应从 declarative Base 发现实体."""
    repo = tmp_path / "py_repo"
    repo.mkdir()
    (repo / "models.py").write_text("""
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200))

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    total = Column(Integer, default=0)
    customer = relationship("Customer", backref="orders")
""")
    result = await run_extraction_agent(repo_path=str(repo), hint_text=None, max_iterations=20)

    obj_names = [o.get("name", "").lower() for o in result.objects]
    assert any("customer" in n for n in obj_names), f"应发现 Customer 表, 实际: {obj_names}"
    assert any("order" in n for n in obj_names), f"应发现 Order 表, 实际: {obj_names}"

    customer = next(o for o in result.objects if "customer" in o.get("name", "").lower())
    field_names = [f.get("name") for f in customer.get("fields", [])]
    assert "name" in field_names, f"Customer 应有 name 字段, 实际: {field_names}"


@pytest.mark.live_llm
async def test_go_gorm_zero_guidance(tmp_path):
    """纯 Go GORM 仓库, 无 profile → agent 应从 struct tags 发现模型."""
    repo = tmp_path / "go_repo"
    repo.mkdir()
    (repo / "model.go").write_text("""
package model

import "gorm.io/gorm"

type Product struct {
    gorm.Model
    Name  string `gorm:"column:product_name;size:100"`
    Price int    `gorm:"column:price"`
    Sku   string `gorm:"column:sku;uniqueIndex"`
}
""")
    result = await run_extraction_agent(repo_path=str(repo), hint_text=None, max_iterations=20)

    obj_names = [o.get("name", "").lower() for o in result.objects]
    assert len(result.objects) >= 1, f"应至少发现 1 个对象, 实际: {obj_names}"
    assert any("product" in n for n in obj_names), f"应发现 Product, 实际: {obj_names}"
