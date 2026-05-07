"""
SmartQA Pro - 经典信息检索评估指标
============================================================
【面试要点】RAG 领域经典 IR 指标

本模块实现了信息检索（Information Retrieval）领域的经典评估指标，
这些指标源自搜索系统评估，是评估 RAG 检索质量的基础指标。

指标列表：
1. Precision@K（精确率@K）
2. Recall@K（召回率@K）
3. MRR（Mean Reciprocal Rank，平均倒数排名）
4. NDCG@K（Normalized Discounted Cumulative Gain）
5. F1@K
6. BM25（算法说明 + 调参建议）

【面试高频问题】
- "RAG 系统的检索质量如何评估？"
- "解释 Precision@K 和 Recall@K 的区别"
- "MRR 和 NDCG@K 各自的优势是什么？"
- "BM25 相比 TF-IDF 有什么改进？"
============================================================
"""
import math
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


# ============================================================
# 数据结构定义
# ============================================================

@dataclass
class IRMetricsResult:
    """单次检索的评估结果"""
    precision_at_k: float
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    f1_at_k: float
    
    def to_dict(self) -> Dict[str, float]:
        return {
            "precision_at_k": self.precision_at_k,
            "recall_at_k": self.recall_at_k,
            "mrr": self.mrr,
            "ndcg_at_k": self.ndcg_at_k,
            "f1_at_k": self.f1_at_k,
        }


@dataclass
class IRMetricsSummary:
    """多查询的聚合评估结果"""
    avg_precision_at_k: float
    avg_recall_at_k: float
    avg_mrr: float
    avg_ndcg_at_k: float
    avg_f1_at_k: float
    num_queries: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "avg_precision_at_k": self.avg_precision_at_k,
            "avg_recall_at_k": self.avg_recall_at_k,
            "avg_mrr": self.avg_mrr,
            "avg_ndcg_at_k": self.avg_ndcg_at_k,
            "avg_f1_at_k": self.avg_f1_at_k,
            "num_queries": self.num_queries,
        }


# ============================================================
# 指标计算函数
# ============================================================

def precision_at_k(
    retrieved_docs: List[str],
    relevant_docs: List[str],
    k: int = 10
) -> float:
    """
    Precision@K（精确率@K）
    ========================
    
    【是什么】
    在检索系统返回的前 K 个结果中，有多少是真正相关的。
    
    【为什么用】
    - 衡量检索结果的质量
    - 用户通常只关注前 K 个结果
    - 反映"检索回来的东西对不对"
    
    【如何计算】
    Precision@K = (前K个结果中相关文档数) / K
    
    【理想值】
    1.0（所有检索结果都相关）
    
    【面试回答模板】
    "Precision@K 衡量的是检索结果前K项中相关文档的比例。
    公式是：相关文档数 / K。
    例如 K=5 时，如果返回的5个结果中有3个相关，Precision@5 = 0.6。"
    """
    if k <= 0:
        return 0.0
    k = min(k, len(retrieved_docs))
    if k == 0:
        return 0.0
    
    relevant_set = set(relevant_docs)
    num_relevant = sum(1 for doc in retrieved_docs[:k] if doc in relevant_set)
    
    return num_relevant / k


def recall_at_k(
    retrieved_docs: List[str],
    relevant_docs: List[str],
    k: int = 10
) -> float:
    """
    Recall@K（召回率@K）
    ========================
    
    【是什么】
    在所有相关文档中，有多少被检索系统找回来了。
    
    【为什么用】
    - 衡量检索系统的覆盖率
    - 反映"能找到多少相关的东西"
    - 在需要全面信息的场景（如法律检索）尤为重要
    
    【如何计算】
    Recall@K = (前K个结果中相关文档数) / (所有相关文档数)
    
    【理想值】
    1.0（所有相关文档都被检索到）
    
    【面试回答模板】
    "Recall@K 衡量的是所有相关文档中有多少在前K个结果中被找到。
    公式是：前K个相关文档数 / 总相关文档数。
    例如总共有10个相关文档，前K=20个结果中有8个相关，Recall@20 = 0.8。"
    """
    if not relevant_docs:
        return 0.0
    
    k = min(k, len(retrieved_docs))
    if k == 0:
        return 0.0
    
    relevant_set = set(relevant_docs)
    num_relevant = sum(1 for doc in retrieved_docs[:k] if doc in relevant_set)
    
    return num_relevant / len(relevant_set)


