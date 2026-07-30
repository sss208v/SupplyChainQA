"""
调优完成后执行：读取最优参数 → 更新 config.py → 更新 .env.example → 更新知识图谱
用法: python backend/scripts/apply_tuning_results.py
"""
import json, os, re, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..")
EVAL_DIR = os.path.join(PROJECT_ROOT, "eval")

# 1. 读取最优参数
report_path = os.path.join(EVAL_DIR, "rrf_full_tuning_report.json")
if not os.path.exists(report_path):
    print(f"错误: 调优报告不存在: {report_path}")
    print("请先运行: python backend/scripts/tune_all_weights.py")
    sys.exit(1)

with open(report_path, encoding="utf-8") as f:
    report = json.load(f)

best = report["best_params"]
print("读取最优参数:")
for k, v in best.items():
    if k != "combined":
        print(f"  {k}: {v}")

# 2. 更新 config.py
config_path = os.path.join(PROJECT_ROOT, "app", "config.py")
with open(config_path, "r", encoding="utf-8") as f:
    config = f.read()

mapping = {
    r"(RRF_BM25_WEIGHT_PRECISE: float = )[\d.]+": f"\\g<1>{best['precise_bm25']}",
    r"(RRF_VECTOR_WEIGHT_SEMANTIC: float = )[\d.]+": f"\\g<1>{best['semantic_vector']}",
    r"(RRF_BM25_WEIGHT_DEFAULT: float = )[\d.]+": f"\\g<1>{best['default_bm25']}",
    r"(RRF_VECTOR_WEIGHT_DEFAULT: float = )[\d.]+": f"\\g<1>{best['default_vector']}",
    r"(RRF_K: int = )\d+": f"\\g<1>{best['rrf_k']}",
    r"(RRF_MIN_SCORE: float = )[\d.]+": f"\\g<1>{best.get('rrf_min_score', 0.008)}",
}

if "graph_alpha" in best:
    mapping[r"(GRAPH_FUSION_ALPHA: float = )[\d.]+"] = f"\\g<1>{best['graph_alpha']}"
if "graph_beta" in best:
    mapping[r"(GRAPH_FUSION_BETA: float = )[\d.]+"] = f"\\g<1>{best['graph_beta']}"

for pattern, replacement in mapping.items():
    old = re.search(pattern, config)
    if old:
        new_config = re.sub(pattern, replacement, config)
        print(f"  config.py: {old.group(0).strip()} → {re.search(pattern, new_config).group(0).strip()}")
        config = new_config

with open(config_path, "w", encoding="utf-8") as f:
    f.write(config)
print(f"\nconfig.py 已更新")

# 3. 更新 .env.example
env_path = os.path.join(PROJECT_ROOT, "..", ".env.example")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        env = f.read()

    env_mapping = {
        r"(RRF_BM25_WEIGHT_PRECISE=)[\d.]+": f"\\g<1>{best['precise_bm25']}",
        r"(RRF_VECTOR_WEIGHT_SEMANTIC=)[\d.]+": f"\\g<1>{best['semantic_vector']}",
        r"(RRF_BM25_WEIGHT_DEFAULT=)[\d.]+": f"\\g<1>{best['default_bm25']}",
        r"(RRF_VECTOR_WEIGHT_DEFAULT=)[\d.]+": f"\\g<1>{best['default_vector']}",
        r"(RRF_K=)\d+": f"\\g<1>{best['rrf_k']}",
        r"(RRF_MIN_SCORE=)[\d.]+": f"\\g<1>{best.get('rrf_min_score', 0.008)}",
    }
    if "graph_alpha" in best:
        env_mapping[r"(GRAPH_FUSION_ALPHA=)[\d.]+"] = f"\\g<1>{best['graph_alpha']}"
    if "graph_beta" in best:
        env_mapping[r"(GRAPH_FUSION_BETA=)[\d.]+"] = f"\\g<1>{best['graph_beta']}"

    for pattern, replacement in env_mapping.items():
        if re.search(pattern, env):
            env = re.sub(pattern, replacement, env)
            print(f"  .env.example: {pattern} updated")

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env)
    print(f".env.example 已更新")

# 4. 更新知识图谱
kg_path = os.path.join(PROJECT_ROOT, "..", ".understand-anything", "knowledge-graph.json")
if os.path.exists(kg_path):
    with open(kg_path, "r", encoding="utf-8") as f:
        kg = json.load(f)

    # 更新 RRF 相关的 concept 节点描述
    for node in kg.get("nodes", []):
        nid = node.get("id", "")
        if nid == "concept:adaptive_rrf":
            node["summary"] = (
                f"自适应RRF融合:precise查询BM25×{best['precise_bm25']}、"
                f"semantic查询向量×{best['semantic_vector']}、"
                f"default等权×{best['default_bm25']}。"
                f"RRF_K={best['rrf_k']}。"
                f"通过optuna+llama.cpp 7B真实调优验证。"
            )
            print(f"  知识图谱: {nid} 已更新")
        elif nid == "concept:graph_rag" and "graph_alpha" in best:
            # 更新 Graph RAG 描述中的权重
            old_summary = node.get("summary", "")
            if f"alpha={best['graph_alpha']}" not in old_summary:
                node["summary"] = old_summary.rstrip("。") + (
                    f"。Graph融合权重:alpha={best['graph_alpha']}/beta={best['graph_beta']}。"
                )
                print(f"  知识图谱: {nid} 已更新")

    with open(kg_path, "w", encoding="utf-8") as f:
        json.dump(kg, f, ensure_ascii=False, indent=2)
    print(f"知识图谱已更新")

print(f"\n所有文件已更新。调优报告: {report_path}")
