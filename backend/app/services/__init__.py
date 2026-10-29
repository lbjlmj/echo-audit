"""services 层按 PRD 七大 FR 域分包（TECH_DESIGN §2.2.2）。

路由层薄、服务层厚：业务规则一律在此；FR 编号与包可互相直查。
状态流转必须过 engine（硬约束 2）；AI 产出只经 ai/pipeline（硬约束 3）。
"""