def mean_reciprocal_rank(
    retrieved_docs_list: List[List[str]],
    relevant_docs_list: List[List[str]]
) -> float:
    """
    MRR（Mean Reciprocal Rank，平均倒数排名）
    ===========================================
    
    【是什么】
    多个查询中，第一个相关文档出现位置的倒数的平均值。
    
    【为什么用】
    - 特别适合"首条结果质量"很重要的场景
    - 搜索引擎的"第一条命中"至关重要
    - 对检索系统排序是否把正确答案放在前面很敏感
    
    【如何计算】
    MRR = (1/Q) * Σ(1/rank_i)
    其中 rank_i 是第 i 个查询第一个相关文档出现的位置。
    
    【理想值】
    1.0（所有查询的第一个结果都是相关的）
    
    【面试回答模板】
    "MRR 关注的是正确答案的平均排名位置。对每个查询，找到第一个相关文档的排名，
    取倒数，然后平均。例如三个查询的第一个相关文档分别排在第1、2、5位，
    MRR = (1/1 + 1/2 + 1/5) / 3 = 0.57。"
    
    【示例】
    查询1: 检索结果 [docA, docB, docC], 相关的是 docB (排名第2) → RR = 1/2
    查询2: 检索结果 [docD, docE, docF], 相关的是 docD (排名第1) → RR = 1/1
    MRR = (1/2 + 1/1) / 2 = 0.75
    """
    if not retrieved_docs_list or not relevant_docs_list:
        return 0.0
    
    if len(retrieved_docs_list) != len(relevant_docs_list):
        raise ValueError("retrieved_docs_list 和 relevant_docs_list 长度必须一致")
    
    reciprocal_ranks = []
    for retrieved, relevant in zip(retrieved_docs_list, relevant_docs_list):
        relevant_set = set(relevant)
        rr = 0.0
        for i, doc in enumerate(retrieved, 1):
            if doc in relevant_set:
                rr = 1.0 / i
                break
        reciprocal_ranks.append(rr)
    
    return sum(reciprocal_ranks) / len(reciprocal_ranks)


def dcg_at_k(
    retrieved_docs: List[str],
    relevance_scores: List[float],
    k: int = 10
) -> float:
    """
    DCG@K（Discounted Cumulative Gain）
    ======================================
    
    【是什么】
    折扣累积收益。考虑文档相关性和位置因素的评估指标。
    
    【计算公式】
    DCG@K = Σ(1..K) rel_i / log2(i+1)
    其中 rel_i 是第 i 个文档的相关性得分（通常为0、1、2、3等级）。
    
    【注意】
    这是 NDCG 的辅助函数，通常 relevance_scores 传入与 retrieved_docs
    对应的相关性标记（1=相关，0=不相关）或实际相关性分数。
    """
    if k <= 0:
        return 0.0
    k = min(k, len(retrieved_docs))
    if k == 0:
        return 0.0
    
    dcg = 0.0
    for i in range(k):
        # relevance_scores[i] 应该是 0-1（相关/不相关）或 0-3（相关性等级）
        rel = relevance_scores[i] if i < len(relevance_scores) else 0.0
        dcg += rel / math.log2(i + 2)  # i+2 因为 i 从0开始
    return dcg


