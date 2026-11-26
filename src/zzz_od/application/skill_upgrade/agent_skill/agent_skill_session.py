from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from one_dragon.utils import cv2_utils, os_utils
from one_dragon.utils.log_utils import log

if TYPE_CHECKING:
    from cv2.typing import MatLike

    from zzz_od.context.zzz_context import ZContext

FOCUS_SETTLE_SECONDS: float = 0.2


def focus_game(ctx: ZContext) -> None:
    """
    每次按键、点击、滚动前调用：把游戏窗口切到最前（不在最前时按 C 会打到别的窗口，2026-11-23 实测）。
    原来不在最前的，切过去后等 0.2 秒再操作（和代理人升级的 focus 一样）
    """
    controller = ctx.controller
    try:
        was_active = bool(controller.game_win.is_win_active)
    except Exception:  # noqa: BLE001 读不到窗口状态就当作不在最前
        was_active = False
    controller.active_window()
    if not was_active:
        time.sleep(FOCUS_SETTLE_SECONDS)


class SkillStepStatus:
    """一步技能强化的结果状态（操作返回的 status，应用按它分支）"""

    # 正常结束，游戏停在技能页
    DONE: ClassVar[str] = '升级完成'
    REHEARSED: ClassVar[str] = '演练完成'
    SIMULATED: ClassVar[str] = '演练模拟'
    SHORT: ClassVar[str] = '材料不足'
    AGENT_LV_LOW: ClassVar[str] = '代理人等级不足'
    MAXED: ClassVar[str] = '技能已到上限'
    LEVEL_MISMATCH: ClassVar[str] = '等级对不上'
    COST_MISMATCH: ClassVar[str] = '花费对不上'
    CONFIRM_DISABLED: ClassVar[str] = '确定按钮不可点'
    RESERVE: ClassVar[str] = '丁尼到保留线'
    CONFIRM: ClassVar[str] = '点确定'  # 读弹窗后的决定：真升级、核对都通过
    # 材料不够时自动转化（游戏在强化弹窗给出「转化获取」）
    CONVERT: ClassVar[str] = '转化材料'  # 读弹窗后的决定：先打开转化弹窗（内部状态，和 CONFIRM 一样）
    CONVERT_REHEARSED: ClassVar[str] = '演练：可以转化'  # 演练：打开转化弹窗看过后取消；应用当作完成
    CONVERT_SKIPPED: ClassVar[str] = '转化不了'  # 转化核对没通过，已取消、关弹窗；应用当作材料不足
    # 失败：截图后整个应用停下
    CONVERT_FAILED: ClassVar[str] = '转化后没升级'  # 已点转化「确认」，但升级没做成或没核对完
    CONVERT_GUARD: ClassVar[str] = '转化核对没通过，不能点'  # 没经过核对就到了点「转化获取」/转化「确认」的节点（不该发生）
    LEVELS_UNREADABLE: ClassVar[str] = '读不到技能等级'
    SCREEN_WRONG: ClassVar[str] = '画面不对'
    DIALOG_WRONG: ClassVar[str] = '强化弹窗不对'
    NO_COST: ClassVar[str] = '强化弹窗里读不到花费'
    SCREEN_CHANGED: ClassVar[str] = '确定前画面变了'
    REHEARSAL_GUARD: ClassVar[str] = '演练模式不能点确定'
    VERIFY_FAILED: ClassVar[str] = '强化后等级不对'
    DENNY_MISMATCH: ClassVar[str] = '丁尼对不上'
    # 升级代理人等级（UpgradeAgentLevelOnce）另外用到的；正常结束时游戏都停在技能页
    ALREADY: ClassVar[str] = '代理人等级已够'
    LEVEL_MAXED: ClassVar[str] = '代理人已满级'
    PROMOTE: ClassVar[str] = '要先突破'
    DIALOG_UNKNOWN: ClassVar[str] = '培训弹窗看不懂'
    TARGET_OVER: ClassVar[str] = '自动添加超过培养目标'
    OVER_BUDGET: ClassVar[str] = '自动添加超过预算'
    LEVEL_SKIP: ClassVar[str] = '升级代理人没做成'  # 点「升级」之前出了意外，已关弹窗回到技能页：跳过，不停下


