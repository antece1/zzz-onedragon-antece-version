"""
绝区零一条龙 antece版 定时运行入口（给 Windows 计划任务用）。

由一条龙界面「运行设置 → 每天定时运行一条龙」新建的计划任务运行本文件：
    conhost.exe --headless <仓库>\\.venv\\Scripts\\python.exe <仓库>\\deploy\\antece_daily_run.py --close-game

- 计划任务不能设置环境变量，所以由本文件切到仓库目录、把 src 加进搜索路径，
  再运行和 OneDragon-Launcher 一条龙模式相同的入口 zzz_od.application.zzz_application_launcher。
- 用 python.exe 运行（pythonw.exe 会被 Windows 智能应用控制拦截），计划任务里用 conhost.exe --headless 隐藏黑框。
- 不要改成调用 OneDragon-Launcher.exe：它会把代码重置成官方版本。
- 一条龙界面开着时会先关掉它：两个进程同时写配置、同时操作游戏会出错。
- 参数原样交给启动器，例如 -i 1,2 只运行这两个账号，--shutdown 运行后关机；不传参数时用 --close-game。
- 每一步写日志到 .log/antece_daily_run.log；出错时退出码为 1。
  注意 conhost.exe --headless 不传子进程的退出码，任务计划程序的「上次运行结果」总是 0，出错要看这个日志。
"""
import contextlib
import os
import sys
import time
import traceback
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
LOG_PATH: Path = ROOT / '.log' / 'antece_daily_run.log'
GUI_SCRIPT: Path = ROOT / 'src' / 'zzz_od' / 'gui' / 'app.py'
DEFAULT_ARGS: list[str] = ['--close-game']


def log(msg: str) -> None:
    """写一行日志，写不了也不影响运行"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _is_gui_process(cmdline: list[str], cwd: str | None) -> bool:
    """
    命令行里是否运行了本仓库的一条龙界面 src/zzz_od/gui/app.py（相对路径按进程的当前目录换算）
    :param cmdline: 进程命令行
    :param cwd: 进程的当前目录，拿不到时为 None
    :return: 是否为一条龙界面
    """
    for arg in cmdline[1:]:
        if not arg.lower().endswith('app.py'):
            continue
        path = Path(arg)
        if not path.is_absolute():
            if cwd is None:
                continue
            path = Path(cwd) / path
        if _same_path(path, GUI_SCRIPT):
            return True
    return False


def close_gui() -> None:
    """
    关掉正在运行的一条龙界面（连同它的子进程）。
    按命令行里的 app.py 判断，不按程序路径判断：本进程的 python.exe 也在仓库目录下。
    """
    try:
        import psutil
    except ImportError:
        log('没有 psutil，跳过检查一条龙界面')
        return
    me = os.getpid()
    targets: list = []
    for proc in psutil.process_iter(['pid', 'cmdline']):
        if proc.pid == me:
            continue
        try:
            cmdline = proc.info.get('cmdline') or []
            if not cmdline:
                continue
            try:
                cwd = proc.cwd()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                cwd = None
            if _is_gui_process(cmdline, cwd):
                targets.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for proc in targets:
        # venv 的 python.exe 会再启动一个真正的 python.exe，两个进程命令行相同，都会被找到
        try:
            family = [*proc.children(recursive=True), proc]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            family = [proc]
        log(f'一条龙界面正在运行（进程 {proc.pid}），先关闭它')
        stopping = []
        for p in family:
            try:
                p.terminate()
                stopping.append(p)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                log(f'没有权限关闭进程 {p.pid}（一条龙界面是管理员运行、本次不是时会这样）')
        _, alive = psutil.wait_procs(stopping, timeout=10)
        for p in alive:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.kill()


def setup_path() -> None:
    """切到仓库目录，让本进程和子进程都能找到 src 里的代码"""
    os.chdir(ROOT)
    src = str(ROOT / 'src')
    script_dir = Path(__file__).resolve().parent
    if sys.path and sys.path[0] and _same_path(Path(sys.path[0]).resolve(), script_dir):
        sys.path[0] = src  # 去掉 deploy/ 本身
    else:
        sys.path.insert(0, src)
    os.environ['PYTHONPATH'] = src
    # 输出被重定向时（例如写到文件），默认编码可能是 cp1252 / GBK，打印中文会出错导致运行失败
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding='utf-8', errors='replace')


def main() -> int:
    args = sys.argv[1:] or list(DEFAULT_ARGS)
    is_help = '-h' in args or '--help' in args
    if not is_help:
        log(f'开始定时运行一条龙 参数 {args}')
    try:
        setup_path()
        from zzz_od.application.zzz_application_launcher import ZApplicationLauncher
        sys.argv = [sys.argv[0], *args]
        launcher = ZApplicationLauncher()
        # 和 launcher.run() 相同的两步，中间插入关闭界面：
        # 先解析参数（--help、参数错误会在这里退出，不会去关界面），再关界面，最后运行
        launcher.setup_parser()
        close_gui()
        launcher.main(launcher.args)
    except SystemExit as e:
        # 参数错误、--help 等由启动器自己退出，退出码原样传出
        if not is_help:
            log(f'启动器退出 退出码 {e.code}')
        raise
    except Exception:  # noqa: BLE001 出错写日志，退出码 1
        log(f'运行出错\n{traceback.format_exc()}')
        return 1
    if not is_help:
        log('定时运行结束')
    return 0


if __name__ == '__main__':
    sys.exit(main())
