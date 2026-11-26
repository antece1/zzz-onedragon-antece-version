"""
通过 COM（Schedule.Service）读写 Windows 计划任务里「每天运行」的任务。

只做通用的事情：读取 / 修改每日触发时间和开关、新建每日任务、按命令内容查找任务。
不知道任何绝区零或一条龙相关的规则，那些放在调用方。

几个要点：
- 用 COM 而不是 schtasks.exe / PowerShell：COM 在本进程里运行，不弹黑框，几毫秒一次，
  任务名是中文也不会乱码，也不会像 schtasks 那样提示输入密码。
- 写入的 StartBoundary 不带时区偏移（如 2026-11-24T23:05:00），表示本地时间。
  带偏移（如 -08:00）的是一个固定的 UTC 时刻，夏令时切换后会早 / 晚 1 小时运行。
- 关闭开关时只关闭触发器，不关闭整个任务：整个任务关闭后连手动「运行」都会失败（0x80041326）。
- win32 相关模块在函数里才导入，本文件的纯函数部分可以在没有 pywin32 的环境里测试。
"""
from __future__ import annotations

import ctypes
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PureWindowsPath
from typing import Any, TypeVar

TASK_TRIGGER_DAILY: int = 2
TASK_ACTION_EXEC: int = 0
TASK_UPDATE: int = 4
TASK_CREATE_OR_UPDATE: int = 6
TASK_ENUM_HIDDEN: int = 1
TASK_LOGON_PASSWORD: int = 1
TASK_LOGON_INTERACTIVE_TOKEN: int = 3
TASK_LOGON_INTERACTIVE_TOKEN_OR_PASSWORD: int = 6
TASK_RUNLEVEL_LUA: int = 0
TASK_RUNLEVEL_HIGHEST: int = 1
TASK_INSTANCES_IGNORE_NEW: int = 2

E_ACCESSDENIED: int = 0x80070005
E_FILE_NOT_FOUND: int = 0x80070002
E_LOGON_FAILURE: int = 0x8007052E
E_NOT_DAILY: int = 0xA0000001
"""自定义错误码：任务没有每天运行的触发器"""
E_NEEDS_PASSWORD: int = 0xA0000002
"""自定义错误码：任务设置了「不管用户是否登录都要运行」，修改需要密码"""
E_BAD_TIME: int = 0xA0000003
"""自定义错误码：时间格式不对"""
E_UNKNOWN: int = 0xA00000FF
"""自定义错误码：其它意外错误"""

_HHMM_RE = re.compile(r'([01]\d|2[0-3]):[0-5]\d')
_OFFSET_RE = re.compile(r'(Z|[+-]\d{2}:\d{2})$')
_BOUNDARY_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?')

_T = TypeVar('_T')


# ---------------------------------------------------------------- 纯函数（不依赖 pywin32）

def is_valid_hhmm(hhmm: str) -> bool:
    """
    是否为 24 小时制的 'HH:MM'
    :param hhmm: 时间文本
    :return: 是否合法
    """
    return isinstance(hhmm, str) and _HHMM_RE.fullmatch(hhmm) is not None


def hhmm_to_minutes(hhmm: str) -> int:
    """
    'HH:MM' 转成当天的分钟数
    :param hhmm: 时间文本
    :return: 0 ~ 1439
    """
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def shift_hhmm(hhmm: str, minutes: int) -> str:
    """
    把时间前后移动若干分钟，跨过午夜时绕回
    :param hhmm: 时间文本 'HH:MM'
    :param minutes: 移动的分钟数，负数表示提前
    :return: 移动后的 'HH:MM'
    """
    total = (hhmm_to_minutes(hhmm) + minutes) % 1440
    return f'{total // 60:02d}:{total % 60:02d}'