@dataclass
class SkillStepOutcome:
    """一步技能强化读到和做了什么；应用从操作实例上取（人工结束、超时时操作结果不带数据）"""

    status: str | None = None
    message: str = ''
    levels: dict[str, int] | None = None  # 技能页 5 个技能的显示等级
    dialog: dict[str, dict[str, int]] | None = None  # 强化弹窗里的 {材料: {have, need}}
    need_level: int | None = None
    short: bool = False
    needs: list[tuple[str, int]] = field(default_factory=list)  # 这一级要的材料（总消耗），写刷取队列用
    spent: dict[str, int] | None = None  # 等级核对 +1 后才有
    dennies_after: int | None = None
    dennies_unverified: bool = False  # 确定后弹窗自己关了，没读到升级后的丁尼
    confirm_clicked: bool = False  # 已经点过「确定」（之后出任何问题都不能继续升级）
    # 材料转化：{'opened', 'would', 'confirmed', 'verified', 'from', 'used', 'to', 'made', 'dennies', 'den_before', ...}
    convert: dict | None = None
    conversion_clicked: bool = False  # 已经点过转化弹窗的「确认」（芯片和丁尼可能已经换掉）
    dialog_before: dict | None = None  # 转化前的强化弹窗；点了确认后 dialog 清空，转化后重新读到并核对过才再填
    shot: str | None = None
    # 升级代理人等级：{'basic': 基础页等级, 'cap': 上限, 'exp': [当前, 下一级], 'target': 自动添加后能到的等级,
    #                 'exp_used': 自动添加用的经验, 'exp_budget': 预算, 'after': 升级后核对的等级 | None, ...}
    agent_level: dict | None = None
    partial: bool = False  # 升了但没到这一步要的等级


class AgentSkillSession:
    """
    一次运行里多个操作共用的状态：模式、保留线、截图目录、停在谁的技能页、演练模拟的等级、丁尼。
    由应用在每次运行开始时新建。
    """

    LABEL_BAD_CHARS: ClassVar[re.Pattern] = re.compile(r'[\\/:*?"<>|\s]+')

    def __init__(self, apply: bool, dennies_reserve: int, run_dir: Path, convert: bool = False) -> None:
        self.apply: bool = apply
        self.dennies_reserve: int = dennies_reserve
        self.convert: bool = convert  # 材料不够时自动转化（低一档芯片 3 换 1）
        self.run_dir: Path = run_dir
        self.shot_seq: int = 0
        self.at_skill_page: str | None = None  # 现在停在哪个代理人的技能页（进入时核对过）
        self.sim: dict[tuple[str, str], int] = {}  # 演练时记下「前一步升完后」的等级
        self.dennies_before: int | None = None
        self.dennies_after: int | None = None
        # 最近一次在技能强化弹窗里读到的现有丁尼（培训弹窗按钮行的「a/b」靠它分辨哪个是现有、哪个是花费）
        self.dennies_last: int | None = None

    @staticmethod
    def default_run_dir(stamp: str) -> Path:
        return Path(os_utils.get_path_under_work_dir('.log', 'skill_upgrade')) / stamp

    def forget_position(self) -> None:
        """出了意外以后不再相信「还停在某人的技能页」，下一步重新从代理人列表进"""
        self.at_skill_page = None

    def shot(self, label: str, image: MatLike | None) -> Path | None:
        """关键画面存到 .log/skill_upgrade/<时间>/NNN_<label>.png（文件名里去掉 : 等 Windows 不允许的字符）"""
        if image is None:
            return None
        self.shot_seq += 1
        safe = self.LABEL_BAD_CHARS.sub('_', label).strip('_') or 'shot'
        path = self.run_dir / f'{self.shot_seq:03d}_{safe}.png'
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            cv2_utils.save_image(image, str(path))
            log.info(f'截图 {path}')
        except Exception:  # noqa: BLE001 截图失败不影响流程
            log.error(f'截图失败 {path}', exc_info=True)
        return path

    @staticmethod
    def stamp() -> str:
        return time.strftime('%Y-%m-%d_%H%M%S')
