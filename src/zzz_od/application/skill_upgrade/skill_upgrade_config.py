from one_dragon.base.operation.application.application_config import ApplicationConfig
from zzz_od.application.skill_upgrade import skill_upgrade_const


class SkillUpgradeConfig(ApplicationConfig):

    def __init__(self, instance_idx: int, group_id: str) -> None:
        ApplicationConfig.__init__(
            self,
            app_id=skill_upgrade_const.APP_ID,
            instance_idx=instance_idx,
            group_id=group_id,
        )

    @property
    def apply(self) -> bool:
        """真的升级。关闭时只演练：进技能页、读等级和强化弹窗里的花费，不点「确定」，不花材料和丁尼"""
        return self.get('apply', False)

    @apply.setter
    def apply(self, new_value: bool) -> None:
        self.update('apply', new_value)

    @property
    def dennies_reserve(self) -> int:
        """丁尼保留线：升完一级后剩下的丁尼不能少于这个数"""
        return int(self.get('dennies_reserve', 100000))

    @dennies_reserve.setter
    def dennies_reserve(self, new_value: int) -> None:
        self.update('dennies_reserve', int(new_value))

    @property
    def max_steps(self) -> int:
        """每次最多升几级（一个技能升 1 级算一步）"""
        return int(self.get('max_steps', 10))

    @max_steps.setter
    def max_steps(self, new_value: int) -> None:
        self.update('max_steps', int(new_value))

    @property
    def research_first(self) -> bool:
        """先把各技能升到攻略建议等级，再按培养目标继续"""
        return self.get('research_first', True)

    @research_first.setter
    def research_first(self, new_value: bool) -> None:
        self.update('research_first', new_value)

    @property
    def auto_level(self) -> bool:
        """技能下一级要的代理人等级不够时，先把代理人升到够用的等级（用调查员记录，不做突破）；关闭时把这个技能记成卡住"""
        return self.get('auto_level', True)

    @auto_level.setter
    def auto_level(self, new_value: bool) -> None:
        self.update('auto_level', new_value)

    @property
    def level_allow_overshoot(self) -> bool:
        """
        升级代理人时允许「自动添加」用掉超过这一步预算的经验（最多升到培养目标和当前上限）。
        关闭时（默认）自动添加加得比这一步要的多（多于预计经验 + 一个资深调查员记录）就不点升级、跳过
        """
        return self.get('level_allow_overshoot', False)

    @level_allow_overshoot.setter
    def level_allow_overshoot(self, new_value: bool) -> None:
        self.update('level_allow_overshoot', new_value)

    @property
    def convert_materials(self) -> bool:
        """
        材料不够时自动转化：强化弹窗芯片不够、游戏给出「转化获取」时，用低一档芯片 3 换 1（花少量丁尼）补上再升级。
        演练时只打开转化弹窗看一眼再取消；关闭时芯片不够就跳过并记进刷取队列
        """
        return self.get('convert_materials', True)

    @convert_materials.setter
    def convert_materials(self, new_value: bool) -> None:
        self.update('convert_materials', new_value)
