APP_ID = "skill_upgrade"
APP_NAME = "角色技能提升"
DEFAULT_GROUP = True
PRIORITY = 650  # 体力刷本 600 之后、活跃度奖励 700 之前
DEFAULT_GROUP_AFTER = "charge_plan"  # 新加入一条龙列表时放在体力刷本后面：生成步骤时按体力计划的完成次数估算当晚刷到的材料，真升不升以强化弹窗为准
NEED_NOTIFY = False