def ndcg_at_k(
    retrieved_docs: List[str],
    relevant_docs: List[str],
    k: int = 10,
    relevance_method: str = "binary"
) -> float:
    """
    NDCG@K（Normalized Discounted Cumulative Gain）
    ==================================================
    
    【是什么】
    归一化折扣累积收益。NDCG 是 DCG 除以 IDCG（理想DCG）得到的归一化值。
    
    【为什么用】
    - 考虑文档相关性的不同程度（不只是二元相关）
    - 对排名靠前的错误惩罚更重
    - 是搜索系统评估的金标准
    
    【如何计算】
    1. DCG@K = Σ(1..K) rel_i / log2(i+1)
    2. IDCG@K = 按最优顺序排列的 DCG@K
    3. NDCG@K = DCG@K / IDCG@K
    
    【理想值】
    1.0（检索结果完美排序）
    
    【面试回答模板】
    "NDCG@K 是搜索系统最常用的评估指标。相比 Precision@K，它有两个优势：
    一是支持多级相关性（不只是相关/不相关，可以是0-3分）；
    二是考虑位置因素，排在前面的错误比后面的更严重。
    NDCG = DCG / IDCG，归一化到 0-1，1 表示完美排序。"
    
    【示例】
    查询: 检索结果 [A, B, C, D]，相关文档是 [A, C]
    相关性分数: [3, 0, 2, 0] (A相关3分，B不相关，C相关2分，D不相关)
    
    DCG = 3/log2(2) + 0/log2(3) + 2/log2(4) = 3 + 0 + 1 = 4
    IDCG = 3/log2(2) + 2/log2(3) + 0/log2(4) = 3 + 1.26 + 0 = 4.26
    NDCG = 4 / 4.26 ≈ 0.94
    """
    if k <= 0:
        return 0.0
    k = min(k, len(retrieved_docs))
    if k == 0:
        return 0.0
    
    relevant_set = set(relevant_docs)
    
    # 构建相关性分数列表
    if relevance_method == "binary":
        # 二元相关：相关=1，不相关=0
        relevance_scores = [1.0 if doc in relevant_set else 0.0 for doc in retrieved_docs[:k]]
    else:
        # 假设传入的是预计算的相关性分数
        relevance_scores = retrieved_docs[:k]
    
    # 计算 DCG
    dcg = 0.0
    for i in range(k):
        rel = relevance_scores[i] if i < len(relevance_scores) else 0.0
        dcg += rel / math.log2(i + 2)
    
    # 计算 IDCG（理想情况：相关文档排在最前面）
    ideal_scores = sorted(relevance_scores, reverse=True)
    idcg = 0.0
    for i in range(k):
        rel = ideal_scores[i] if i < len(ideal_scores) else 0.0
        idcg += rel / math.log2(i + 2)
    
    if idcg == 0:
        return 0.0
    
    return dcg / idcg


def f1_at_k(
    retrieved_docs: List[str],
    relevant_docs: List[str],
    k: int = 10
) -> float:
    """
    F1@K
    =====
    
    【是什么】
    Precision@K 和 Recall@K 的调和平均，综合衡量两者。
    
    【为什么用】
    - Precision 和 Recall 通常是跷跷板关系
    - F1 综合考虑两者，找到平衡点
    - 适合需要同时关注"准确"和"全面"的场景
    
    【如何计算】
    F1@K = 2 * (Precision@K * Recall@K) / (Precision@K + Recall@K)
    
    【理想值】
    1.0（Precision 和 Recall 都达到 1.0）
    
    【面试回答模板】
    "F1@K 是 Precision@K 和 Recall@K 的调和平均，综合反映检索系统的性能。
    公式是：2 * P * R / (P + R)。当 P 和 R 差距很大时，F1 会被拉低。
    例如 P=0.8, R=0.4，则 F1 = 2*0.8*0.4/(0.8+0.4) = 0.53。"
    """
    p = precision_at_k(retrieved_docs, relevant_docs, k)
    r = recall_at_k(retrieved_docs, relevant_docs, k)
    
    if p + r == 0:
        return 0.0
    
    return 2 * (p * r) / (p + r)


def compute_all_ir_metrics(
    retrieved_docs: List[str],
    relevant_docs: List[str],
    k: int = 10
) -> IRMetricsResult:
    """
    计算所有 IR 指标的便捷函数
    
    Args:
        retrieved_docs: 检索系统返回的文档列表（按相关性排序）
        relevant_docs: 实际相关的文档列表
        k: 评估的深度（默认10，即评估前10个结果）
    
    Returns:
        IRMetricsResult 包含所有指标值
    """
    return IRMetricsResult(
        precision_at_k=precision_at_k(retrieved_docs, relevant_docs, k),
        recall_at_k=recall_at_k(retrieved_docs, relevant_docs, k),
        mrr=0.0,  # MRR 需要多查询，下面的函数计算
        ndcg_at_k=ndcg_at_k(retrieved_docs, relevant_docs, k),
        f1_at_k=f1_at_k(retrieved_docs, relevant_docs, k),
    )


