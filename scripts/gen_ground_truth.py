"""生成 92 篇文档的 ground truth QA 对"""
import json, os, re

KNOWLEDGE_DIR = "/mnt/c/Users/sss208/Desktop/agent/supply-chain-qa/knowledge"
OUT_DIR = "/mnt/c/Users/sss208/Desktop/agent/supply-chain-qa/backend/eval"

def extract_qa(filename, text):
    """从文档内容提取 QA 对"""
    qa_pairs = []
    
    # Extract title from first line
    title_match = re.search(r'# (.+)', text)
    title = title_match.group(1) if title_match else filename
    
    # Extract purpose section
    purpose_match = re.search(r'### 1\.1 目的\n(.+?)(?:\n##|\n###)', text)
    purpose = purpose_match.group(1).strip() if purpose_match else ""
    
    # Extract KPI metrics
    kpi_section = re.search(r'### 4\.1 关键指标.*?\n((?:\|.+\|\n)+)', text)
    
    # Extract exception handling
    exception_section = re.search(r'## 5\. 异常处理\n\n(.+?)(?:\n##|\Z)', text, re.DOTALL)
    
    # Extract steps
    steps_section = re.search(r'### 3\.2 详细步骤\n(.+?)(?:\n##|\Z)', text, re.DOTALL)
    
    # QA 1: What is this document about?
    if purpose:
        qa_pairs.append({
            "question": f"{title}的主要内容是什么？",
            "answer_snippet": purpose[:200],
            "section": "purpose"
        })
    
    # QA 2: What are the KPIs?
    if kpi_section:
        qa_pairs.append({
            "question": f"{title}有哪些关键考核指标？",
            "answer_snippet": kpi_section.group(0)[:200],
            "section": "kpi"
        })
    
    # QA 3: How to handle exceptions?
    if exception_section:
        qa_pairs.append({
            "question": f"在执行{title}时遇到异常怎么处理？",
            "answer_snippet": exception_section.group(1)[:200],
            "section": "exception"
        })
    
    # QA 4: What are the steps?
    if steps_section:
        first_step = re.search(r'#### 步骤\d：(.+)', steps_section.group(1))
        if first_step:
            qa_pairs.append({
                "question": f"{title}的第一步操作是什么？",
                "answer_snippet": first_step.group(0)[:200],
                "section": "steps"
            })
    
    # QA 5: Generic query about this topic
    qa_pairs.append({
        "question": f"请介绍一下{title}的规范要求",
        "answer_snippet": purpose[:200] if purpose else title,
        "section": "overview"
    })
    
    return title, qa_pairs

def main():
    all_pairs = []
    
    files = sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "SC-*.md")))
    # Also include original knowledge files
    orig_files = sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md")))
    files = sorted(set(orig_files))
    
    for filepath in files:
        fname = os.path.basename(filepath)
        # Skip the comprehensive data manual (too big, already handled separately)
        if "comprehensive" in fname.lower() or "readme" in fname.lower():
            continue
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
        except:
            continue
        
        if len(text) < 500:
            continue
        
        title, pairs = extract_qa(fname, text)
        all_pairs.extend(pairs)
        print(f"  {fname}: {len(pairs)} QA pairs")
    
    # Deduplicate by question
    seen = set()
    unique = []
    for p in all_pairs:
        if p["question"] not in seen:
            seen.add(p["question"])
            unique.append(p)
    
    print(f"\nTotal: {len(unique)} QA pairs from {len(files)} documents")
    
    # Save
    out_path = os.path.join(OUT_DIR, "test_dataset_expanded.py")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('"""Expanded ground truth — QA pairs from 92 supply chain documents"""\n\n')
        f.write('QA_PAIRS = [\n')
        for p in unique:
            q = p["question"].replace('"', '\\"')
            a = p["answer_snippet"].replace('"', '\\"').replace('\n', ' ')
            f.write(f'    {{"question": "{q}", "answer": "{a[:150]}"}},\n')
        f.write(']\n')
    
    print(f"Saved to {out_path}")

if __name__ == "__main__":
    import glob
    main()
