"""
Ground Truth for RAG Parameter Tuning
======================================
20 QA pairs derived from the real supply-chain knowledge base:
  - 供应商管理手册.md   (SUPP-2025-001, V2.8)
  - 质量检验标准.md     (QC-2025-001, V2.5)
  - 库存管理制度.md     (INV-2025-001, V3.0)

Each pair:
  - question: natural-language query
  - reference_answer: key facts extracted from the source doc
  - relevant_chunk_ids: which chunk IDs must appear in top-K results
  - source_file: which .md file the answer comes from
"""

from typing import TypedDict


class QAPair(TypedDict):
    question: str
    reference_answer: str
    relevant_chunk_ids: list[str]   # chunk_ids that must appear in retrieval
    source_file: str                 # which knowledge doc


# -----------------------------------------------------------------------
# 供应商管理手册 (SUPP-2025-001)
# -----------------------------------------------------------------------
_供应商_ids = [
    "供应商管理手册-chunk-0000", "供应商管理手册-chunk-0001",
    "供应商管理手册-chunk-0002", "供应商管理手册-chunk-0003",
]

TEST_QA_PAIRS: list[QAPair] = [
    {
        "question": "供应商准入需要提供哪些资质文件？",
        "reference_answer": "供应商准入必须提供：营业执照（经营范围覆盖所供物料）、ISO 9001质量管理体系认证（必须）、ISO 14001环境管理体系认证（优先）、行业特殊许可（特种设备制造许可证等）、近3年财务报表或银行资信证明。",
        "relevant_chunk_ids": ["供应商管理手册-chunk-0000", "供应商管理手册-chunk-0001"],
        "source_file": "供应商管理手册.md",
    },
    {
        "question": "供应商准入流程分哪几步？每步由谁负责，要多久？",
        "reference_answer": "准入流程分三步：①资质审查（采购部，5个工作日内完成）②样品测试（质量部，提供3批次样品全检，10个工作日内出具报告）③现场审核（采购+质量联合实地评估）。三项全部通过后列入《合格供应商名录》（AVL）。",
        "relevant_chunk_ids": ["供应商管理手册-chunk-0001", "供应商管理手册-chunk-0002"],
        "source_file": "供应商管理手册.md",
    },
    {
        "question": "供应商绩效评估的指标和权重是什么？",
        "reference_answer": "评估指标权重：质量合格率40%（合格标准≥98%）、交期准时率30%（合格标准≥95%）、价格竞争力20%（市场均值±10%）、服务响应10%（24小时内响应）。每季度评估一次，年度综合评定。",
        "relevant_chunk_ids": ["供应商管理手册-chunk-0002", "供应商管理手册-chunk-0003"],
        "source_file": "供应商管理手册.md",
    },
    {
        "question": "供应商评级A级和B级有什么区别？",
        "reference_answer": "A级（≥90分）为优秀供应商，享有优先分配订单权益；B级（75-89分）为合格供应商，维持正常合作。两者都无需整改，但订单优先级不同。",
        "relevant_chunk_ids": ["供应商管理手册-chunk-0003", "供应商管理手册-chunk-0004"],
        "source_file": "供应商管理手册.md",
    },
    {
        "question": "供应商被淘汰后多久可以重新申请准入？",
        "reference_answer": "被淘汰的供应商在6个月内不得重新申请准入。淘汰条件包括：连续2个季度C级且整改不达标、发生重大质量事故（批次不合格率>5%或导致客户投诉）。",
        "relevant_chunk_ids": ["供应商管理手册-chunk-0005", "供应商管理手册-chunk-0006"],
        "source_file": "供应商管理手册.md",
    },
    {
        "question": "C级供应商被要求整改，整改期多久？",
        "reference_answer": "连续2个季度评估为C级的供应商，将收到《整改通知书》，限期30天改进。整改期满仍不达标，降为D级，直接启动淘汰流程。",
        "relevant_chunk_ids": ["供应商管理手册-chunk-0005"],
        "source_file": "供应商管理手册.md",
    },

    # -------------------------------------------------------------------
    # 质量检验标准 (QC-2025-001)
    # -------------------------------------------------------------------
    {
        "question": "A类物料和B类物料的抽检比例分别是多少？",
        "reference_answer": "A类（关键物料）：100%全检，检验方式包括尺寸+外观+功能测试；B类（重要物料）：20%抽检，按GB/T 2828.1标准AQL抽样；C类（一般物料）：5%抽检，仅外观+数量核对。",
        "relevant_chunk_ids": ["质量检验标准-chunk-0000", "质量检验标准-chunk-0001"],
        "source_file": "质量检验标准.md",
    },
    {
        "question": "常规检验的时效要求是什么？需要送实验室要多久？",
        "reference_answer": "常规检验：到货后4小时内完成；需送实验室检测：2个工作日内出具报告。仓库收到送货单后通知IQC检验员，依据物料检验标准进行抽检或全检。",
        "relevant_chunk_ids": ["质量检验标准-chunk-0000", "质量检验标准-chunk-0001"],
        "source_file": "质量检验标准.md",
    },
    {
        "question": "严重缺陷（Critical）的判定标准是什么？发现后如何处理？",
        "reference_answer": "严重缺陷定义：可能导致安全隐患或产品完全丧失功能的缺陷，如电气绝缘失效、结构强度不达标、有害物质超标。判定标准：零容忍，发现即拒收整批。",
        "relevant_chunk_ids": ["质量检验标准-chunk-0002", "质量检验标准-chunk-0003"],
        "source_file": "质量检验标准.md",
    },
    {
        "question": "主要缺陷（Major）的AQL标准是多少？超出时怎么处理？",
        "reference_answer": "主要缺陷（如尺寸超差>2倍公差、功能参数偏差>5%）适用AQL 0.65，超出则整批拒收。次要缺陷（轻微色差、包装破损）适用AQL 2.5，超出则退货或让步接收。",
        "relevant_chunk_ids": ["质量检验标准-chunk-0002", "质量检验标准-chunk-0003"],
        "source_file": "质量检验标准.md",
    },
    {
        "question": "IQC来料检验的完整流程是什么？",
        "reference_answer": "IQC流程：①仓库收到送货单后通知IQC检验员；②检验员依据物料检验标准进行抽检/全检；③合格：贴绿色合格标签，办理入库；④不合格：贴红色不合格标签，隔离存放至不合格品区。",
        "relevant_chunk_ids": ["质量检验标准-chunk-0000"],
        "source_file": "质量检验标准.md",
    },

    # -------------------------------------------------------------------
    # 库存管理制度 (INV-2025-001)
    # -------------------------------------------------------------------
    {
        "question": "ABC三类物料的金额占比是多少？",
        "reference_answer": "A类物料：约20%的SKU占比，约80%的金额占比，重点管控，每周盘点；B类物料：约30%的SKU占比，约15%的金额占比，常规管控，月度盘点；C类物料：约50%的SKU占比，约5%的金额占比，简化管控，季度盘点。",
        "relevant_chunk_ids": ["库存管理制度-chunk-0000", "库存管理制度-chunk-0001"],
        "source_file": "库存管理制度.md",
    },
    {
        "question": "安全库存的计算公式是什么？",
        "reference_answer": "安全库存 = 日均消耗量 × 采购周期（天） × 1.5。其中日均消耗量取近3个月平均值，采购周期为下单到入库的实际天数，系数1.5为标准浮动系数（A类可调至1.8，C类可调至1.2）。",
        "relevant_chunk_ids": ["库存管理制度-chunk-0001", "库存管理制度-chunk-0002"],
        "source_file": "库存管理制度.md",
    },
    {
        "question": "库存预警的触发条件是什么？",
        "reference_answer": "库存预警触发条件：当库存量降至安全库存的1.2倍时，系统自动触发采购建议；当库存量降至安全库存以下，触发紧急采购流程。每半年重新评估一次ABC分类，根据物料消耗金额动态调整。",
        "relevant_chunk_ids": ["库存管理制度-chunk-0002", "库存管理制度-chunk-0003"],
        "source_file": "库存管理制度.md",
    },
    {
        "question": "盘点差异率超过多少需要启动专项调查？",
        "reference_answer": "盘点差异率处理标准：≤0.5%为正常范围，记录备查；0.5%-2%需分析原因，3个工作日内提交报告；>2%启动专项调查，追究责任。月度抽盘针对A类物料100%和B类物料30%，季度全盘覆盖所有物料。",
        "relevant_chunk_ids": ["库存管理制度-chunk-0003", "库存管理制度-chunk-0004"],
        "source_file": "库存管理制度.md",
    },
    {
        "question": "呆滞料的定义是什么？超过多久没动就算呆滞料？",
        "reference_answer": "呆滞料指在库超过6个月未发生任何收料或发料业务的物料。呆滞料处理流程：先评估是否有替代用途或可转用于其他项目；如无利用价值则通过调剂、折价出售或报废等方式处理，以释放库存空间和资金。",
        "relevant_chunk_ids": ["库存管理制度-chunk-0005", "库存管理制度-chunk-0006"],
        "source_file": "库存管理制度.md",
    },
    {
        "question": "季度全盘的责任部门是哪些？",
        "reference_answer": "季度全盘由仓储部和财务部联合执行，覆盖所有物料。月度抽盘由仓管员负责，每月25日进行，范围为A类物料100%和B类物料30%。差异率>2%时启动专项调查并追究责任。",
        "relevant_chunk_ids": ["库存管理制度-chunk-0003", "库存管理制度-chunk-0004"],
        "source_file": "库存管理制度.md",
    },
]