def is_in_window(now_hhmm: str, start_hhmm: str, end_hhmm: str) -> bool:
    """
    当前时间是否在环形区间 [start, end) 里，区间可以跨过午夜
    :param now_hhmm: 当前时间
    :param start_hhmm: 区间开始（包含）
    :param end_hhmm: 区间结束（不包含）
    :return: 是否在区间里
    """
    now = hhmm_to_minutes(now_hhmm)
    start = hhmm_to_minutes(start_hhmm)
    end = hhmm_to_minutes(end_hhmm)
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def has_time_offset(boundary: str | None) -> bool:
    """
    StartBoundary 是否带时区偏移（'Z' 或 '+08:00' 这类）
    :param boundary: StartBoundary 文本
    :return: 是否带偏移
    """
    return boundary is not None and _OFFSET_RE.search(boundary.strip()) is not None


def build_start_boundary(old_boundary: str | None, hhmm: str, today: date) -> str:
    """
    生成新的 StartBoundary：保留原来的日期，只换时分，并去掉时区偏移（表示本地时间）。
    原来没有日期、或日期在今天之后（会推迟第一次运行）时，用今天的日期。
    :param old_boundary: 原来的 StartBoundary，可以为空
    :param hhmm: 新的时间 'HH:MM'
    :param today: 今天的日期
    :return: 'YYYY-MM-DDTHH:MM:00'
    """
    date_part = today.isoformat()
    if old_boundary:
        m = _BOUNDARY_RE.match(old_boundary.strip())
        if m is not None:
            try:
                old_date = date.fromisoformat(m.group(1))
            except ValueError:
                old_date = None
            if old_date is not None and old_date <= today:
                date_part = old_date.isoformat()
    return f'{date_part}T{hhmm}:00'


def parse_start_hhmm(boundary: str | None, today: date | None = None) -> str | None:
    """
    从 StartBoundary 里取出每天运行的本地时间 'HH:MM'。
    带时区偏移时，偏移表示固定的 UTC 时刻，要按今天的本地时区换算，
    这样夏令时切换后显示的也是实际运行的时间。
    :param boundary: StartBoundary 文本
    :param today: 按哪一天的时区换算，默认今天
    :return: 'HH:MM'，无法解析时返回 None
    """
    if not boundary:
        return None
    text = boundary.strip()
    m = _BOUNDARY_RE.match(text)
    if m is None:
        return None
    if not has_time_offset(text):
        hhmm = f'{m.group(2)}:{m.group(3)}'
        return hhmm if is_valid_hhmm(hhmm) else None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    day = today if today is not None else date.today()
    local = datetime.combine(day, parsed.timetz()).astimezone()
    return local.strftime('%H:%M')


def split_task_path(path: str) -> tuple[str, str]:
    """
    把任务路径拆成 (文件夹, 名称)，例如 '\\a\\b' -> ('\\a', 'b')，'b' -> ('\\', 'b')
    :param path: 任务路径或名称
    :return: 文件夹路径, 任务名称
    """
    full = path if path.startswith('\\') else '\\' + path
    folder, _, name = full.rpartition('\\')
    return folder or '\\', name


def task_display_name(path: str) -> str:
    """
    任务路径里的名称部分，用在提示文字里
    :param path: 任务路径或名称
    :return: 名称
    """
    return split_task_path(path)[1]


class TaskSchedulerError(Exception):
    """计划任务操作失败。code 为 HRESULT 或本文件里的自定义错误码，message 为中文说明。"""

    def __init__(self, code: int, message: str) -> None:
        Exception.__init__(self, message)
        self.code: int = code
        self.message: str = message


