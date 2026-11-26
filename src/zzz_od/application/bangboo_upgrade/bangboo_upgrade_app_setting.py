from one_dragon_qt.services.app_setting.app_setting_provider import (
    AppSettingProvider,
    SettingType,
)
from zzz_od.application.bangboo_upgrade.bangboo_upgrade_const import APP_ID


class BangbooUpgradeAppSetting(AppSettingProvider):
    app_id = APP_ID
    setting_type = SettingType.FLYOUT

    @staticmethod
    def get_setting_cls() -> type:
        from zzz_od.gui.app_setting.bangboo_upgrade_setting_flyout import (
            BangbooUpgradeSettingFlyout,
        )

        return BangbooUpgradeSettingFlyout
