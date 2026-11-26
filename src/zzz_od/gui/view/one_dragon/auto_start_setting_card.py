from collections.abc import Callable

from PySide6.QtCore import Qt, QThread, QTime, Signal
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import QLabel, QWidget
from qfluentwidgets import (
    FluentIcon,
    IndicatorPosition,
    InfoBar,
    InfoBarPosition,
    SwitchButton,
    TimePicker,
)

from one_dragon.utils.log_utils import log
from one_dragon_qt.widgets.setting_card.setting_card_base import SettingCardBase
from zzz_od.utils.auto_start_utils import (
    DEFAULT_RUN_TIME,
    AutoStartApplyResult,
    AutoStartService,
    AutoStartStatus,
)

_TITLE: str = '每天定时运行一条龙'
_CONTENT_MAX_WIDTH: int = 230
"""状态文字最多占多宽，太长时省略，鼠标悬停看完整内容"""


class ConfirmTimePicker(TimePicker):
    """每次在弹出面板里点确认都发 time_confirmed 信号（时间没变也发），方便重新保存一次"""

    time_confirmed = Signal(QTime)

    def _onConfirmed(self, value: list) -> None:
        TimePicker._onConfirmed(self, value)
        self.time_confirmed.emit(self.getTime())


class AutoStartApplyRunner(QThread):
    """在后台线程保存到 Windows 计划任务：新建任务前要查遍所有计划任务，可能要 1 秒左右，不能卡住界面"""

    done = Signal(object)
    """保存结果 AutoStartApplyResult"""

    def __init__(self, service: AutoStartService, run_time: str, enabled: bool) -> None:
        QThread.__init__(self)
        self.service: AutoStartService = service
        self.run_time: str = run_time
        self.enabled: bool = enabled

    def run(self) -> None:
        try:
            result = self.service.apply(self.run_time, self.enabled)
        except Exception as e:  # noqa: BLE001 apply 本身不抛异常，这里兜底
            log.error('保存定时运行失败', exc_info=True)
            result = AutoStartApplyResult(ok=False, level='error', message=f'保存失败：{e}',
                                          status=AutoStartStatus(state='error', message=f'保存失败：{e}'))
        self.done.emit(result)


_running_runners: set[AutoStartApplyRunner] = set()
"""正在保存的线程。保存期间页面被销毁时，线程对象也要留到运行结束"""


