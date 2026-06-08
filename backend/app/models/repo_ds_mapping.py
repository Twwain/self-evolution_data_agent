"""仓库 ↔ 数据源映射 — 控制 repo 解析结果训练到哪些引擎"""

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RepoDataSourceMapping(Base):
    __tablename__ = "repo_ds_mappings"
    __table_args__ = (
        UniqueConstraint("repo_id", "datasource_id", name="uq_repo_ds"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("git_repos.id", ondelete="CASCADE"), index=True
    )
    datasource_id: Mapped[int] = mapped_column(
        ForeignKey("datasources.id", ondelete="CASCADE"), index=True
    )
