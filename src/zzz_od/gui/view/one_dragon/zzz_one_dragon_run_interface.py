from pathlib import Path

from PySide6.QtWidgets import QWidget
from qfluentwidgets import SettingCardGroup

from one_dragon.utils import os_utils
from one_dragon.utils.task_scheduler_utils import TaskScheduler
from one_dragon_qt.view.one_dragon.one_dragon_run_interface import OneDragonRunInterface
from zzz_od.config.auto_start_config import AutoStartConfig
from zzz_od.context.zzz_context import ZContext
from zzz_od.gui.view.one_dragon.auto_start_setting_card import AutoStartSettingCard
from zzz_od.utils.auto_start_utils import AutoStartService


class ZOneDragonRunInterface(OneDragonRunInterface):

    def __init__(self, ctx: ZContext, parent=None):
        self.ctx: ZContext = ctx
        self.auto_start_card: AutoStartSettingCard | None = None
        OneDragonRunInterface.__init__(
            self,
            ctx=ctx,
            parent=parent,
            help_url='https://one-dragon.com/zzz/zh/feat/feat_one_dragon/onedragon.html',
        )

    def get_widget_at_top(self) -> QWidget:
        """在「运行设置」最后、「结束后」下面加「每天定时运行一条龙」卡片"""
        top = OneDragonRunInterface.get_widget_at_top(self)
        run_group = self.after_done_opt.parent()
        self.auto_start_card = AutoStartSettingCard(self._create_auto_start_service, parent=run_group)
        if isinstance(run_group, SettingCardGroup):
            run_group.addSettingCard(self.auto_start_card)
        return top

    def on_interface_shown(self) -> None:
        OneDragonRunInterface.on_interface_shown(self)
        if self.auto_start_card is not None:
            self.auto_start_card.refresh()

    def _create_auto_start_service(self) -> AutoStartService:
        """每次都新建，改了 config/auto_start.yml 不用重启就生效"""
        return AutoStartService(
            config=AutoStartConfig(),
            scheduler=TaskScheduler(),
            root=Path(os_utils.get_work_dir()),
        )