@dataclass
class DailyTaskInfo:
    """读取到的每日计划任务"""

    path: str
    """任务路径，例如 '\\ZZZ OneDragon Daily 2330'"""

    task_enabled: bool
    """整个任务是否启用（Settings.Enabled）"""

    trigger_enabled: bool
    """第一个每日触发器是否启用"""

    run_time: str | None
    """每天运行的本地时间 'HH:MM'；没有每日触发器时为 None"""

    start_boundary: str
    """第一个每日触发器的原始 StartBoundary"""

    next_run: str | None
    """下次运行 'YYYY-MM-DD HH:MM'；关闭时为 None"""

    run_level: int
    """0=普通权限 1=最高权限"""

    logon_type: int
    """3=只在用户登录时运行 1=不管用户是否登录都要运行（需要密码）"""

    user_id: str
    """运行账户"""

    daily_trigger_count: int
    """每日触发器的数量"""

    exec_path: str
    """第一个「启动程序」操作的程序路径"""

    exec_args: str
    """第一个「启动程序」操作的参数"""

    working_dir: str
    """第一个「启动程序」操作的起始目录"""

    @property
    def enabled(self) -> bool:
        """任务和每日触发器都启用时，才会每天自动运行"""
        return self.task_enabled and self.trigger_enabled

    @property
    def has_daily_trigger(self) -> bool:
        return self.daily_trigger_count > 0

    @property
    def has_time_offset(self) -> bool:
        """StartBoundary 带时区偏移，夏令时切换后运行时间会差 1 小时"""
        return has_time_offset(self.start_boundary)

    @property
    def needs_password(self) -> bool:
        return self.logon_type in (TASK_LOGON_PASSWORD, TASK_LOGON_INTERACTIVE_TOKEN_OR_PASSWORD)


@dataclass
class NewDailyTaskSpec:
    """新建每日计划任务的参数"""

    path: str
    """任务路径或名称"""

    description: str
    author: str
    run_time: str
    """每天运行的本地时间 'HH:MM'"""

    exec_path: str
    exec_args: str
    working_dir: str
    run_level: int
    """0=普通权限 1=最高权限"""

    execution_time_limit: str
    """运行超过多久就停止，例如 'PT12H'"""

    enabled: bool


def _com_code(error: Any) -> int:
    """从 pywintypes.com_error 里取出真正的错误码"""
    excepinfo = getattr(error, 'excepinfo', None)
    if excepinfo and len(excepinfo) > 5 and excepinfo[5]:
        return excepinfo[5] & 0xFFFFFFFF
    return (getattr(error, 'hresult', 0) or 0) & 0xFFFFFFFF


def _com_text(error: Any) -> str:
    """从 pywintypes.com_error 里取出错误说明"""
    excepinfo = getattr(error, 'excepinfo', None)
    if excepinfo and len(excepinfo) > 2 and excepinfo[2]:
        return str(excepinfo[2]).strip()
    strerror = getattr(error, 'strerror', None)
    return str(strerror).strip() if strerror else ''


def make_error(code: int, path: str, detail: str = '') -> TaskSchedulerError:
    """
    把错误码转成中文说明
    :param code: HRESULT 或自定义错误码
    :param path: 任务路径
    :param detail: 系统给出的错误说明
    :return: 异常
    """
    name = task_display_name(path)
    if code == E_FILE_NOT_FOUND:
        return TaskSchedulerError(code, f'计划任务「{name}」不存在')
    if code == E_ACCESSDENIED:
        return TaskSchedulerError(code, f'没有权限修改计划任务「{name}」（0x{code:08X}）')
    if code in (E_LOGON_FAILURE, E_NEEDS_PASSWORD):
        return TaskSchedulerError(
            code, f'计划任务「{name}」设置了「不管用户是否登录都要运行」，需要密码，请在 Windows 任务计划程序里修改')
    if code == E_NOT_DAILY:
        return TaskSchedulerError(code, f'计划任务「{name}」不是每天运行的，请在 Windows 任务计划程序里修改')
    text = f' {detail}' if detail else ''
    return TaskSchedulerError(code, f'计划任务「{name}」操作失败：0x{code:08X}{text}')


# ---------------------------------------------------------------- COM 部分

