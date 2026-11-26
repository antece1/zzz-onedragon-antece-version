"""
绝区零一条龙 antece版「每天定时运行一条龙」的规则：管理哪个 Windows 计划任务、怎么新建、怎么提示。

真正读写计划任务的是 one_dragon.utils.task_scheduler_utils.TaskScheduler，本文件不依赖 Qt。
- 运行时间和开关以 Windows 计划任务为准，界面每次都从 Windows 读取。
- 用户以前自己建的任务（KNOWN_RUN_TASK_NAMES）只改时间和开关，不改名、不改运行内容。
- 都没有时，打开开关才新建本版自己的任务 APP_TASK_NAME，运行 deploy/antece_daily_run.py。
- 米游社数据同步任务只跟着改时间（比一条龙早 5 分钟），开关不动，也从不新建。
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from one_dragon.utils.log_utils import log
from one_dragon.utils.task_scheduler_utils import (
    E_ACCESSDENIED,
    E_NEEDS_PASSWORD,
    TASK_RUNLEVEL_HIGHEST,
    DailyTaskInfo,
    NewDailyTaskSpec,
    TaskScheduler,
    TaskSchedulerError,
    is_in_window,
    is_valid_hhmm,
    make_error,
    same_windows_path,
    shift_hhmm,
    task_display_name,
)
from zzz_od.config.auto_start_config import AutoStartConfig

APP_TASK_NAME: str = 'ZZZ OneDragon antece Auto Start'
"""本版自己新建的计划任务名。用英文：命令行和 GBK 控制台显示中文任务名会乱码"""

KNOWN_RUN_TASK_NAMES: tuple[str, ...] = ('ZZZ OneDragon Daily 2330',)
"""用户以前手动建的一条龙任务（只改时间和开关，不改名）"""

KNOWN_SYNC_TASK_NAMES: tuple[str, ...] = ('auto-zzz HoYoLAB sync 2300',)
"""用户以前手动建的米游社数据同步任务"""

SYNC_LEAD_MINUTES: int = 5
"""数据同步比一条龙提前几分钟"""

DEFAULT_RUN_TIME: str = '23:05'
"""还没有计划任务时，界面上显示的默认时间"""

APP_TASK_DESCRIPTION: str = (
    '绝区零一条龙 antece版：每天定时运行一条龙。'
    '由一条龙界面「运行设置 → 每天定时运行一条龙」管理，请在界面里改时间。'
)
APP_TASK_AUTHOR: str = 'ZZZ-OneDragon-antece'
APP_TASK_TIME_LIMIT: str = 'PT12H'
"""运行超过 12 小时就停止：设置了「已在运行就不启动新的」，卡住的一次不能一直挡住以后每天的运行"""

WRAPPER_RELATIVE_PATH: tuple[str, ...] = ('deploy', 'antece_daily_run.py')
DEFAULT_RUN_ARGS: tuple[str, ...] = ('--close-game',)

DUPLICATE_KEYWORDS: tuple[str, ...] = (
    'zzz_application_launcher',
    'OneDragon-Launcher.exe',
    'ZZZ-Auto.ps1',
    'antece_daily_run.py',
)
"""新建任务前查重用的关键字（另外还会查仓库目录）：已有任务在运行一条龙时不新建，避免每天跑两次"""

NEED_ADMIN_MESSAGE: str = '需要管理员权限：请用桌面快捷方式重新打开一条龙（会弹出管理员确认），再操作一次'
OFFSET_HINT: str = '时间带时区偏移，夏令时切换后会差 1 小时，在这里重新选一次时间即可修正'


def console_python(root: Path) -> Path:
    """
    计划任务用的 python.exe。
    不用 pythonw.exe（会被 Windows 智能应用控制拦截），也不用 sys.executable（可能是 pythonw.exe）。
    :param root: 仓库根目录
    :return: .venv 里的 python.exe
    """
    return root / '.venv' / 'Scripts' / 'python.exe'


def wrapper_script(root: Path) -> Path:
    """
    :param root: 仓库根目录
    :return: 定时运行入口 deploy/antece_daily_run.py
    """
    return root.joinpath(*WRAPPER_RELATIVE_PATH)


def conhost_exe() -> Path:
    """用 conhost.exe --headless 启动 python.exe，不弹黑框"""
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    return Path(system_root) / 'System32' / 'conhost.exe'


def build_app_task_spec(root: Path, run_time: str, enabled: bool) -> NewDailyTaskSpec:
    """
    本版自己的计划任务的参数
    :param root: 仓库根目录
    :param run_time: 每天运行时间 'HH:MM'
    :param enabled: 是否启用
    :return: 新建任务的参数
    """
    exec_path, exec_args = build_app_task_action(root)
    return NewDailyTaskSpec(
        path='\\' + APP_TASK_NAME,
        description=APP_TASK_DESCRIPTION,
        author=APP_TASK_AUTHOR,
        run_time=run_time,
        exec_path=exec_path,
        exec_args=exec_args,
        working_dir=str(root),
        run_level=TASK_RUNLEVEL_HIGHEST,  # 游戏是管理员进程，一条龙要管理员权限才能操作游戏
        execution_time_limit=APP_TASK_TIME_LIMIT,
        enabled=enabled,
    )


def build_app_task_action(root: Path) -> tuple[str, str]:
    """
    本版计划任务的「启动程序」操作
    :param root: 仓库根目录
    :return: 程序路径, 参数
    """
    args = ['--headless', str(console_python(root)), str(wrapper_script(root)), *DEFAULT_RUN_ARGS]
    return str(conhost_exe()), subprocess.list2cmdline(args)


@dataclass
class AutoStartStatus:
    """从 Windows 读到的定时运行状态"""

    state: str
    """'on' 已开启 | 'off' 已关闭 | 'missing' 没有计划任务 | 'error' 读取失败"""

    message: str
    """卡片上的状态文字"""

    run_time: str = DEFAULT_RUN_TIME
    """界面上显示的时间 'HH:MM'"""

    run_task: DailyTaskInfo | None = None
    sync_task: DailyTaskInfo | None = None
    detail: str = ''
    """卡片的鼠标提示：管理的任务、时间、运行账户"""


@dataclass
class AutoStartApplyResult:
    """保存的结果"""

    ok: bool
    level: str
    """'success' | 'warning' | 'error' | 'info'"""

    message: str
    status: AutoStartStatus
    """保存后重新从 Windows 读到的状态"""

    warnings: list[str] = field(default_factory=list)


class _ResolveError(Exception):
    """找任务时出错（例如配置的任务不存在）"""

    def __init__(self, message: str) -> None:
        Exception.__init__(self, message)
        self.message: str = message


class AutoStartService:
    """读取和保存「每天定时运行一条龙」。不会抛异常，出错时返回中文说明。"""

    def __init__(self, config: AutoStartConfig, scheduler: TaskScheduler, root: Path,
                 app_task_name: str = APP_TASK_NAME,
                 known_run_names: tuple[str, ...] = KNOWN_RUN_TASK_NAMES,
                 known_sync_names: tuple[str, ...] = KNOWN_SYNC_TASK_NAMES) -> None:
        self.config: AutoStartConfig = config
        self.scheduler: TaskScheduler = scheduler
        self.root: Path = root
        self.app_task_name: str = app_task_name
        self.known_run_names: tuple[str, ...] = known_run_names
        self.known_sync_names: tuple[str, ...] = known_sync_names

    # ------------------------------------------------------------ 找任务

    def _resolve_run(self) -> tuple[str | None, DailyTaskInfo | None]:
        """
        找运行一条龙的任务：配置的任务名 > 已知的用户任务 > 本版自己的任务
        :return: (任务名, 任务信息)，都没有时为 (None, None)
        """
        configured = self.config.run_task_name
        if configured:
            info = self.scheduler.read_daily_task(configured)
            if info is None:
                raise _ResolveError(f'配置里的计划任务「{task_display_name(configured)}」不存在')
            return configured, info
        for name in (*self.known_run_names, self.app_task_name):
            info = self.scheduler.read_daily_task(name)
            if info is not None:
                return name, info
        return None, None

    def _resolve_sync(self) -> tuple[str | None, DailyTaskInfo | None, bool]:
        """
        找米游社数据同步任务
        :return: (任务名, 任务信息, 是否为配置了但不存在)
        """
        configured = self.config.sync_task_name
        if configured == '-':
            return None, None, False
        if configured:
            info = self.scheduler.read_daily_task(configured)
            return configured, info, info is None
        for name in self.known_sync_names:
            info = self.scheduler.read_daily_task(name)
            if info is not None:
                return name, info, False
        return None, None, False

    def _is_app_task(self, name: str) -> bool:
        return task_display_name(name) == self.app_task_name

    # ------------------------------------------------------------ 读取

    def read_status(self) -> AutoStartStatus:
        """从 Windows 读取当前状态"""
        try:
            return self._read_status()
        except _ResolveError as e:
            return AutoStartStatus(state='error', message=f'读取失败：{e.message}', detail=e.message)
        except TaskSchedulerError as e:
            return AutoStartStatus(state='error', message=f'读取失败：{e.message}', detail=e.message)
        except Exception as e:  # noqa: BLE001 界面上只显示原因，详细的写日志
            log.error('读取定时运行状态失败', exc_info=True)
            return AutoStartStatus(state='error', message=f'读取失败：{e}', detail=str(e))

    def _read_status(self) -> AutoStartStatus:
        run_name, run_info = self._resolve_run()
        sync_name: str | None = None
        sync_info: DailyTaskInfo | None = None
        sync_note = ''
        try:
            sync_name, sync_info, sync_missing = self._resolve_sync()
            if sync_missing:
                sync_note = f'数据同步：没找到「{task_display_name(sync_name)}」'
        except TaskSchedulerError as e:
            sync_note = f'数据同步：读取失败（{e.message}）'

        if run_name is None or run_info is None:
            return AutoStartStatus(
                state='missing',
                message='没有计划任务，打开开关即新建',
                detail=f'打开开关后会新建计划任务「{self.app_task_name}」，每天到点用管理员权限运行一条龙，结束后关闭游戏',
            )

        detail_parts = [self._task_detail('计划任务', run_name, run_info)]
        if sync_info is not None and sync_name is not None:
            detail_parts.append(self._task_detail('数据同步', sync_name, sync_info))
        if sync_note:
            detail_parts.append(sync_note)
        if run_info.daily_trigger_count > 1:
            detail_parts.append('这个任务有多个每日触发器，这里只改第一个')
        if run_info.has_time_offset:
            detail_parts.append(OFFSET_HINT)
        detail = '\n'.join(detail_parts)

        if not run_info.has_daily_trigger:
            return AutoStartStatus(
                state='error',
                message=f'计划任务「{task_display_name(run_name)}」不是每天运行的，请在 Windows 任务计划程序里修改',
                run_task=run_info, sync_task=sync_info, detail=detail,
            )
        if run_info.needs_password:
            # 「不管用户是否登录都要运行」的任务，修改要密码，这里改不了，读取时就提示
            return AutoStartStatus(
                state='error',
                message=make_error(E_NEEDS_PASSWORD, run_name).message,
                run_time=run_info.run_time or DEFAULT_RUN_TIME,
                run_task=run_info, sync_task=sync_info, detail=detail,
            )

        run_time = run_info.run_time or DEFAULT_RUN_TIME
        if run_info.enabled:
            # 不显示年份，卡片右边一栏比较窄
            next_run = run_info.next_run[5:] if run_info.next_run and len(run_info.next_run) == 16 else run_info.next_run
            message = f'已开启，下次运行 {next_run}' if next_run else f'已开启，每天 {run_time}'
            state = 'on'
        else:
            message = '已关闭'
            state = 'off'
        if run_info.has_time_offset:
            message = f'{message}；{OFFSET_HINT}'
        return AutoStartStatus(state=state, message=message, run_time=run_time,
                               run_task=run_info, sync_task=sync_info, detail=detail)

    @staticmethod
    def _task_detail(label: str, name: str, info: DailyTaskInfo) -> str:
        on_off = '开' if info.enabled else '关'
        level = '管理员权限' if info.run_level == TASK_RUNLEVEL_HIGHEST else '普通权限'
        user = f'，账户 {info.user_id}' if info.user_id else ''
        return f'{label}：{task_display_name(name)}（{info.run_time or "?"}，{on_off}，{level}{user}）'

    # ------------------------------------------------------------ 保存

    def apply(self, run_time: str, enabled: bool, now: datetime | None = None) -> AutoStartApplyResult:
        """
        保存到 Windows 计划任务
        :param run_time: 每天运行时间 'HH:MM'
        :param enabled: 是否开启
        :param now: 当前时间，测试用
        :return: 结果（含保存后重新读到的状态）
        """
        try:
            level, message, warnings = self._apply(run_time, enabled, now or datetime.now())
        except _ResolveError as e:
            level, message, warnings = 'error', e.message, []
        except TaskSchedulerError as e:
            level, message, warnings = 'error', f'保存失败：{e.message}', []
        except Exception as e:  # noqa: BLE001 界面上只显示原因，详细的写日志
            log.error('保存定时运行失败', exc_info=True)
            level, message, warnings = 'error', f'保存失败：{e}', []
        if warnings and level == 'success':
            level = 'warning'
        if warnings:
            message = '；'.join([message, *warnings])
        return AutoStartApplyResult(ok=level != 'error', level=level, message=message,
                                    status=self.read_status(), warnings=warnings)

    def _apply(self, run_time: str, enabled: bool, now: datetime) -> tuple[str, str, list[str]]:
        """
        :return: (级别, 文字, 附加提示)
        """
        if not is_valid_hhmm(run_time):
            return 'error', f'时间格式不对：{run_time}，应为 HH:MM', []

        run_name, run_info = self._resolve_run()
        created = False
        if run_name is None or run_info is None:
            if not enabled:
                return 'info', '没有计划任务，无需关闭', []
            refuse = self._check_can_create()
            if refuse:
                return 'error', refuse, []
            spec = build_app_task_spec(self.root, run_time, True)
            self.scheduler.create_daily_task(spec)
            run_name = spec.path
            created = True
        else:
            if not run_info.has_daily_trigger:
                return 'error', (f'计划任务「{task_display_name(run_name)}」不是每天运行的，'
                                 '请在 Windows 任务计划程序里修改'), []
            exec_path: str | None = None
            exec_args: str | None = None
            working_dir: str | None = None
            if self._is_app_task(run_name):
                # 本版自己的任务：仓库搬家或换了 venv 时顺便把运行内容改对
                new_path, new_args = build_app_task_action(self.root)
                if (not same_windows_path(run_info.exec_path, new_path) or run_info.exec_args != new_args
                        or not run_info.working_dir or not same_windows_path(run_info.working_dir, str(self.root))):
                    exec_path, exec_args, working_dir = new_path, new_args, str(self.root)
            try:
                self.scheduler.update_daily_task(run_name, run_time, enabled,
                                                 exec_path=exec_path, exec_args=exec_args,
                                                 working_dir=working_dir, today=now.date())
            except TaskSchedulerError as e:
                if e.code == E_ACCESSDENIED and not self.scheduler.is_admin():
                    return 'error', NEED_ADMIN_MESSAGE, []
                raise

        if not enabled:
            if run_info is not None and run_info.enabled:
                return 'success', '已关闭定时运行', []
            # 开关本来就是关的，这次只是改了时间
            return 'success', f'已保存：每天 {run_time}（定时运行未开启，打开开关后生效）', []

        warnings: list[str] = []
        sync_time = shift_hhmm(run_time, -SYNC_LEAD_MINUTES)
        sync_saved = False
        try:
            sync_name, sync_info, sync_missing = self._resolve_sync()
        except TaskSchedulerError as e:
            sync_name, sync_info, sync_missing = None, None, False
            warnings.append(f'已保存，但数据同步任务读取失败：{e.message}')
        if sync_missing and sync_name is not None:
            warnings.append(f'没找到数据同步任务「{task_display_name(sync_name)}」，只修改了一条龙任务')
        elif sync_name is not None and sync_info is not None:
            try:
                if sync_info.run_time != sync_time or sync_info.has_time_offset:
                    # 只改时间，不动开关
                    self.scheduler.update_daily_task(sync_name, sync_time, None, today=now.date())
                sync_saved = True
            except TaskSchedulerError as e:
                warnings.append(f'已保存，但数据同步任务修改失败：{e.message}')
            if sync_saved and sync_info.enabled and is_in_window(now.strftime('%H:%M'), sync_time, run_time):
                warnings.append('今天的数据同步时间已过，今晚这次一条龙会用上次同步的数据')

        sync_text = f'（数据同步 {sync_time}）' if sync_saved else ''
        if created:
            message = f'已新建计划任务「{self.app_task_name}」：每天 {run_time} 运行一条龙{sync_text}'
        else:
            message = f'已保存：每天 {run_time} 运行一条龙{sync_text}'
        return 'success', message, warnings

    def _check_can_create(self) -> str | None:
        """
        新建任务前的检查
        :return: 不能新建的原因；可以新建时返回 None
        """
        if not self.scheduler.is_admin():
            return NEED_ADMIN_MESSAGE
        try:
            token_user, session_user = self.scheduler.get_token_and_session_user()
        except Exception:  # noqa: BLE001 查不到时不拦着
            log.error('查询当前账户失败', exc_info=True)
            token_user, session_user = '', ''
        if token_user and session_user and token_user.lower() != session_user.lower():
            return (f'当前以 {token_user} 的身份运行，但登录桌面的是 {session_user}，'
                    f'这样新建的任务不会运行。请用 {session_user} 自己的管理员权限打开一条龙')
        python = console_python(self.root)
        if not python.is_file():
            return f'找不到 {python}，无法新建计划任务'
        wrapper = wrapper_script(self.root)
        if not wrapper.is_file():
            return f'找不到 {wrapper}，无法新建计划任务'
        conhost = conhost_exe()
        if not conhost.is_file():
            return f'找不到 {conhost}，无法新建计划任务（需要 Windows 10 1809 或更新的系统）'
        duplicates = self.scheduler.find_tasks_by_command([str(self.root), *DUPLICATE_KEYWORDS])
        # 数据同步任务也在仓库目录下运行，它不是一条龙任务，不算重复
        try:
            _, sync_info, _ = self._resolve_sync()
        except TaskSchedulerError:
            sync_info = None
        if sync_info is not None:
            duplicates = [d for d in duplicates if d.lower() != sync_info.path.lower()]
        if duplicates:
            name = duplicates[0]
            return (f'已有计划任务「{name}」在运行一条龙，为避免每天运行两次没有新建。'
                    f'如果要用它，请在 config/auto_start.yml 里写 run_task_name: {name}')
        return None
