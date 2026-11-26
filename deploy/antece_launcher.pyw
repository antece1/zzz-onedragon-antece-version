"""
绝区零一条龙 antece版 启动器。

桌面快捷方式用 .venv\\Scripts\\python.exe 运行本文件（快捷方式设为最小化，启动后马上隐藏黑框）。
不用 pythonw.exe：uv 装的 Python 没有签名，Windows「智能应用控制」从 2026-11-24 起拦截 pythonw.exe
（python.exe 还能运行）。界面也用 python.exe 加 CREATE_NO_WINDOW 启动，不弹黑框。
步骤：
0. 不是管理员就弹 UAC，以管理员身份重新运行（游戏是管理员进程，一条龙不是管理员就点不动游戏）。
1. 在 main 分支、没有未提交的改动时，从 antece1/zzz-onedragon-antece-version 拉取最新代码（只快进，不覆盖本地改动）。
2. 代码更新里 pyproject.toml / uv.lock 有变化时，用 uv 同步依赖。
3. 启动一条龙界面。

每一步写日志到 .log/antece_launcher.log；更新失败不影响启动，启动失败弹窗提示。
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
ANTECE_REPO: str = 'https://github.com/antece1/zzz-onedragon-antece-version.git'
BRANCH: str = 'main'
LOG_PATH: Path = ROOT / '.log' / 'antece_launcher.log'
NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open('a', encoding='utf-8') as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def run(cmd: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout, creationflags=NO_WINDOW)


def update_code() -> bool:
    """
    从 antece1/zzz-onedragon-antece-version 快进到最新代码
    :return: 依赖文件是否有变化
    """
    git = shutil.which('git')
    if git is None or not (ROOT / '.git').exists():
        log('找不到 git 或不是 git 仓库，跳过更新')
        return False
    branch = run([git, 'rev-parse', '--abbrev-ref', 'HEAD']).stdout.strip()
    if branch != BRANCH:
        log(f'当前在 {branch} 分支，不是 {BRANCH}，跳过更新')
        return False
    if run([git, 'status', '--porcelain', '--untracked-files=no']).stdout.strip():
        log('有未提交的改动，跳过更新')
        return False

    old = run([git, 'rev-parse', 'HEAD']).stdout.strip()
    fetch = run([git, 'fetch', ANTECE_REPO, BRANCH], timeout=30)
    if fetch.returncode != 0:
        log(f'拉取失败，用本地代码启动：{fetch.stderr.strip()[-300:]}')
        return False
    merge = run([git, 'merge', '--ff-only', 'FETCH_HEAD'])
    if merge.returncode != 0:
        log(f'无法快进（本地有远程没有的提交），用本地代码启动：{merge.stderr.strip()[-300:]}')
        return False
    new = run([git, 'rev-parse', 'HEAD']).stdout.strip()
    if new == old:
        log(f'已是最新 {new[:8]}')
        return False
    log(f'代码已更新 {old[:8]} -> {new[:8]}')
    changed = run([git, 'diff', '--name-only', old, new]).stdout.split()
    return any(name in ('pyproject.toml', 'uv.lock') for name in changed)


def find_uv() -> str | None:
    """uv 可能不在 pythonw 的 PATH 里，再看 winget 和官方安装脚本的默认位置"""
    candidates = [
        shutil.which('uv'),
        str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Links' / 'uv.exe'),
        str(Path.home() / '.local' / 'bin' / 'uv.exe'),
    ]
    return next((c for c in candidates if c and Path(c).exists()), None)


def sync_deps() -> None:
    uv = find_uv()
    if uv is None:
        log('依赖有变化，但找不到 uv，请在项目目录手动运行 uv sync')
        return
    # --inexact：不卸载手动装的可选依赖（如虚拟手柄）
    result = run([uv, 'sync', '--frozen', '--inexact', '--python', sys.executable], timeout=900)
    log('依赖已同步' if result.returncode == 0 else f'依赖同步失败：{result.stderr.strip()[-500:]}')


def console_python() -> str:
    """同一个 venv 里的 python.exe（pythonw.exe 会被智能应用控制拦截）"""
    python = Path(sys.executable).with_name('python.exe')
    return str(python if python.exists() else sys.executable)


def hide_own_console() -> None:
    """用 python.exe 运行时会带一个黑框，马上藏起来"""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:  # noqa: BLE001 藏不掉也不影响启动
        pass


def start_gui() -> None:
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    # CREATE_NO_WINDOW：python.exe 不弹黑框，界面窗口照常显示
    subprocess.Popen([console_python(), str(ROOT / 'src' / 'zzz_od' / 'gui' / 'app.py')], cwd=ROOT, env=env,
                     creationflags=NO_WINDOW)
    log('已启动一条龙界面')


def ensure_admin() -> bool:
    """
    游戏 ZenlessZoneZero.exe 只能以管理员身份运行。一条龙不是管理员时，Windows 会丢掉它发给游戏的点击和按键，
    截图也只能退回到复制屏幕像素（游戏被别的窗口挡住时就截不到游戏）。
    和官方 OneDragon-Launcher.exe 一样：不是管理员就弹 UAC，以管理员身份重新运行本文件，之后启动的界面也是管理员。
    :return: 已是管理员返回 True；否则已请求重新运行（或被拒绝），返回 False，调用方直接退出
    """
    if ctypes.windll.shell32.IsUserAnAdmin():
        return True
    shell_execute = ctypes.windll.shell32.ShellExecuteW
    shell_execute.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                              ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    shell_execute.restype = ctypes.c_void_p
    # 以管理员重新运行时用 python.exe 并隐藏窗口（SW_HIDE），不弹黑框
    rc = shell_execute(None, 'runas', console_python(), f'"{Path(__file__).resolve()}"', str(ROOT), 0) or 0
    if rc <= 32:  # 返回值不大于 32 表示失败；在 UAC 里点了「否」是 5
        log(f'没有获得管理员权限（ShellExecuteW 返回 {rc}），不启动')
        ctypes.windll.user32.MessageBoxW(
            None, '游戏以管理员身份运行，一条龙也需要管理员权限才能点击游戏。\n请重新打开，并在弹出的提示里选「是」。',
            '绝区零一条龙 antece版', 0x30)
    else:
        log('不是管理员，已请求以管理员身份重新启动')
    return False


def main() -> None:
    hide_own_console()
    if not ensure_admin():
        return
    log(f'---- 启动 antece版（{ROOT}，管理员）')
    try:
        if update_code():
            sync_deps()
    except Exception:
        log('更新出错，用本地代码启动：\n' + traceback.format_exc())
    try:
        start_gui()
    except Exception:
        log('启动失败：\n' + traceback.format_exc())
        ctypes.windll.user32.MessageBoxW(None, f'启动失败，详情见\n{LOG_PATH}', '绝区零一条龙 antece版', 0x10)


if __name__ == '__main__':
    main()
