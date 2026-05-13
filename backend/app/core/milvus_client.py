"""
SmartQA Pro - Milvus向量数据库连接与操作封装
"""
import logging
from typing import Optional
from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility,
)
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class MilvusManager:
    """Milvus向量数据库管理器"""

    def __init__(self):
        self._connected = False
        self.collection: Optional[Collection] = None

    def connect(self):
        """连接Milvus"""
        try:
            connections.connect(
                alias="default",
                host=settings.MILVUS_HOST,
                port=settings.MILVUS_PORT,
                timeout=10,
            )
            self._connected = True
            logger.info(
                f"Milvus连接成功: {settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
            )
        except Exception as e:
            logger.error(f"Milvus连接失败: {e}")
            raise

    def disconnect(self):
        """断开Milvus连接"""
        try:
            connections.disconnect("default")
            self._connected = False
            logger.info("Milvus连接已断开")
        except Exception as e:
            logger.error(f"Milvus断开连接失败: {e}")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def create_collection(self, collection_name: Optional[str] = None) -> Collection:
        """
        创建文档向量集合

        Schema设计:
        - id: 主键 (自增)
        - doc_id: 文档ID
        - chunk_id: 切片ID
        - content: 文本内容
        - source: 来源文件名
        - page_num: 页码
        - embedding: 向量
        - security_group: 权限角色数组（行级权限控制）
        """
        name = collection_name or settings.MILVUS_COLLECTION

        # 如果集合已存在，直接加载
        if utility.has_collection(name):
            self.collection = Collection(name)
            logger.info(f"集合已存在，直接加载: {name}")
            return self.collection

        # 定义Schema
        fields = [
            FieldSchema(
                name="id",
                dtype=DataType.INT64,
                is_primary=True,
                auto_id=True,
            ),
            FieldSchema(
                name="doc_id",
                dtype=DataType.VARCHAR,
                max_length=64,
            ),
            FieldSchema(
                name="chunk_id",
                dtype=DataType.VARCHAR,
                max_length=128,
            ),
            FieldSchema(
                name="content",
                dtype=DataType.VARCHAR,
                max_length=4096,
            ),
            FieldSchema(
                name="source",
                dtype=DataType.VARCHAR,
                max_length=512,
            ),
            FieldSchema(
                name="page_num",
                dtype=DataType.INT64,
            ),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=settings.EMBEDDING_DIMENSION,
            ),
            FieldSchema(
                name="security_group",
                dtype=DataType.ARRAY,
                element_type=DataType.VARCHAR,
                max_capacity=10,
                max_length=64,
            ),
        ]

        schema = CollectionSchema(
            fields=fields,
            description="SmartQA文档向量集合",
            enable_dynamic_field=True,
        )

        # 创建集合
        self.collection = Collection(
            name=name,
            schema=schema,
        )

        # 创建向量索引
        index_params = {
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }
        self.collection.create_index(
            field_name="embedding",
            index_params=index_params,
        )

        logger.info(f"集合创建成功: {name} (含 security_group 数组列)")
        return self.collection

    def insert(
        self,
        doc_id: str,
        chunk_id: str,
        content: str,
        embedding: list,
        source: str = "",
        page_num: int = 0,
        security_group: list = None,
    ) -> dict:
        """插入向量数据"""
        if not self.collection:
            self.create_collection()

        data = [
            {
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "content": content,
                "source": source,
                "page_num": page_num,
                "embedding": embedding,
                "security_group": security_group or ["admin"],
            }
        ]

        result = self.collection.insert(data)
        self.collection.flush()
        logger.info(f"插入向量数据成功: doc_id={doc_id}, chunk_id={chunk_id}")
        return {"insert_count": result.insert_count, "ids": result.primary_keys}

    def batch_insert(self, records: list[dict]) -> dict:
        """批量插入向量数据"""
        if not self.collection:
            self.create_collection()

        result = self.collection.insert(records)
        self.collection.flush()
        logger.info(f"批量插入成功: {result.insert_count}条")
        return {"insert_count": result.insert_count, "ids": result.primary_keys}

    def search(
        self,
        query_embedding: list,
        top_k: int = 20,
        expr: Optional[str] = None,
    ) -> list[dict]:
        """
        向量检索

        Args:
            query_embedding: 查询向量
            top_k: 返回Top-K结果
            expr: 过滤表达式，如 'doc_id == "xxx"'
        """
        if not self.collection:
            self.create_collection()

        self.collection.load()

        search_params = {
            "metric_type": "COSINE",
            "params": {"nprobe": 16},
        }

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=expr,
            output_fields=["doc_id", "chunk_id", "content", "source", "page_num", "section_title", "security_group"],
        )

        hits = []
        for hit in results[0]:
            hits.append(
                {
                    "id": hit.id,
                    "score": hit.score,
                    "doc_id": hit.entity.get("doc_id"),
                    "chunk_id": hit.entity.get("chunk_id"),
                    "content": hit.entity.get("content"),
                    "source": hit.entity.get("source"),
                    "page_num": hit.entity.get("page_num"),
                }
            )

        logger.info(f"向量检索完成: 返回{len(hits)}条结果")
        return hits

    def delete_by_doc_id(self, doc_id: str):
        """根据doc_id删除数据"""
        if not self.collection:
            return
        self.collection.delete(f'doc_id == "{doc_id}"')
        self.collection.flush()
        logger.info(f"删除文档数据: doc_id={doc_id}")

    def get_collection_stats(self) -> dict:
        """获取集合统计信息"""
        if not self.collection:
            return {"error": "集合未初始化"}

        self.collection.flush()
        stats = self.collection.num_entities
        return {
            "collection_name": self.collection.name,
            "num_entities": stats,
        }

    def get_count(self) -> int:
        """获取文档数量"""
        if not self.collection:
            return 0
        try:
            self.collection.flush()
            return self.collection.num_entities or 0
        except Exception:
            return 0

    def build_visibility_expr(self, role: str, user_doc_ids: Optional[list] = None) -> str:
        """根据用户角色构建 Milvus 过滤表达式（基于 security_group 数组列）

        使用 array_contains 实现行级权限控制：
        - admin 角色可以看到所有文档
        - 其他角色只能看到 security_group 包含该角色的文档

        Args:
            role: 用户角色 (admin/finance/sales/developer/employee等)
            user_doc_ids: 用户指定的文档ID列表

        Returns:
            Milvus 过滤表达式
        """
        parts = []

        # 非 admin 角色需要过滤
        if role != "admin":
            parts.append(f'array_contains(security_group, "{role}")')

        # 如果用户指定了 doc_ids，进一步过滤
        if user_doc_ids:
            ids_str = ", ".join(f'"{d}"' for d in user_doc_ids)
            parts.append(f"doc_id in [{ids_str}]")

        return " and ".join(parts) if parts else ""

    def list_documents(self, role: str = "admin") -> list[dict]:
        """
        获取知识库中文档列表（按角色过滤）

        Args:
            role: 用户角色，admin 看全部，其他角色用 array_contains 过滤
        """
        if not self.collection:
            return []

        self.collection.flush()
        if self.collection.num_entities == 0:
            return []

        # 加载 collection 到内存（查询前必须）
        self.collection.load()

        # 构建过滤表达式
        expr = 'id >= 0'  # 默认匹配全部
        if role != "admin":
            expr = f'array_contains(security_group, "{role}")'

        results = self.collection.query(
            expr=expr,
            output_fields=["doc_id", "source", "security_group"],
            limit=16384,
        )

        # 按doc_id分组统计chunk数量和来源文件名
        doc_map: dict[str, dict] = {}
        for row in results:
            did = row.get("doc_id", "")
            if did not in doc_map:
                doc_map[did] = {
                    "doc_id": did,
                    "chunk_count": 0,
                    "source": row.get("source", "unknown"),
                    "security_group": row.get("security_group", ["admin"]),
                }
            doc_map[did]["chunk_count"] += 1

        logger.info(f"获取文档列表: 共{len(doc_map)}个文档")
        return list(doc_map.values())

    # ---- 多模态图像Collection（架构三）----

    def create_image_collection(self, collection_name: str, dim: int = 512) -> Collection:
        """创建图像向量集合（CLIP embedding）"""
        if utility.has_collection(collection_name):
            col = Collection(collection_name)
            logger.info(f"[CLIP] 图像集合已存在: {collection_name}")
            return col

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="image_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="clip_embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="base64_data", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="description", dtype=DataType.VARCHAR, max_length=2048),
            FieldSchema(name="security_group", dtype=DataType.ARRAY,
                        element_type=DataType.VARCHAR, max_capacity=10, max_length=64),
        ]
        schema = CollectionSchema(fields, description="SmartQA CLIP 多模态图像索引")
        col = Collection(collection_name, schema)

        # 创建向量索引
        index_params = {
            "metric_type": "IP",  # Inner Product（CLIP 使用 L2 normalized vectors → IP = cosine）
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }
        col.create_index("clip_embedding", index_params)
        col.load()
        logger.info(f"[CLIP] 图像集合创建完成: {collection_name}, dim={dim}")
        return col

    def insert_image(
        self,
        collection_name: str,
        image_id: str,
        source: str,
        clip_embedding: list[float],
        base64_data: str = "",
        description: str = "",
        security_group: list[str] = None,
    ):
        """插入一张图片到 CLIP 图像集合"""
        col = self.create_image_collection(collection_name, dim=len(clip_embedding))
        data = [{
            "image_id": image_id,
            "source": source,
            "clip_embedding": clip_embedding,
            "base64_data": base64_data,
            "description": description,
            "security_group": security_group or ["admin"],
        }]
        col.insert(data)
        col.flush()
        logger.info(f"[CLIP] 图片入库: {image_id}")

    def search_images(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 3,
        expr: str = "",
    ) -> list[dict]:
        """在图像集合中搜索相似图片"""
        if not utility.has_collection(collection_name):
            logger.warning(f"[CLIP] 图像集合不存在: {collection_name}")
            return []

        col = Collection(collection_name)
        col.load()

        search_params = {"metric_type": "IP", "params": {"nprobe": 16}}
        results = col.search(
            data=[query_embedding],
            anns_field="clip_embedding",
            param=search_params,
            limit=top_k,
            expr=expr if expr else None,
            output_fields=["image_id", "source", "base64_data", "description"],
        )

        images = []
        for hits in results:
            for hit in hits:
                images.append({
                    "image_id": hit.entity.get("image_id", ""),
                    "source": hit.entity.get("source", ""),
                    "base64_data": hit.entity.get("base64_data", ""),
                    "description": hit.entity.get("description", ""),
                    "score": float(hit.score),
                })
        return images


# 全局单例
milvus_manager = MilvusManager()
