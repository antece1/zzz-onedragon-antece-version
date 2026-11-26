from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from one_dragon_qt.utils.config_utils import get_prop_adapter
from one_dragon_qt.widgets.app_setting.app_setting_flyout import AppSettingFlyout
from one_dragon_qt.widgets.setting_card.spin_box_setting_card import SpinBoxSettingCard
from one_dragon_qt.widgets.setting_card.switch_setting_card import SwitchSettingCard
from zzz_od.application.skill_upgrade import skill_upgrade_const


class SkillUpgradeSettingFlyout(AppSettingFlyout):
    """角色技能提升配置弹出框"""

    def _setup_ui(self, layout: QVBoxLayout) -> None:
        self.apply_opt = SwitchSettingCard(
            icon='',
            title='真的升级',
            content='关闭时只演练：进技能页读等级和强化花费，不点「确定」，不花材料和丁尼。'
                    '开启后按「角色培养」里计入的代理人（★ 优先）和培养目标，真的强化技能',
            margins=self.card_margins,
        )
        layout.addWidget(self.apply_opt)

        self.dennies_reserve_opt = SpinBoxSettingCard(
            icon='',
            title='丁尼保留',
            content='升完一级后剩下的丁尼不能少于这个数，到线就停',
            step=10000,
            minimum=0,
            maximum=99999999,
            margins=self.card_margins,
        )
        layout.addWidget(self.dennies_reserve_opt)

        self.max_steps_opt = SpinBoxSettingCard(
            icon='',
            title='每次最多升几级',
            content='一个技能升 1 级算一步',
            minimum=1,
            maximum=60,
            margins=self.card_margins,
        )
        layout.addWidget(self.max_steps_opt)

        self.research_first_opt = SwitchSettingCard(
            icon='',
            title='先到攻略建议等级',
            content='先把各技能升到攻略建议等级，再按培养目标继续（★ 代理人先，然后其余代理人；同一组里所有人都先到攻略建议）；'
                    '关闭时直接按培养目标',
            margins=self.card_margins,
        )
        layout.addWidget(self.research_first_opt)

        self.auto_level_opt = SwitchSettingCard(
            icon='',
            title='等级不够时先升代理人',
            content='技能下一级要的代理人等级不够（例如 10 级要 55 级）时，先用调查员记录把代理人升到够用的等级，'
                    '再升技能；要突破时会停下。关闭时把这个技能记成卡住，先升别的',
            margins=self.card_margins,
        )
        layout.addWidget(self.auto_level_opt)

        self.convert_materials_opt = SwitchSettingCard(
            icon='',
            title='材料不够时自动转化',
            content='芯片不够、游戏提供「转化获取」时，先用低一档芯片按 3 换 1 转化（约每个 300 丁尼）再升级；'
                    '会核对芯片名、数量和丁尼保留线，对不上就取消。演练时只打开转化弹窗看一眼再取消。'
                    '关闭时芯片不够就跳过并记进刷取队列',
            margins=self.card_margins,
        )
        layout.addWidget(self.convert_materials_opt)

        self.level_allow_overshoot_opt = SwitchSettingCard(
            icon='',
            title='升代理人时允许多用经验',
            content='游戏的「自动添加」常常把经验加到上限：关闭时（默认）加得比这一步要的多就不点升级、跳过；'
                    '打开后允许一直升到培养目标（不超过当前上限）',
            margins=self.card_margins,
        )
        layout.addWidget(self.level_allow_overshoot_opt)

    def init_config(self) -> None:
        config = self.ctx.run_context.get_config(
            app_id=skill_upgrade_const.APP_ID,
            instance_idx=self.ctx.current_instance_idx,
            group_id=self.group_id,
        )
        self.apply_opt.init_with_adapter(get_prop_adapter(config, 'apply'))
        self.dennies_reserve_opt.init_with_adapter(get_prop_adapter(config, 'dennies_reserve'))
        self.max_steps_opt.init_with_adapter(get_prop_adapter(config, 'max_steps'))
        self.research_first_opt.init_with_adapter(get_prop_adapter(config, 'research_first'))
        self.auto_level_opt.init_with_adapter(get_prop_adapter(config, 'auto_level'))
        self.convert_materials_opt.init_with_adapter(get_prop_adapter(config, 'convert_materials'))
        self.level_allow_overshoot_opt.init_with_adapter(get_prop_adapter(config, 'level_allow_overshoot'))