class TaskScheduler:
    """
    通过 COM(Schedule.Service) 读写 Windows 计划任务。
    每次调用都自己 CoInitialize / CoUninitialize，可以在任意线程调用。
    COM 对象只在内部函数里使用，返回前全部释放，返回值和异常里都只有普通数据。
    """

    def _call(self, path: str, func: Callable[[Any], _T]) -> _T:
        """
        连接计划任务服务并执行 func(service)
        :param path: 任务路径，用于错误提示
        :param func: 实际的操作，参数为已连接的 Schedule.Service
        :return: func 的返回值
        """
        import pythoncom
        import pywintypes

        pythoncom.CoInitialize()
        error: TaskSchedulerError | None = None
        try:
            return self._connect_and_run(func)
        except pywintypes.com_error as e:
            error = make_error(_com_code(e), path, _com_text(e))
        except TaskSchedulerError as e:
            error = TaskSchedulerError(e.code, e.message)
        except Exception as e:  # noqa: BLE001 意外错误也转成普通数据，避免异常里留着 COM 对象
            error = TaskSchedulerError(E_UNKNOWN, f'计划任务「{task_display_name(path)}」操作失败：{e!r}')
        finally:
            # except 块结束时异常和它引用的 COM 对象已经释放，这里再结束 COM
            pythoncom.CoUninitialize()
        raise error

    @staticmethod
    def _connect_and_run(func: Callable[[Any], _T]) -> _T:
        import win32com.client

        service = win32com.client.Dispatch('Schedule.Service')
        service.Connect()
        return func(service)

    @staticmethod
    def _first_daily_trigger(definition: Any) -> tuple[Any | None, int]:
        """返回 (第一个每日触发器, 每日触发器数量)"""
        first = None
        count = 0
        triggers = definition.Triggers
        for i in range(1, triggers.Count + 1):
            trigger = triggers.Item(i)
            if trigger.Type == TASK_TRIGGER_DAILY:
                count += 1
                if first is None:
                    first = trigger
        return first, count

    @staticmethod
    def _first_exec_action(definition: Any) -> Any | None:
        actions = definition.Actions
        for i in range(1, actions.Count + 1):
            action = actions.Item(i)
            if action.Type == TASK_ACTION_EXEC:
                return action
        return None

    def read_daily_task(self, path: str) -> DailyTaskInfo | None:
        """
        读取每日计划任务
        :param path: 任务路径或名称
        :return: 任务信息；任务不存在时返回 None
        """
        try:
            return self._call(path, lambda service: self._read(service, path))
        except TaskSchedulerError as e:
            if e.code == E_FILE_NOT_FOUND:
                return None
            raise

    def _read(self, service: Any, path: str) -> DailyTaskInfo:
        folder_path, name = split_task_path(path)
        task = service.GetFolder(folder_path).GetTask(name)
        definition = task.Definition
        trigger, count = self._first_daily_trigger(definition)
        action = self._first_exec_action(definition)
        task_enabled = bool(task.Enabled)
        trigger_enabled = bool(trigger.Enabled) if trigger is not None else False
        boundary = str(trigger.StartBoundary or '') if trigger is not None else ''
        next_run: str | None = None
        if task_enabled and trigger_enabled:
            # pywintypes 把本地时间标成了 UTC，不能 astimezone，直接格式化
            nrt = task.NextRunTime
            if nrt is not None and nrt.year > 1900:
                next_run = nrt.strftime('%Y-%m-%d %H:%M')
        principal = definition.Principal
        return DailyTaskInfo(
            path=str(task.Path),
            task_enabled=task_enabled,
            trigger_enabled=trigger_enabled,
            run_time=parse_start_hhmm(boundary) if trigger is not None else None,
            start_boundary=boundary,
            next_run=next_run,
            run_level=int(principal.RunLevel or 0),
            logon_type=int(principal.LogonType or 0),
            user_id=str(principal.UserId or ''),
            daily_trigger_count=count,
            exec_path=str(action.Path or '') if action is not None else '',
            exec_args=str(action.Arguments or '') if action is not None else '',
            working_dir=str(action.WorkingDirectory or '') if action is not None else '',
        )

    def update_daily_task(self, path: str, hhmm: str | None, enabled: bool | None,
                          exec_path: str | None = None, exec_args: str | None = None,
                          working_dir: str | None = None, today: date | None = None) -> None:
        """
        修改每日计划任务。只改第一个每日触发器和（可选的）第一个启动程序操作，其余设置原样保留。
        :param path: 任务路径或名称
        :param hhmm: 新的每天运行时间 'HH:MM'；None 表示不改时间
        :param enabled: True=打开（任务和触发器都启用）；False=只关闭触发器；None=不改开关
        :param exec_path: 新的程序路径；None 表示不改
        :param exec_args: 新的参数；None 表示不改
        :param working_dir: 新的起始目录；None 表示不改
        :param today: 今天的日期，测试用
        """
        if hhmm is not None and not is_valid_hhmm(hhmm):
            raise TaskSchedulerError(E_BAD_TIME, f'时间格式不对：{hhmm}，应为 HH:MM')
        day = today if today is not None else date.today()

        def _update(service: Any) -> None:
            folder_path, name = split_task_path(path)
            folder = service.GetFolder(folder_path)
            task = folder.GetTask(name)
            definition = task.Definition
            logon_type = int(definition.Principal.LogonType or 0)
            if logon_type in (TASK_LOGON_PASSWORD, TASK_LOGON_INTERACTIVE_TOKEN_OR_PASSWORD):
                raise make_error(E_NEEDS_PASSWORD, path)
            trigger, _ = self._first_daily_trigger(definition)
            if trigger is None:
                raise make_error(E_NOT_DAILY, path)
            if hhmm is not None:
                # 不带时区偏移 = 本地时间，夏令时切换后仍在同一本地时间运行
                trigger.StartBoundary = build_start_boundary(str(trigger.StartBoundary or ''), hhmm, day)
            if enabled is True:
                definition.Settings.Enabled = True
                trigger.Enabled = True
            elif enabled is False:
                # 只关闭触发器：整个任务关闭后手动「运行」也会失败
                trigger.Enabled = False
            if exec_path is not None or exec_args is not None or working_dir is not None:
                action = self._first_exec_action(definition)
                if action is None:
                    action = definition.Actions.Create(TASK_ACTION_EXEC)
                if exec_path is not None:
                    action.Path = exec_path
                if exec_args is not None:
                    action.Arguments = exec_args
                if working_dir is not None:
                    action.WorkingDirectory = working_dir
            # 用户和密码传 None：沿用任务原来的运行账户、登录方式和权限级别
            # 路径要用相对于 folder 的名称：传完整路径时，子文件夹里的任务会被当成 folder 下面的再下一层，报「不存在」
            folder.RegisterTaskDefinition(name, definition, TASK_UPDATE, None, None, logon_type, None)

        self._call(path, _update)

    def create_daily_task(self, spec: NewDailyTaskSpec) -> None:
        """
        新建（或覆盖同名的）每日计划任务，运行账户为当前账户，只在用户登录时运行
        :param spec: 任务参数
        """
        if not is_valid_hhmm(spec.run_time):
            raise TaskSchedulerError(E_BAD_TIME, f'时间格式不对：{spec.run_time}，应为 HH:MM')
        today = date.today()

        def _create(service: Any) -> None:
            folder_path, name = split_task_path(spec.path)
            folder = service.GetFolder(folder_path)
            definition = service.NewTask(0)
            info = definition.RegistrationInfo
            info.Author = spec.author
            info.Description = spec.description
            principal = definition.Principal
            # 不填 UserId：注册时自动使用调用者自己的账户
            principal.LogonType = TASK_LOGON_INTERACTIVE_TOKEN
            principal.RunLevel = spec.run_level
            settings = definition.Settings
            settings.Enabled = True
            settings.DisallowStartIfOnBatteries = False
            settings.StopIfGoingOnBatteries = False
            settings.MultipleInstances = TASK_INSTANCES_IGNORE_NEW
            settings.ExecutionTimeLimit = spec.execution_time_limit
            settings.StartWhenAvailable = False
            settings.WakeToRun = False
            trigger = definition.Triggers.Create(TASK_TRIGGER_DAILY)
            trigger.StartBoundary = build_start_boundary(None, spec.run_time, today)
            trigger.DaysInterval = 1
            trigger.Enabled = spec.enabled
            action = definition.Actions.Create(TASK_ACTION_EXEC)
            action.Path = spec.exec_path
            action.Arguments = spec.exec_args
            action.WorkingDirectory = spec.working_dir
            folder.RegisterTaskDefinition(name, definition, TASK_CREATE_OR_UPDATE, None, None,
                                          TASK_LOGON_INTERACTIVE_TOKEN, None)

        self._call(spec.path, _create)

    def find_tasks_by_command(self, keywords: list[str]) -> list[str]:
        """
        在所有文件夹里查找「启动程序」操作（程序、参数、起始目录）包含任一关键字的任务，只读不改
        :param keywords: 关键字，不区分大小写
        :return: 任务路径列表
        """
        lowered = [k.lower() for k in keywords if k]

        def _match(definition: Any) -> bool:
            actions = definition.Actions
            for i in range(1, actions.Count + 1):
                action = actions.Item(i)
                if action.Type != TASK_ACTION_EXEC:
                    continue
                text = ' '.join([str(action.Path or ''), str(action.Arguments or ''),
                                 str(action.WorkingDirectory or '')]).lower()
                if any(k in text for k in lowered):
                    return True
            return False

        def _walk(folder: Any, found: list[str]) -> None:
            import pywintypes
            try:
                tasks = folder.GetTasks(TASK_ENUM_HIDDEN)
                for i in range(1, tasks.Count + 1):
                    try:
                        task = tasks.Item(i)
                        if _match(task.Definition):
                            found.append(str(task.Path))
                    except pywintypes.com_error:
                        continue  # 没有权限读取的任务跳过
                sub_folders = folder.GetFolders(0)
                for j in range(1, sub_folders.Count + 1):
                    _walk(sub_folders.Item(j), found)
            except pywintypes.com_error:
                return  # 没有权限读取的文件夹跳过

        def _find(service: Any) -> list[str]:
            found: list[str] = []
            _walk(service.GetFolder('\\'), found)
            return found

        return self._call('\\', _find)

    @staticmethod
    def is_admin() -> bool:
        """当前进程是否以管理员身份运行"""
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    @staticmethod
    def get_token_and_session_user() -> tuple[str, str]:
        """
        返回 (当前进程的账户, 当前桌面登录的账户)，格式都是 '域或电脑名\\用户名'。
        两者不同说明 UAC 用了另一个管理员账户，用它新建的「只在用户登录时运行」任务永远不会运行。
        """
        import win32api
        import win32con
        import win32ts

        token_user = str(win32api.GetUserNameEx(win32con.NameSamCompatible))
        user = win32ts.WTSQuerySessionInformation(
            win32ts.WTS_CURRENT_SERVER_HANDLE, win32ts.WTS_CURRENT_SESSION, win32ts.WTSUserName)
        domain = win32ts.WTSQuerySessionInformation(
            win32ts.WTS_CURRENT_SERVER_HANDLE, win32ts.WTS_CURRENT_SESSION, win32ts.WTSDomainName)
        session_user = f'{domain}\\{user}' if domain else str(user)
        return token_user, session_user


def same_windows_path(a: str, b: str) -> bool:
    """
    两个 Windows 路径是否相同（不区分大小写，忽略首尾引号）
    :param a: 路径
    :param b: 路径
    :return: 是否相同
    """
    return PureWindowsPath(a.strip().strip('"')) == PureWindowsPath(b.strip().strip('"'))
