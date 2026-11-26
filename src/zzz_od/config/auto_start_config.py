from one_dragon.base.config.yaml_config import YamlConfig


class AutoStartConfig(YamlConfig):
    """
    每天定时运行一条龙的配置（config/auto_start.yml，整台电脑共用，不分账号实例）。
    只记要管理哪些 Windows 计划任务；运行时间和开关以 Windows 计划任务为准，不存在这里。
    界面不会写这些值，需要时手动编辑文件；改完后重新打开「一条龙运行」页面或再操作一次卡片就会生效。
    """

    def __init__(self, is_mock: bool = False) -> None:
        YamlConfig.__init__(self, 'auto_start', is_mock=is_mock)

    @property
    def run_task_name(self) -> str:
        """
        运行一条龙的计划任务名，可以带文件夹，例如 '\\我的任务\\一条龙'。
        留空表示自动查找；填了就只管这个任务，找不到也不会新建。
        """
        return str(self.get('run_task_name', '') or '').strip()

    @run_task_name.setter
    def run_task_name(self, value: str) -> None:
        self.update('run_task_name', value)

    @property
    def sync_task_name(self) -> str:
        """
        米游社数据同步的计划任务名。留空表示自动查找已知的同步任务；填 '-' 表示不管同步任务。
        同步任务只跟着一条龙的时间提前 5 分钟，开关不动。
        """
        return str(self.get('sync_task_name', '') or '').strip()

    @sync_task_name.setter
    def sync_task_name(self, value: str) -> None:
        self.update('sync_task_name', value)
