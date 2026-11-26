from PySide6.QtWidgets import QWidget
from qfluentwidgets import BodyLabel, ComboBox, MessageBoxBase, SubtitleLabel

from one_dragon.utils.i18_utils import gt


class AppSelectDialog(MessageBoxBase):
    """从下拉框里选一个应用，用于一条龙「任务列表」的新增。"""

    def __init__(self, app_list: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        """
        Args:
            app_list: 可选的应用，每项是 (app_id, 应用名称)
            parent: 父窗口
        """
        MessageBoxBase.__init__(self, parent)
        self.app_list: list[tuple[str, str]] = app_list

        self.title_label = SubtitleLabel(gt('新增任务'))
        self.viewLayout.addWidget(self.title_label)

        self.tip_label = BodyLabel(gt('选一个应用加到任务列表末尾，加好后默认打开开关'))
        self.viewLayout.addWidget(self.tip_label)

        self.app_combo = ComboBox()
        for app_id, app_name in app_list:
            self.app_combo.addItem(gt(app_name), userData=app_id)
        self.app_combo.setMinimumWidth(280)
        self.viewLayout.addWidget(self.app_combo)

        self.yesButton.setText(gt('新增'))
        self.cancelButton.setText(gt('取消'))
        self.widget.setMinimumWidth(360)

    @property
    def selected_app_id(self) -> str | None:
        """选中的应用ID，没有可选应用时返回 None"""
        if self.app_combo.currentIndex() < 0:
            return None
        return self.app_combo.currentData()
