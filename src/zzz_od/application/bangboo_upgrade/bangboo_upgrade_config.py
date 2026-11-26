from one_dragon.base.operation.application.application_config import ApplicationConfig


class BangbooUpgradeConfig(ApplicationConfig):

    def __init__(self, instance_idx: int, group_id: str) -> None:
        ApplicationConfig.__init__(
            self,
            app_id='bangboo_upgrade',
            instance_idx=instance_idx,
            group_id=group_id,
        )

    @property
    def apply(self) -> bool:
        """真的升级。关闭时只演练：传送、进邦布界面、找红点、打开升级窗口看一眼就关，不花材料和丁尼"""
        return self.get('apply', False)

    @apply.setter
    def apply(self, new_value: bool) -> None:
        self.update('apply', new_value)

    @property
    def count(self) -> int:
        """处理最新的几个带红点邦布（列表里最后几个带红点的）"""
        return int(self.get('count', 2))

    @count.setter
    def count(self, new_value: int) -> None:
        self.update('count', int(new_value))

    @property
    def target_level(self) -> int:
        """升到多少级为止"""
        return int(self.get('target_level', 60))

    @target_level.setter
    def target_level(self, new_value: int) -> None:
        self.update('target_level', int(new_value))

    @property
    def optimize(self) -> bool:
        """到等级上限时做系统优化（突破，花丁尼）。关闭时到上限就停"""
        return self.get('optimize', True)

    @optimize.setter
    def optimize(self, new_value: bool) -> None:
        self.update('optimize', new_value)

    @property
    def max_dennies(self) -> int:
        """这次运行做系统优化最多花多少丁尼，超了就停"""
        return int(self.get('max_dennies', 60000))

    @max_dennies.setter
    def max_dennies(self, new_value: int) -> None:
        self.update('max_dennies', int(new_value))
