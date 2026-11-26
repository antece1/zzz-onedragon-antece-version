from __future__ import annotations

import contextlib

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryToolButton,
    PushButton,
    SettingCardGroup,
    SubtitleLabel,
    ToolButton,
)

from one_dragon.base.config.one_dragon_config import (
    AfterDoneOpEnum,
    InstanceRun,
)
from one_dragon.base.operation.application import application_const
from one_dragon.base.operation.application.application_group_config import (
    ApplicationGroupConfig,
    ApplicationGroupConfigItem,
)
from one_dragon.base.operation.application_base import ApplicationEventId
from one_dragon.base.operation.application_run_record import AppRunRecord
from one_dragon.base.operation.one_dragon_context import (
    ContextInstanceEventEnum,
    OneDragonContext,
)
from one_dragon.base.operation.one_dragon_finalizer import (
    AfterDoneRequest,
    execute_after_done,
)
from one_dragon.utils import cmd_utils
from one_dragon.utils.i18_utils import gt
from one_dragon.utils.log_utils import log
from one_dragon_qt.view.app_run_interface import SplitAppRunInterface
from one_dragon_qt.view.context_event_signal import ContextEventSignal
from one_dragon_qt.widgets.app_run_list import AppRunList
from one_dragon_qt.widgets.app_select_dialog import AppSelectDialog
from one_dragon_qt.widgets.column import Column
from one_dragon_qt.widgets.setting_card.combo_box_setting_card import (
    ComboBoxSettingCard,
)
from one_dragon_qt.widgets.setting_card.help_card import HelpCard
from one_dragon_qt.widgets.setting_card.switch_setting_card import SwitchSettingCard
from one_dragon_qt.windows.main_app_window_base import MainAppWindowBase