def compute_ir_metrics_for_batch(
    retrieved_docs_list: List[List[str]],
    relevant_docs_list: List[List[str]],
    k: int = 10
) -> IRMetricsSummary:
    """
    批量计算 IR 指标（多查询平均）
    
    Args:
        retrieved_docs_list: 多个查询的检索结果列表
        relevant_docs_list: 多个查询的实际相关文档列表
        k: 评估深度
    
    Returns:
        IRMetricsSummary 包含平均指标值
    """
    if not retrieved_docs_list:
        return IRMetricsSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)
    
    precisions, recalls, ndcgs, f1s = [], [], [], []
    
    for retrieved, relevant in zip(retrieved_docs_list, relevant_docs_list):
        precisions.append(precision_at_k(retrieved, relevant, k))
        recalls.append(recall_at_k(retrieved, relevant, k))
        ndcgs.append(ndcg_at_k(retrieved, relevant, k))
        f1s.append(f1_at_k(retrieved, relevant, k))
    
    # MRR 需要特殊处理
    mrr = mean_reciprocal_rank(retrieved_docs_list, relevant_docs_list)
    
    n = len(retrieved_docs_list)
    return IRMetricsSummary(
        avg_precision_at_k=sum(precisions) / n,
        avg_recall_at_k=sum(recalls) / n,
        avg_mrr=mrr,
        avg_ndcg_at_k=sum(ndcgs) / n,
        avg_f1_at_k=sum(f1s) / n,
        num_queries=n,
    )


# ============================================================
# BM25 算法实现与说明
# ============================================================

class BM25:
    """
    BM25（Best Matching 25）- 经典关键词检索算法
    ================================================
    
    【算法说明】
    BM25 是 1994 年提出的经典检索算法，至今仍是搜索引擎的核心算法之一。
    它基于 TF-IDF 思想，但做了重要改进：
    
    1. **词频饱和（TF Saturation）**
       TF-IDF 中，词频越高分数越高，没有上限。
       BM25 使用饱和函数：TF / (TF + k1)，其中 k1 通常为 1.2-2.0
       这意味着词频增加到一定程度后，边际收益递减。
    
    2. **文档长度归一化（Document Length Normalization）**
       短文档如果包含查询词，更可能是精准匹配。
       BM25 使用：L / avg_L（文档长度 / 平均长度）
       公式中有 (1 - b + b * L / avg_L) 作为归一化因子，其中 b 通常为 0.75。
    
    3. **IDF 改进**
       使用 log((N - n + 0.5) / (n + 0.5)) 替代简单的 log(N/n)
    
    【完整公式】
    BM25(d, q) = Σ IDF(qi) * (TF(ti,d) * (k1 + 1)) / (TF(ti,d) + k1 * (1 - b + b * L_d / L_avg))
    
    【SmartQA 中的应用】
    我们使用 rank_bm25 库实现 BM25，作为混合检索的一部分。
    配合向量检索（Milvus/BGE），显著提升召回率。
    
    【调参建议】
    | 参数 | 默认值 | 增大 → | 减小 → |
    |-----|--------|--------|--------|
    | k1 | 1.5 | 更重视词频差异 | 更忽略词频差异 |
    | b | 0.75 | 更惩罚长文档 | 更倾向长文档 |
    
    【面试高频问题】
    Q: BM25 和 TF-IDF 的区别？
    A: BM25 在 TF-IDF 基础上增加了：1) TF 饱和函数，防止词频过高过度加分；
       2) 文档长度归一化，更公平地比较不同长度的文档。
    
    Q: BM25 和向量检索如何选择？
    A: BM25 擅长精确关键词匹配（如人名、术语），向量检索擅长语义相似。
       两者互补，混合使用效果最佳。SmartQA 就是这样设计的。
    """
    
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        """
        初始化 BM25 参数
        
        Args:
            k1: 词频饱和参数，通常 1.2-2.0
            b: 文档长度归一化参数，通常 0.5-0.75
        """
        self.k1 = k1
        self.b = b
        self.corpus_size = 0
        self.avg_doc_length = 0.0
        self.doc_freqs = {}  # term -> doc_freq
        self.idf = {}
        self.doc_len = []
        self.corpus = []
    
    def get_params(self) -> Dict[str, float]:
        """返回当前参数，用于调参参考"""
        return {
            "k1": self.k1,
            "b": self.b,
            "corpus_size": self.corpus_size,
            "avg_doc_length": self.avg_doc_length,
        }
    
    def recommend_params(self) -> Dict[str, Dict[str, str]]:
        """
        返回调参建议
        
        面试时可参考：
        - k1 增大：更看重词频，适合短文档检索
        - k1 减小：更看重 IDF，适合长文档检索  
        - b 增大：对长文档惩罚更重
        - b 减小：对长文档更宽容
        """
        return {
            "k1": {
                "low": "k1=1.0: 词频影响较小，IDF 更重要",
                "default": "k1=1.5: 平衡词频和 IDF",
                "high": "k1=2.0: 词频影响更大，适合短文档",
            },
            "b": {
                "low": "b=0.5: 对文档长度不敏感",
                "default": "b=0.75: 标准归一化",
                "high": "b=0.9: 强惩罚长文档",
            }
        }


