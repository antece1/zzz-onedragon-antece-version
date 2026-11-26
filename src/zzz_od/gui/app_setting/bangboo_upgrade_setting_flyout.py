from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from one_dragon_qt.utils.config_utils import get_prop_adapter
from one_dragon_qt.widgets.app_setting.app_setting_flyout import AppSettingFlyout
from one_dragon_qt.widgets.setting_card.switch_setting_card import SwitchSettingCard
from one_dragon_qt.widgets.setting_card.text_setting_card import TextSettingCard
from zzz_od.application.bangboo_upgrade import bangboo_upgrade_const


class BangbooUpgradeSettingFlyout(AppSettingFlyout):
    """邦布升级配置弹出框"""

    def _setup_ui(self, layout: QVBoxLayout) -> None:
        self.count_opt = TextSettingCard(icon='', title='处理几个邦布',
                                         content='列表里最后几个带红点的（最新的）', input_placeholder='2',
                                         margins=self.card_margins)
        layout.addWidget(self.count_opt)

        self.target_level_opt = TextSettingCard(icon='', title='目标等级', content='升到多少级为止',
                                                input_placeholder='60', margins=self.card_margins)
        layout.addWidget(self.target_level_opt)

        self.optimize_opt = SwitchSettingCard(icon='', title='做系统优化',
                                              content='到等级上限时花丁尼突破；关闭则到上限就停',
                                              margins=self.card_margins)
        layout.addWidget(self.optimize_opt)

        self.max_dennies_opt = TextSettingCard(icon='', title='丁尼上限',
                                               content='这次运行做系统优化最多花多少丁尼',
                                               input_placeholder='60000', margins=self.card_margins)
        layout.addWidget(self.max_dennies_opt)

        self.apply_opt = SwitchSettingCard(icon='', title='真的升级',
                                           content='关闭时只演练：打开升级窗口看一眼就关，不花材料和丁尼',
                                           margins=self.card_margins)
        layout.addWidget(self.apply_opt)

    def init_config(self) -> None:
        config = self.ctx.run_context.get_config(
            app_id=bangboo_upgrade_const.APP_ID,
            instance_idx=self.ctx.current_instance_idx,
            group_id=self.group_id,
        )
        self.count_opt.init_with_adapter(get_prop_adapter(config, 'count', getter_convert='str', setter_convert='int'))
        self.target_level_opt.init_with_adapter(get_prop_adapter(config, 'target_level', getter_convert='str', setter_convert='int'))
        self.optimize_opt.init_with_adapter(get_prop_adapter(config, 'optimize'))
        self.max_dennies_opt.init_with_adapter(get_prop_adapter(config, 'max_dennies', getter_convert='str', setter_convert='int'))
        self.apply_opt.init_with_adapter(get_prop_adapter(config, 'apply'))