class OneDragonRunInterface(SplitAppRunInterface):

    run_all_apps_signal = Signal()

    def __init__(self, ctx: OneDragonContext,
                 nav_text_cn: str = '一条龙运行',
                 object_name: str = 'one_dragon_run_interface',
                 need_multiple_instance: bool = True,
                 help_url: str | None = None, parent=None):
        self.config: ApplicationGroupConfig | None = None
        self._context_event_signal = ContextEventSignal()
        self.help_url: str = help_url
        self.need_multiple_instance: bool = need_multiple_instance
        self._runner_finished_connected: bool = False

        SplitAppRunInterface.__init__(
            self,
            ctx=ctx,
            app_id=application_const.ONE_DRAGON_APP_ID,
            object_name=object_name,
            nav_text_cn=nav_text_cn,
            parent=parent,
        )

    def get_left_widget(self) -> QWidget:
        self.app_run_list = AppRunList(self.ctx, enable_task_actions=True)
        self.app_run_list.app_list_changed.connect(self._on_app_list_changed)
        self.app_run_list.app_run_clicked.connect(self._on_app_card_run)
        self.app_run_list.app_switch_changed.connect(self.on_app_switch_run)
        self.app_run_list.app_setting_clicked.connect(self.on_app_setting_clicked)
        self.app_run_list.app_notify_clicked.connect(self.on_app_notify_clicked)
        self.app_run_list.app_add_clicked.connect(self._on_app_add_clicked)
        self.app_run_list.app_remove_clicked.connect(self._on_app_remove_clicked)
        self.app_run_list.app_run_from_clicked.connect(self._on_app_run_from_clicked)
        self.app_run_list.app_clear_mark_clicked.connect(self._on_app_clear_mark_clicked)
        return self.app_run_list

    def get_left_header(self) -> QWidget:
        """任务列表标题栏：开始运行一条龙、新增任务"""
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        layout.addWidget(SubtitleLabel(gt('任务列表')))
        layout.addStretch(1)

        self.task_start_btn = PrimaryToolButton(FluentIcon.PLAY)
        self.task_start_btn.setToolTip(gt('按顺序运行任务列表里打开的任务'))
        self.task_start_btn.clicked.connect(self._on_start_clicked)
        layout.addWidget(self.task_start_btn)

        self.task_add_btn = ToolButton(FluentIcon.ADD)
        self.task_add_btn.setToolTip(gt('新增任务'))
        self.task_add_btn.clicked.connect(self._on_app_add_clicked)
        layout.addWidget(self.task_add_btn)

        return header

    def get_widget_at_top(self) -> QWidget:
        top = Column()

        run_group = SettingCardGroup(gt('运行设置'))
        top.add_widget(run_group)

        if self.help_url is not None:
            self.help_opt = HelpCard(url=self.help_url)
            run_group.addSettingCard(self.help_opt)

        self.notify_switch = SwitchSettingCard(icon=FluentIcon.INFO, title='应用通知')
        self.notify_btn = PushButton(text=gt('设置'), icon=FluentIcon.SETTING)
        self.notify_btn.clicked.connect(self._on_notify_setting_clicked)
        self.notify_switch.hBoxLayout.addWidget(self.notify_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.notify_switch.hBoxLayout.addSpacing(16)
        run_group.addSettingCard(self.notify_switch)

        self.instance_run_opt = ComboBoxSettingCard(icon=FluentIcon.PEOPLE, title='运行实例',
                                                    options_enum=InstanceRun)
        self.instance_run_opt.value_changed.connect(self._on_instance_run_changed)
        run_group.addSettingCard(self.instance_run_opt)

        self.after_done_opt = ComboBoxSettingCard(icon=FluentIcon.CALENDAR, title='结束后',
                                                  options_enum=AfterDoneOpEnum)
        self.after_done_opt.value_changed.connect(self._on_after_done_changed)
        run_group.addSettingCard(self.after_done_opt)

        return top

    def _init_app_list(self) -> None:
        """
        初始化应用列表
        :return:
        """
        self.app_run_list.set_app_list(
            app_list=self.config.app_list,
            instance_idx=self.ctx.current_instance_idx,
            removable_app_ids=self._removable_app_ids(),
        )
        self._apply_task_actions_lock()

    def _removable_app_ids(self) -> set[str]:
        """能删除的任务：手动新增的、并且现在不是默认一条龙应用（应用更新后可能变成默认应用）"""
        if self.config is None:
            return set()
        return set(self.config.added_app_ids) - set(self.ctx.run_context.default_group_apps)

    def _is_task_list_locked(self) -> bool:
        """一条龙运行中（含暂停）不允许 新增 / 删除 / 从此执行 / 清除标记"""
        app_runner = getattr(self, 'app_runner', None)  # 右侧运行控件建好之前还没有
        return not self.ctx.run_context.is_context_stop or (app_runner is not None and app_runner.isRunning())

    def _apply_task_actions_lock(self) -> None:
        """按运行状态锁住 / 解锁任务列表操作"""
        enabled = not self._is_task_list_locked()
        for card in self.app_run_list._app_cards:
            card.set_task_actions_enabled(enabled)
        if hasattr(self, 'task_add_btn'):
            self.task_add_btn.setEnabled(enabled)

    def _refresh_app_config(self) -> None:
        self.config = self.ctx.app_group_manager.get_one_dragon_group_config(
            instance_idx=self.ctx.current_instance_idx,
        )
        self._init_app_list()

    def on_interface_shown(self) -> None:
        SplitAppRunInterface.on_interface_shown(self)
        if not self._runner_finished_connected:
            self.app_runner.finished.connect(self._on_app_runner_finished)
            self._runner_finished_connected = True
        self._refresh_app_config()
        self.notify_switch.init_with_adapter(self.ctx.notify_config.get_prop_adapter('enable_notify'))

        self.ctx.listen_event(ApplicationEventId.APPLICATION_START.value, self._on_app_state_changed)
        self.ctx.listen_event(ApplicationEventId.APPLICATION_STOP.value, self._on_app_state_changed)
        self.ctx.listen_event(ContextInstanceEventEnum.instance_active.value, self._on_instance_event)

        self.instance_run_opt.blockSignals(True)
        self.instance_run_opt.setValue(self.ctx.one_dragon_config.instance_run)
        self.instance_run_opt.setVisible(self.need_multiple_instance)
        self.instance_run_opt.blockSignals(False)

        self.after_done_opt.setValue(self.ctx.one_dragon_config.after_done)

        self._context_event_signal.instance_changed.connect(self._on_instance_changed)
        self.run_all_apps_signal.connect(self.run_app)

        if self.ctx.signal.start_onedragon:
            self.ctx.signal.start_onedragon = False
            self.run_all_apps_signal.emit()

        self._update_setting_btn_visibility()

        # AppSettingManager 可能尚未就绪，监听信号以在就绪后刷新
        window = self.window()
        if isinstance(window, MainAppWindowBase):
            window.app_setting_manager.ready.connect(self._on_app_setting_manager_ready)

    def on_interface_hidden(self) -> None:
        SplitAppRunInterface.on_interface_hidden(self)
        with contextlib.suppress(RuntimeError):
            self._context_event_signal.instance_changed.disconnect(self._on_instance_changed)
        window = self.window()
        if isinstance(window, MainAppWindowBase):
            with contextlib.suppress(RuntimeError):
                window.app_setting_manager.ready.disconnect(self._on_app_setting_manager_ready)

    def _on_after_done_changed(self, idx: int, value: str) -> None:
        """
        结束后的操作
        :param value:
        :return:
        """
        self.ctx.one_dragon_config.after_done = value
        if value != AfterDoneOpEnum.SHUTDOWN.value.value:
            log.info('已取消关机计划')
            cmd_utils.cancel_shutdown_sys()

    def _on_app_runner_finished(self) -> None:
        # 「从此执行」只对这一次运行有效；没走到应用组（比如进游戏失败）也要清掉，免得下次正常运行也从中间开始
        self.ctx.run_context.group_start = None
        self._apply_task_actions_lock()
        if self.app_runner.app_id != self.app_id:
            return
        after_done = self.ctx.one_dragon_config.after_done
        if after_done == AfterDoneOpEnum.NONE.value.value:
            return
        execute_after_done(
            self.ctx,
            self.app_runner.run_result,
            AfterDoneRequest(
                close_game=after_done == AfterDoneOpEnum.CLOSE_GAME.value.value,
                shutdown_seconds=(
                    60 if after_done == AfterDoneOpEnum.SHUTDOWN.value.value else None
                ),
                hibernate=after_done == AfterDoneOpEnum.HIBERNATE.value.value,
                sleep=after_done == AfterDoneOpEnum.SLEEP.value.value,
            ),
        )

    def run_app(self) -> None:
        if self.app_runner.isRunning():
            log.error('已有应用在运行中')
            return
        self.app_runner.app_id = self.app_id
        self.app_runner.start()

    def run_app_by_item(self, app: ApplicationGroupConfigItem) -> None:
        if self.app_runner.isRunning():
            log.error('已有应用在运行中')
            return
        self.app_runner.app_id = app.app_id
        self.app_runner.start()

    def on_context_state_changed(self) -> None:
        SplitAppRunInterface.on_context_state_changed(self)
        self.app_run_list.update_cards_display()
        self._apply_task_actions_lock()
        if not hasattr(self, 'task_start_btn'):
            return
        if self.ctx.run_context.is_context_running:
            self.task_start_btn.setIcon(FluentIcon.PAUSE)
            self.task_start_btn.setToolTip(gt('暂停'))
        else:
            self.task_start_btn.setIcon(FluentIcon.PLAY)
            self.task_start_btn.setToolTip(gt('按顺序运行任务列表里打开的任务'))

    def _show_task_tip(self, text: str, success: bool = True) -> None:
        """在页面右上角提示任务列表操作的结果"""
        bar = InfoBar.success if success else InfoBar.warning
        bar(title=text, content='', orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP_RIGHT, duration=3000, parent=self)

    def _on_app_add_clicked(self) -> None:
        """新增任务：从还不在列表里的应用中选一个加到末尾"""
        if self.config is None:
            return
        if self._is_task_list_locked():
            self._show_task_tip(gt('正在运行，先停止再新增任务'), success=False)
            return
        existing = {item.app_id for item in self.config.app_list}
        candidates: list[tuple[str, str]] = [
            (app_id, self.ctx.run_context.get_application_name(app_id))
            for app_id in self.ctx.run_context.standalone_apps
            if app_id not in existing
        ]
        if len(candidates) == 0:
            self._show_task_tip(gt('所有应用都已经在任务列表里了'), success=False)
            return

        dialog = AppSelectDialog(candidates, parent=self.window())
        if not dialog.exec():
            return
        app_id = dialog.selected_app_id
        if app_id is None or self._is_task_list_locked():  # 对话框开着时可能用快捷键开始了运行
            return
        item = self.config.add_app(app_id)
        if item is None:
            return
        item.app_name = self.ctx.run_context.get_application_name(app_id)
        self._init_app_list()
        self._update_setting_btn_visibility()
        self._show_task_tip(f"{gt('已新增')} {gt(item.app_name)}")

    def _on_app_remove_clicked(self, app_id: str) -> None:
        """删除手动新增的任务（应用本身和它的设置都还在，可以再新增回来）"""
        if self.config is None or app_id not in self._removable_app_ids():
            return
        if self._is_task_list_locked():
            self._show_task_tip(gt('正在运行，先停止再删除任务'), success=False)
            return
        app_name = self.ctx.run_context.get_application_name(app_id)
        self.config.remove_added_app(app_id)
        self._init_app_list()
        self._update_setting_btn_visibility()
        self._show_task_tip(f"{gt('已删除')} {gt(app_name)}")

    def _on_app_run_from_clicked(self, app_id: str) -> None:
        """从此执行：从这个任务开始按顺序运行一条龙"""
        if self._is_task_list_locked():
            self._show_task_tip(gt('正在运行，先停止再从此执行'), success=False)
            return

        # 运行实例选「全部」、当前账号又没开「一条龙中运行」时，一条龙会先切到别的账号，
        # 从此执行就对不上界面上这个列表了，先提示
        current = self.ctx.one_dragon_config.current_active_instance
        od_list = self.ctx.one_dragon_config.instance_list_in_od
        if (
            self.ctx.one_dragon_config.instance_run == InstanceRun.ALL.value.value
            and len(od_list) > 0
            and current is not None
            and all(instance.idx != current.idx for instance in od_list)
        ):
            self._show_task_tip(gt('当前账号没开「一条龙中运行」，从此执行会跑到别的账号，请先打开'), success=False)
            return

        self.ctx.run_context.group_start = (self.ctx.current_instance_idx, app_id)
        self.run_app()

    def _on_app_clear_mark_clicked(self, app_id: str) -> None:
        """
        清除标记：把今天的完成标记改回未运行，下次一条龙会重新运行这个任务。
        只改完成状态，不清应用自己记的进度（如恶名狩猎剩余次数、世界巡逻已完成路线）。
        """
        if self._is_task_list_locked():
            self._show_task_tip(gt('正在运行，先停止再清除标记'), success=False)
            return
        run_record = self.ctx.run_context.get_run_record(
            app_id=app_id,
            instance_idx=self.ctx.current_instance_idx,
        )
        app_name = self.ctx.run_context.get_application_name(app_id)
        if run_record is None:
            self._show_task_tip(f"{gt(app_name)} {gt('没有完成标记，每次都会运行')}", success=False)
            return
        run_record.update_status(AppRunRecord.STATUS_WAIT, only_status=True)
        self.app_run_list.update_cards_display()

        # 有些任务按自己的进度判断完成（如恶名狩猎本周次数用完、式舆防卫战满星），清了状态也还算完成
        if run_record.is_done or run_record.run_status_under_now == AppRunRecord.STATUS_SUCCESS:
            self._show_task_tip(
                f"{gt(app_name)} {gt('按自己的进度判断完成，清除不了；要强制运行请用「从此执行」')}",
                success=False,
            )
            return
        self._show_task_tip(f"{gt('已清除标记')} {gt(app_name)}")

    def _on_app_state_changed(self, event) -> None:
        self.app_run_list.update_cards_display()

    def _on_app_list_changed(self, new_app_list: list[ApplicationGroupConfigItem]) -> None:
        """
        应用列表改变后的回调（拖拽排序或置顶）

        Args:
            new_app_list: 新顺序的应用列表
        """
        # 更新配置中的 app_list 顺序；用户主动调整顺序时一并保存临时应用
        self.config.set_app_order([item.app_id for item in new_app_list])

    def _on_app_card_run(self, app_id: str) -> None:
        """
        运行某个特殊的应用
        :param app_id:
        :return:
        """
        for app in self.config.app_list:
            if app.app_id == app_id:
                self.config.persist_app(app_id)
                self.run_app_by_item(app)
                break

    def on_app_switch_run(self, app_id: str, value: bool) -> None:
        """
        应用运行状态切换
        :param app_id:
        :param value:
        :return:
        """
        self.ctx.app_group_manager.set_one_dragon_app_enable(
            config=self.config,
            app_id=app_id,
            enabled=value,
        )

    def _on_instance_event(self, event) -> None:
        """
        实例变更 这是context的事件 不能改UI
        :return:
        """
        self._context_event_signal.instance_changed.emit()

    def _on_instance_changed(self) -> None:
        """
        实例变更 这是signal 可以改ui
        :return:
        """
        self._refresh_app_config()

    def _on_app_setting_manager_ready(self) -> None:
        self.ctx.app_group_manager.clear_config_cache()
        self._refresh_app_config()
        self._update_setting_btn_visibility()

    def _on_instance_run_changed(self, idx: int, value: str) -> None:
        self.ctx.one_dragon_config.instance_run = value

    def _init_notify_switch(self) -> None:
        pass

    def _on_notify_setting_clicked(self) -> None:
        """处理通知设置按钮被点击，委托给 app_setting_manager。"""
        window = self.window()
        if not isinstance(window, MainAppWindowBase):
            return
        window.app_setting_manager.show_notify_setting(parent=self)

    def on_app_setting_clicked(self, app_id: str) -> None:
        """处理应用设置按钮被点击，委托给 app_setting_manager"""
        window = self.window()
        if not isinstance(window, MainAppWindowBase):
            return
        target = self._find_app_card_setting_btn(app_id)
        if target is None:
            return
        window.app_setting_manager.show_app_setting(
            app_id=app_id,
            parent=self,
            group_id=application_const.DEFAULT_GROUP_ID,
            target=target,
        )

    def on_app_notify_clicked(self, app_id: str) -> None:
        """处理应用通知设置按钮被点击，委托给 app_setting_manager。"""
        window = self.window()
        if not isinstance(window, MainAppWindowBase):
            return
        target = self._find_app_card_notify_btn(app_id)
        if target is None:
            return
        window.app_setting_manager.show_app_notify_setting(
            app_id=app_id,
            parent=self.window(),
            target=target,
        )

    def _update_setting_btn_visibility(self) -> None:
        """根据 app_setting_manager 的注册信息，显示或隐藏卡片的设置按钮"""
        window = self.window()
        if not isinstance(window, MainAppWindowBase):
            return
        settable = window.app_setting_manager.settable_app_ids
        for card in self.app_run_list._app_cards:
            card.setting_btn.setVisible(card.app.app_id in settable)
            card.set_notify_visible(card.app.app_id in self.ctx.notify_config.app_map)

    def _find_app_card_setting_btn(self, app_id: str):
        """找到对应 app_id 的卡片的设置按钮"""
        for card in self.app_run_list._app_cards:
            if card.app.app_id == app_id:
                return card.setting_btn
        return None

    def _find_app_card_notify_btn(self, app_id: str):
        """找到对应 app_id 的卡片的通知设置按钮"""
        for card in self.app_run_list._app_cards:
            if card.app.app_id == app_id:
                return card.more_btn
        return None
