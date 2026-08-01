"""
SupplyChainRAG - 用户模型与RBAC角色
============================================================
【设计说明】
RBAC (Role-Based Access Control) 权限模型（部门 × 级别二维）：
- role（部门维度）: admin/purchase/warehouse/... 决定数据可见范围
  （Milvus security_group 行级过滤、工具列表匹配）
- level（级别维度）: admin/manager/employee 决定操作权限
  （能否上传/删除文档、调用写工具、维护部门记忆）

二维分离的原因：同一部门内经理与普通员工看到相同数据
（共享 security_group），但操作权限不同（经理可管理，员工只读）。
============================================================
"""
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(str, Enum):
    """用户角色枚举（部门级权限，决定数据可见范围）"""
    ADMIN = "admin"            # 管理员 - 全部权限
    PURCHASE = "purchase"      # 采购部
    WAREHOUSE = "warehouse"    # 仓库部
    QUALITY = "quality"        # 质量部
    PRODUCTION = "production"  # 生产部
    FINANCE = "finance"        # 财务部
    LOGISTICS = "logistics"    # 物流部


class UserLevel(str, Enum):
    """用户级别枚举（操作权限维度）

    与部门 role 正交：level 控制“能做什么操作”，role 控制“能看什么数据”。
    - admin: 全部操作（上传/删除任意部门、维护术语表、一键导入）
    - manager: 本部门管理操作（上传/删除本部门文档、调用写工具、沉淀部门记忆）
    - employee: 只读（检索/对话/只读工具/查看部门记忆）
    """
    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"


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

    # RBAC角色（部门维度，决定数据可见范围）
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserRole.PURCHASE.value
    )

    # 操作级别（部门内权限维度：admin/manager/employee）
    level: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserLevel.EMPLOYEE.value
    )

    # 所属部门（manager角色用于数据隔离）
    department: Mapped[str] = mapped_column(String(64), nullable=True, default=None)

    # 账户是否激活
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False
    )

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, role={self.role})>"

    def to_dict(self) -> dict:
        """转换为字典（不包含密码）"""
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "level": self.level,
            "department": self.department,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