# ============================================================
# 演示代码（可直接运行测试）
# ============================================================

def demo_ir_metrics():
    """
    演示 IR 指标计算
    """
    print("=" * 60)
    print("SmartQA - IR 指标计算演示")
    print("=" * 60)
    
    # 示例1: 单查询评估
    print("\n【示例1: 单查询评估】")
    retrieved = ["doc_A", "doc_B", "doc_C", "doc_D", "doc_E"]
    relevant = ["doc_A", "doc_C"]
    
    print(f"检索结果: {retrieved}")
    print(f"相关文档: {relevant}")
    print(f"K = 5")
    
    p = precision_at_k(retrieved, relevant, k=5)
    r = recall_at_k(retrieved, relevant, k=5)
    f1 = f1_at_k(retrieved, relevant, k=5)
    ndcg = ndcg_at_k(retrieved, relevant, k=5)
    
    print(f"\nPrecision@5 = {p:.4f}")
    print(f"Recall@5 = {r:.4f}")
    print(f"F1@5 = {f1:.4f}")
    print(f"NDCG@5 = {ndcg:.4f}")
    
    # 示例2: 多查询批量评估
    print("\n" + "=" * 60)
    print("【示例2: 多查询批量评估】")
    
    retrieved_list = [
        ["doc_A", "doc_B", "doc_C", "doc_D"],
        ["doc_X", "doc_Y", "doc_Z", "doc_W"],
        ["doc_M", "doc_N", "doc_O", "doc_P"],
    ]
    relevant_list = [
        ["doc_A", "doc_C"],
        ["doc_X", "doc_Z"],
        ["doc_O"],  # 只有 doc_O 相关
    ]
    
    print(f"查询数量: {len(retrieved_list)}")
    
    summary = compute_ir_metrics_for_batch(retrieved_list, relevant_list, k=4)
    
    print(f"\n平均 Precision@4 = {summary.avg_precision_at_k:.4f}")
    print(f"平均 Recall@4 = {summary.avg_recall_at_k:.4f}")
    print(f"平均 MRR = {summary.avg_mrr:.4f}")
    print(f"平均 NDCG@4 = {summary.avg_ndcg_at_k:.4f}")
    print(f"平均 F1@4 = {summary.avg_f1_at_k:.4f}")
    
    # 示例3: 不同 K 值的对比
    print("\n" + "=" * 60)
    print("【示例3: 不同 K 值的对比】")
    
    retrieved = ["doc_1", "doc_2", "doc_3", "doc_4", "doc_5", "doc_6", "doc_7", "doc_8", "doc_9", "doc_10"]
    relevant = ["doc_2", "doc_5", "doc_9"]
    
    print(f"检索结果 (Top-10): {retrieved}")
    print(f"相关文档: {relevant}")
    print("\nK\tPrecision\tRecall\tF1\tNDCG")
    print("-" * 50)
    
    for k in [1, 3, 5, 10]:
        p = precision_at_k(retrieved, relevant, k)
        r = recall_at_k(retrieved, relevant, k)
        f = f1_at_k(retrieved, relevant, k)
        n = ndcg_at_k(retrieved, relevant, k)
        print(f"{k}\t{p:.4f}\t\t{r:.4f}\t{f:.4f}\t{n:.4f}")
    
    print("\n" + "=" * 60)
    print("BM25 参数说明")
    print("=" * 60)
    bm25 = BM25(k1=1.5, b=0.75)
    print(f"当前参数: k1={bm25.k1}, b={bm25.b}")
    print("\n调参建议:")
    for param, desc in bm25.recommend_params().items():
        print(f"\n{param}:")
        for level, text in desc.items():
            print(f"  {level}: {text}")
    
    print("\n" + "=" * 60)
    print("演示完成!")
    print("=" * 60)


if __name__ == "__main__":
    demo_ir_metrics()
