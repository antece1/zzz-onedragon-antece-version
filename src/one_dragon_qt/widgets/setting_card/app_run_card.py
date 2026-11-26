from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QWidget
from qfluentwidgets import (
    Action,
    FluentIcon,
    FluentThemeColor,
    RoundMenu,
    SwitchButton,
    TransparentToolButton,
)

from one_dragon.base.operation.application.application_group_config import (
    ApplicationGroupConfigItem,
)
from one_dragon.base.operation.application_run_record import AppRunRecord
from one_dragon.utils.i18_utils import gt
from one_dragon_qt.widgets.draggable_list import DraggableListItem
from one_dragon_qt.widgets.setting_card.multi_push_setting_card import (
    MultiPushSettingCard,
)


class AppRunCard(DraggableListItem):

    move_top = Signal(str)  # 置顶功能，用于拖拽不能处理滚动的场景
    run = Signal(str)
    switched = Signal(str, bool)
    setting_clicked = Signal(str)
    notify_clicked = Signal(str)
    add_clicked = Signal()  # 任务列表菜单：新增
    remove_clicked = Signal(str)  # 任务列表菜单：删除
    run_from_clicked = Signal(str)  # 任务列表菜单：从此执行
    clear_mark_clicked = Signal(str)  # 任务列表菜单：清除标记

    def __init__(
        self,
        app: ApplicationGroupConfigItem,
        index: int = 0,
        run_record: AppRunRecord | None = None,
        switch_on: bool = False,
        parent: QWidget | None = None,
        enable_opacity_effect: bool = True,
    ):
        self.app: ApplicationGroupConfigItem = app
        self.run_record: AppRunRecord | None = run_record

        self.setting_btn = TransparentToolButton(FluentIcon.SETTING, None)
        self.setting_btn.setToolTip(gt('应用设置'))
        self.setting_btn.clicked.connect(self._on_setting_clicked)

        self.more_btn = TransparentToolButton(FluentIcon.MORE, None)
        self.more_btn.setToolTip(gt('更多'))
        self.more_btn.clicked.connect(self._show_more_menu)

        self.more_menu = RoundMenu()
        self.notify_action = Action(FluentIcon.MESSAGE, gt('通知设置'), self.more_menu)
        self.notify_action.triggered.connect(lambda _checked=False: self._on_notify_clicked())
        self.move_top_action = Action(FluentIcon.PIN, gt('移到顶部'), self.more_menu)
        self.move_top_action.triggered.connect(lambda _checked=False: self._on_move_top_clicked())
        self.more_menu.addAction(self.notify_action)
        self.more_menu.addAction(self.move_top_action)

        # 任务列表菜单（一条龙页面用 enable_task_actions 打开，其他地方的列表不显示）
        self._task_actions_enabled: bool = False
        self.add_action = Action(FluentIcon.ADD, gt('新增'), self.more_menu)
        self.add_action.triggered.connect(lambda _checked=False: self.add_clicked.emit())
        self.remove_action = Action(FluentIcon.DELETE, gt('删除'), self.more_menu)
        self.remove_action.triggered.connect(lambda _checked=False: self.remove_clicked.emit(self.app.app_id))
        self.run_from_action = Action(FluentIcon.PLAY, gt('从此执行'), self.more_menu)
        self.run_from_action.setToolTip(gt('从这个任务开始按顺序运行一条龙，这个任务不管开关和完成标记都会运行'))
        self.run_from_action.triggered.connect(lambda _checked=False: self.run_from_clicked.emit(self.app.app_id))
        self.clear_mark_action = Action(FluentIcon.BROOM, gt('清除标记'), self.more_menu)
        self.clear_mark_action.setToolTip(gt('清除今天的完成标记，下次一条龙会重新运行这个任务'))
        self.clear_mark_action.triggered.connect(
            lambda _checked=False: self.clear_mark_clicked.emit(self.app.app_id)
        )

        self.run_btn = TransparentToolButton(FluentIcon.PLAY, None)
        self.run_btn.setToolTip(gt('运行'))
        self.run_btn.clicked.connect(self._on_run_clicked)

        self.switch_btn = SwitchButton()
        self.switch_btn.setOnText('')
        self.switch_btn.setOffText('')
        self.switch_btn.setChecked(switch_on)
        self.switch_btn.checkedChanged.connect(self._on_switch_changed)

        # 创建 MultiPushSettingCard 作为 content_widget
        content_widget = MultiPushSettingCard(
            btn_list=[self.setting_btn, self.more_btn, self.run_btn, self.switch_btn],
            icon=FluentIcon.GAME,
            title=self.app.app_name,
            parent=parent,
        )

        # 调用 DraggableListItem 的 __init__
        DraggableListItem.__init__(
            self,
            data=app,
            index=index,
            content_widget=content_widget,
            parent=parent,
            enable_opacity_effect=enable_opacity_effect
        )

    def update_display(self) -> None:
        """
        更新显示的状态
        :return:
        """
        title = gt(self.app.app_name)
        self.content_widget.setTitle(title)
        if self.run_record is None:
            self.content_widget.setContent('')
        else:
            self.content_widget.setContent(f"{gt('上次运行')} {self.run_record.run_time}")

            status = self.run_record.run_status_under_now
            if status == AppRunRecord.STATUS_SUCCESS:
                icon = FluentIcon.COMPLETED.icon(color=FluentThemeColor.DEFAULT_BLUE.value)
            elif status == AppRunRecord.STATUS_RUNNING:
                icon = FluentIcon.COMPLETED.STOP_WATCH
            elif status == AppRunRecord.STATUS_FAIL:
                icon = FluentIcon.INFO.icon(color=FluentThemeColor.RED.value)
            else:
                icon = FluentIcon.INFO
            self.content_widget.iconLabel.setIcon(icon)

    def _on_move_top_clicked(self) -> None:
        """
        置顶运行顺序（用于拖拽不能处理滚动的场景）
        :return:
        """
        self.move_top.emit(self.app.app_id)

    def _show_more_menu(self) -> None:
        """
        显示低频操作菜单。
        """
        pos = self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft())
        self.more_menu.popup(pos)

    def enable_task_actions(self, removable: bool) -> None:
        """
        在更多菜单和右键菜单里加上任务列表操作：新增、删除、从此执行、清除标记。

        Args:
            removable: 是否能删除。只有手动新增的任务能删除，默认一条龙任务只能关掉开关。
        """
        if not self._task_actions_enabled:
            self._task_actions_enabled = True
            self.more_menu.addSeparator()
            self.more_menu.addAction(self.add_action)
            self.more_menu.addAction(self.run_from_action)
            self.more_menu.addAction(self.clear_mark_action)

        # RoundMenu 不认 QAction.setVisible（隐藏的项会变成灰色留在菜单里），所以按需插入 / 移除「删除」
        in_menu = self.remove_action in self.more_menu.menuActions()
        if removable and not in_menu:
            self.more_menu.insertAction(self.run_from_action, self.remove_action)
        elif not removable and in_menu:
            self.more_menu.removeAction(self.remove_action)

    def set_task_actions_enabled(self, enabled: bool) -> None:
        """一条龙运行中锁住 新增 / 删除 / 从此执行 / 清除标记，避免运行中改列表"""
        for action in (self.add_action, self.remove_action, self.run_from_action, self.clear_mark_action):
            action.setEnabled(enabled)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """右键卡片时弹出和「更多」按钮一样的菜单。"""
        if not self._task_actions_enabled or not self.more_btn.isEnabled():
            DraggableListItem.contextMenuEvent(self, event)
            return
        self.more_menu.popup(event.globalPos())
        event.accept()

    def _on_run_clicked(self) -> None:
        """
        运行应用
        :return:
        """
        self.run.emit(self.app.app_id)

    def _on_switch_changed(self, value: bool) -> None:
        """
        切换开关状态
        :return:
        """
        self.switched.emit(self.app.app_id, value)

    def set_app(
        self,
        app: ApplicationGroupConfigItem,
        run_record: AppRunRecord | None = None,
    ) -> None:
        """
        更新对应的app
        :param app:
        :return:
        """
        self.app = app
        self.run_record = run_record
        self.update_display()

    def setDisabled(self, arg__1: bool) -> None:
        self.content_widget.setDisabled(arg__1)
        self.setting_btn.setDisabled(arg__1)
        self.more_btn.setDisabled(arg__1)
        self.notify_action.setEnabled(not arg__1)
        self.move_top_action.setEnabled(not arg__1)
        self.set_task_actions_enabled(not arg__1)
        self.run_btn.setDisabled(arg__1)
        self.switch_btn.setDisabled(arg__1)

    def set_switch_on(self, on: bool) -> None:
        self.switch_btn.setChecked(on)

    def set_notify_visible(self, visible: bool) -> None:
        """
        设置通知菜单项是否可见。
        """
        self.notify_action.setVisible(visible)

    def _on_setting_clicked(self) -> None:
        """
        点击设置按钮
        """
        self.setting_clicked.emit(self.app.app_id)

    def _on_notify_clicked(self) -> None:
        """
        点击通知设置按钮
        """
        self.notify_clicked.emit(self.app.app_id)
