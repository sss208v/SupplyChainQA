"""
SmartQA Pro - 用户模型与RBAC角色
============================================================
【设计说明】
RBAC (Role-Based Access Control) 权限模型：
- admin: 管理员，全部权限（增删改查所有知识库、用户管理）
- manager: 部门经理，本部门知识库管理权限（增删改查本部门文档）
- employee: 普通员工，仅查看公开知识库
============================================================
"""
from datetime import datetime
from enum import Enum
from sqlalchemy import String, Boolean, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class UserRole(str, Enum):
    """用户角色枚举"""
    ADMIN = "admin"        # 管理员 - 全部权限
    MANAGER = "manager"    # 部门经理 - 本部门知识库
    EMPLOYEE = "employee"  # 普通员工 - 公开知识库


class User(Base):
    """
    用户表

    核心设计：
    1. 密码使用 hashlib PBKDF2 哈希存储（不可逆）
    2. role字段控制RBAC权限等级
    3. department字段用于manager角色的数据隔离
    4. is_active用于禁用账户（软删除）
    """
    __tablename__ = "users"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 用户名（唯一）
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )

    # 密码哈希（PBKDF2-SHA256）
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)

    # RBAC角色
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserRole.EMPLOYEE.value
    )

    # 所属部门（manager角色用于数据隔离）
    department: Mapped[str] = mapped_column(String(64), nullable=True, default=None)

    # 账户是否激活
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, role={self.role})>"

    def to_dict(self) -> dict:
        """转换为字典（不包含密码）"""
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "department": self.department,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
