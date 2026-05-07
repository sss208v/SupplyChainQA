"""
SmartQA Pro - RAGAS 评估测试数据集
============================================================
【学习要点】
1. RAGAS 评估需要以下字段：
   - user_input: 用户问题
   - response: RAG系统生成的回答
   - reference: 人工标注的标准答案（Ground Truth）
   - retrieved_contexts: RAG检索到的上下文片段

2. 测试集设计原则：
   - 覆盖不同难度：简单事实型、条件推理型、多跳组合型
   - 覆盖不同主题：确保知识库各章节都有问题
   - 包含"无法回答"的问题：测试系统的诚实性

3. RAGAS 核心指标含义：
   - Faithfulness（忠实度）：回答是否只基于检索到的上下文，不编造
   - Answer Relevance（答案相关性）：回答是否真正回答了用户问题
   - Context Precision（上下文精确度）：检索结果中相关内容的排名
   - Context Recall（上下文召回率）：标准答案所需的信息是否都被检索到
"""

# ============================================================
# 测试问题集（知识库基于"企业IT支持知识库.md"）
# ============================================================
# 每条数据包含：
# - question: 用户提问
# - reference_answer: 人工标注的标准答案（Ground Truth）

TEST_QA_PAIRS = [
    # ---- 简单事实型 ----
    {
        "question": "VPN服务器地址是什么？",
        "reference_answer": "VPN服务器地址是vpn.company.com。"
    },
    {
        "question": "公司邮箱的IMAP端口是多少？",
        "reference_answer": "公司邮箱的IMAP端口是993，使用SSL加密。"
    },
    {
        "question": "IT服务台的内线电话号码是多少？",
        "reference_answer": "IT服务台的内线电话是8888。"
    },
    {
        "question": "3楼市场部的打印机IP地址是什么？",
        "reference_answer": "3楼市场部的打印机IP地址是192.168.1.301，型号为Canon imageCLASS MF445dw。"
    },
    {
        "question": "新员工领取的笔记本电脑是什么型号？",
        "reference_answer": "新员工领取的笔记本电脑型号是ThinkPad T14s。"
    },
    {
        "question": "会议室预约最多可以提前多少天？",
        "reference_answer": "会议室预约最多可以提前14天。"
    },
    {
        "question": "企业邮箱的总容量限制是多少？",
        "reference_answer": "企业邮箱的总容量限制是10GB，超出容量后将无法接收新邮件。"
    },

    # ---- 条件推理型 ----
    {
        "question": "VPN连接时出现错误代码789应该怎么处理？",
        "reference_answer": "错误代码789表示L2TP连接失败，需要检查预共享密钥是否正确，或者在注册表中修改AssumeUDPEncapsulationContextOnSendRule的值为2。"
    },
    {
        "question": "打印队列堵塞了怎么解决？",
        "reference_answer": "打印队列堵塞时，需要打开「服务」管理器，重启Print Spooler服务来解决问题。"
    },
    {
        "question": "密码连续输错3次会怎样？",
        "reference_answer": "如果连续3次输错VPN密码，账号将被锁定30分钟。"
    },
    {
        "question": "预约了会议室但没去会怎样？",
        "reference_answer": "预约开始后15分钟未签到，系统会自动释放该会议室。"
    },
    {
        "question": "误删的邮件还能恢复吗？",
        "reference_answer": "误删的邮件可以在30天内从「已删除邮件」文件夹中恢复。"
    },

    # ---- 多跳组合型 ----
    {
        "question": "如何在Windows上配置VPN连接？",
        "reference_answer": "Windows VPN配置步骤：1.打开设置→网络和Internet→VPN；2.点击添加VPN连接；3.VPN提供商选Windows(内置)；4.连接名称输入公司VPN；5.服务器地址填vpn.company.com；6.VPN类型选L2TP/IPsec与预共享密钥；7.预共享密钥联系IT部门获取；8.登录类型选用户名和密码；9.输入域账号密码；10.保存后点击连接。"
    },
    {
        "question": "新员工入职第一天需要做哪些IT相关的事情？",
        "reference_answer": "新员工首日IT必做事项：1.到IT服务台(1F-A101)领取办公设备；2.使用初始密码(Company@工号后6位)登录电脑；3.首次登录必须修改密码；4.配置企业邮箱；5.安装企业微信并加入部门群；6.完成约45分钟的网络安全在线培训。"
    },
    {
        "question": "机密级别的数据应该怎么处理？",
        "reference_answer": "机密级别数据（红色标识）需要加密存储和传输，访问需审批。公司数据分为四个级别：公开(绿色)可对外分享、内部(黄色)仅公司内部使用、机密(红色)需加密和审批、绝密(黑色)仅限特定人员且禁止复制和打印。"
    },
    {
        "question": "想安装IntelliJ IDEA需要走什么流程？",
        "reference_answer": "IntelliJ IDEA属于开发类软件，在软件白名单中。安装流程：1.登录公司软件门户software.company.com；2.搜索IntelliJ IDEA；3.点击申请安装；4.直属主管审批(1个工作日内)；5.IT部门自动推送安装包；6.安装后激活许可证。注意JetBrains IDE是公司采购的20个浮动许可，先到先得。"
    },

    # ---- 无法回答型（测试系统诚实性） ----
    {
        "question": "公司的年假政策是什么？",
        "reference_answer": "根据现有知识库，无法回答此问题。（该信息不在IT支持知识库中）"
    },
    {
        "question": "下个季度的预算分配方案是什么？",
        "reference_answer": "根据现有知识库，无法回答此问题。（该信息不在IT支持知识库中）"
    },

    # ---- 边界情况型 ----
    {
        "question": "朝阳厅会议室能容纳多少人？有什么设备？",
        "reference_answer": "朝阳厅会议室可容纳20人，配备投影仪、视频会议系统和白板，位于1楼。"
    },
    {
        "question": "MFA验证有哪些方式？如果丢失怎么办？",
        "reference_answer": "MFA支持三种验证方式：企业微信验证码（推荐）、短信验证码、硬件安全密钥(YubiKey，IT部门统一配发)。如果MFA丢失或无法使用，需要携带工牌到IT服务台现场重置。"
    },
]
