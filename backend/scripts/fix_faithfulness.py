# -*- coding: utf-8 -*-
"""Fix Faithfulness by strengthening the RAG prompt."""
import sys, os, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

rag_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                        "app", "agents", "rag.py")

with open(rag_path, "r", encoding="utf-8") as f:
    content = f.read()

# New strengthened prompt
new_prompt = '''    RAG_SYSTEM_PROMPT = """你是一个知识库问答助手。你必须严格遵守以下规则，违反任何一条都是严重错误:

## 硬性规则:
1. **禁止使用你自己的知识** - 你只能使用下面【参考资料】中的内容回答问题。绝对不能编造、推测或添加参考资料中不存在的信息。
2. **参考资料没有信息时** - 如果参考资料中没有与问题相关的信息，直接回答："这个问题知识库中暂无相关信息"
3. **每个事实必须有引用** - 回答中的每一个事实、数字、流程都必须标注引用编号 [1] [2] 等，且这些引用必须来自参考资料
4. **不能确定时** - 如果你不能100%确定某个信息在参考资料中，就不要说，只引用你确定的部分

## 引用规则:
- 在回答正文中 标注 [1] [2] 等编号
- 回答结尾列出所有引用
- 引用格式: [编号] 文档名称 — 章节

## 输出规则:
- 第一句就回答问题
- 禁止过渡语
- 禁止结语

## 当前对话历史:
{chat_history}

## 参考资料:
{context}"""'''

# Find and replace the old prompt
old_pattern = r'    RAG_SYSTEM_PROMPT = """.*?"""'
match = re.search(old_pattern, content, re.DOTALL)
if match:
    content = content[:match.start()] + new_prompt + content[match.end():]
    with open(rag_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("RAG_SYSTEM_PROMPT updated successfully!")
else:
    print("Could not find RAG_SYSTEM_PROMPT pattern")
