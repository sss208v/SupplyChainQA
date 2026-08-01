"""add user level — 部门内操作权限维度（admin/manager/employee）

Revision ID: 002_user_level
Revises: 001_initial
Create Date: 2026-08-01
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_user_level"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 既有用户默认为 employee（最低权限，安全默认）；
    # 需要 manager 权限的账号由管理员后续提升
    op.add_column(
        "users",
        sa.Column("level", sa.String(16), nullable=False, server_default="employee"),
    )


def downgrade() -> None:
    op.drop_column("users", "level")