class AutoStartSettingCard(SettingCardBase):
    """
    每天定时运行一条龙：本地时间 + 开关。
    状态从 Windows 计划任务读取；拨动开关、或在时间面板里点确认，就马上保存到 Windows。
    保存失败时弹出提示，并把界面恢复成 Windows 里的实际状态。
    读取只要几毫秒，在界面线程里做；保存放到后台线程（新建任务前的查重比较慢）。
    """

    def __init__(self, service_factory: Callable[[], AutoStartService], parent: QWidget | None = None) -> None:
        SettingCardBase.__init__(
            self,
            icon=FluentIcon.STOP_WATCH,
            title=_TITLE,
            content='正在读取计划任务…',
            parent=parent,
        )
        self._service_factory: Callable[[], AutoStartService] = service_factory
        self._status: AutoStartStatus | None = None
        self._busy: bool = False
        self._content_text: str = '正在读取计划任务…'
        """状态文字的完整内容（显示时可能被省略）"""

        self.contentLabel.setMaximumWidth(_CONTENT_MAX_WIDTH)

        self.time_picker = ConfirmTimePicker(self)
        self.time_picker.setColumnWidth(0, 52)
        self.time_picker.setColumnWidth(1, 52)
        # 必须先设一个时间，否则显示英文的 hour / minute
        self.time_picker.setTime(QTime.fromString(DEFAULT_RUN_TIME, 'HH:mm'))
        self.time_picker.time_confirmed.connect(self._on_time_confirmed)

        self.switch_btn = SwitchButton(parent=self, indicatorPos=IndicatorPosition.RIGHT)
        self.switch_btn.setOnText('')
        self.switch_btn.setOffText('')
        self.switch_btn.checkedChanged.connect(self._on_switch_changed)

        self.hBoxLayout.addWidget(self.time_picker, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(12)
        self.hBoxLayout.addWidget(self.switch_btn, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    # ------------------------------------------------------------ 文字省略

    def setContent(self, content: str) -> None:
        """按卡片实际剩下的宽度省略状态文字，完整内容放在鼠标提示里"""
        self._content_text = content or ''
        self._elide_labels()

    def resizeEvent(self, e: QResizeEvent) -> None:
        SettingCardBase.resizeEvent(self, e)
        self._elide_labels()

    def showEvent(self, e: QShowEvent) -> None:
        SettingCardBase.showEvent(self, e)
        self._elide_labels()

    def _text_width(self) -> int:
        """标题和状态文字能用的宽度：卡片宽度减去左边图标和右边的时间、开关"""
        if not self.isVisible():
            return _CONTENT_MAX_WIDTH * 10  # 还没显示时宽度不准，先不按宽度省略，显示时再算
        right = self.time_picker.sizeHint().width() + 12 + self.switch_btn.sizeHint().width() + 16
        return max(0, self.width() - self.contentLabel.x() - right - 8)

    def _elide_labels(self) -> None:
        avail = self._text_width()
        _set_elided(self.titleLabel, _TITLE, avail)
        _set_elided(self.contentLabel, self._content_text, min(_CONTENT_MAX_WIDTH, avail))
        self.contentLabel.setVisible(len(self._content_text) > 0)

    # ------------------------------------------------------------ 读取

    def refresh(self) -> None:
        """从 Windows 重新读取状态"""
        if self._busy:
            return
        try:
            # 每次都新建：手动改了 config/auto_start.yml 后，不用重启界面就能生效
            status = self._service_factory().read_status()
        except Exception as e:  # noqa: BLE001 读取失败只显示在卡片上
            log.error('读取定时运行状态失败', exc_info=True)
            status = AutoStartStatus(state='error', message=f'读取失败：{e}')
        self._show_status(status)

    def _show_status(self, status: AutoStartStatus) -> None:
        """按 Windows 里的实际状态显示，不触发保存"""
        self._status = status
        time = QTime.fromString(status.run_time or DEFAULT_RUN_TIME, 'HH:mm')
        if not time.isValid():
            time = QTime.fromString(DEFAULT_RUN_TIME, 'HH:mm')
        if status.state == 'missing':
            # 没有任务时保留用户刚选的时间，打开开关时用它新建
            time = self.time_picker.getTime() if self.time_picker.getTime().isValid() else time
        self.time_picker.blockSignals(True)
        self.time_picker.setTime(time)
        self.time_picker.blockSignals(False)
        self.switch_btn.blockSignals(True)
        self.switch_btn.setChecked(status.state == 'on')
        self.switch_btn.blockSignals(False)
        self.setContent(status.message)
        self.setToolTip(status.detail)

    # ------------------------------------------------------------ 保存

    def _on_switch_changed(self, checked: bool) -> None:
        self._apply(self._picker_hhmm(), checked)

    def _on_time_confirmed(self, time: QTime) -> None:
        if self._status is not None and self._status.state == 'missing' and not self.switch_btn.isChecked():
            # 还没有任务、开关也没开：只记住时间，打开开关时再新建
            return
        self._apply(time.toString('HH:mm'), self.switch_btn.isChecked())

    def _picker_hhmm(self) -> str:
        time = self.time_picker.getTime()
        return time.toString('HH:mm') if time.isValid() else DEFAULT_RUN_TIME

    def _apply(self, run_time: str, enabled: bool) -> None:
        """在后台线程保存到 Windows，保存完按重新读到的状态刷新界面"""
        if self._busy:
            return
        try:
            service = self._service_factory()
        except Exception as e:  # noqa: BLE001 创建失败时按保存失败处理
            log.error('保存定时运行失败', exc_info=True)
            self._on_apply_done(AutoStartApplyResult(
                ok=False, level='error', message=f'保存失败：{e}',
                status=AutoStartStatus(state='error', message=f'保存失败：{e}')))
            return
        self._busy = True
        self.setContent('正在保存…')
        self.time_picker.setEnabled(False)
        self.switch_btn.setEnabled(False)

        runner = AutoStartApplyRunner(service, run_time, enabled)
        runner.done.connect(self._on_apply_done)
        runner.finished.connect(lambda r=runner: _running_runners.discard(r))
        _running_runners.add(runner)
        runner.start()

    def _on_apply_done(self, result: AutoStartApplyResult) -> None:
        self.time_picker.setEnabled(True)
        self.switch_btn.setEnabled(True)
        self._busy = False
        self._show_status(result.status)
        self._show_result(result)

    def _show_result(self, result: AutoStartApplyResult) -> None:
        if result.level == 'success':
            log.info(f'定时运行一条龙 {result.message}')
        else:
            log.info(f'定时运行一条龙 {result.level} {result.message}')
        parent = self.window()
        if result.level == 'success':
            InfoBar.success(title='已保存', content=result.message, orient=Qt.Orientation.Horizontal,
                            isClosable=True, position=InfoBarPosition.TOP, duration=3000, parent=parent)
        elif result.level == 'warning':
            InfoBar.warning(title='已保存', content=result.message, orient=Qt.Orientation.Horizontal,
                            isClosable=True, position=InfoBarPosition.TOP, duration=-1, parent=parent)
        elif result.level == 'info':
            InfoBar.info(title='提示', content=result.message, orient=Qt.Orientation.Horizontal,
                         isClosable=True, position=InfoBarPosition.TOP, duration=3000, parent=parent)
        else:
            InfoBar.error(title='保存失败', content=result.message, orient=Qt.Orientation.Horizontal,
                          isClosable=True, position=InfoBarPosition.TOP, duration=-1, parent=parent)


def _set_elided(label: QLabel, text: str, width: int) -> None:
    """文字放不下时在末尾省略，并把完整文字放到鼠标提示里"""
    metrics = label.fontMetrics()
    if metrics.horizontalAdvance(text) > width:
        label.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, width))
        label.setToolTip(text)
    else:
        label.setText(text)
        label.setToolTip('')
