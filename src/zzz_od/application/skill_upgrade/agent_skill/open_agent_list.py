from __future__ import annotations

from typing import ClassVar

from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.log_utils import log
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_session import AgentSkillSession, focus_game
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.back_to_normal_world import BackToNormalWorld
from zzz_od.operation.zzz_operation import ZOperation


class OpenAgentList(ZOperation):
    """
    打开代理人列表（停在「代理人-列表」画面）。
    - 已在列表：直接结束；在代理人的详情页/技能页/强化弹窗：点返回/关闭退回列表
    - 在大世界-普通：按住 C 0.2 秒（瞬间按下游戏不认，2026-11-23 实测）；没开就点右上角代理人图标（按住 ALT）；再按一次 C
    - 在勘域：先回大世界（ensure_normal_world 会传送回录像店，勘域里按 C 没反应），再按 C
    - 认不出的画面（等几轮还是认不出）：截图后失败，应用整体停下（不在未知画面上按键）
    每次按键、点击前先把游戏窗口切到最前（不在最前时按 C 会打到别的窗口）
    """

    STATUS_OPENED: ClassVar[str] = '已打开代理人列表'
    STATUS_IN_WORLD: ClassVar[str] = '在大世界'
    STATUS_NEED_WORLD: ClassVar[str] = '需要回大世界'
    STATUS_LIST_NOT_OPEN: ClassVar[str] = '打不开代理人列表'
    STATUS_WORLD_FAIL: ClassVar[str] = '回不到大世界'
    STATUS_UNKNOWN_SCREEN: ClassVar[str] = '认不出当前画面'
    STATUS_USER_STOP: ClassVar[str] = '人工结束'

    SCREEN_LIST: ClassVar[str] = '代理人-列表'
    SCREEN_WORLD: ClassVar[str] = '大世界-普通'
    SCREEN_KANYU: ClassVar[str] = '大世界-勘域'
    # 代理人详情类画面 -> 退回用的区域
    BACK_AREA: ClassVar[dict[str, str]] = {
        '代理人-技能强化弹窗': '按钮-关闭',
        '代理人-技能详情': '按钮-返回',
        '代理人-技能': '按钮-返回',
        '代理人-基础': '按钮-返回',
    }
    # 打开列表的尝试：按 C、点右上角代理人图标（大世界-普通.预备编队 就是 C 键的图标，画面配置了 pc_alt）、再按 C
    OPEN_ATTEMPTS: ClassVar[tuple[str, ...]] = ('key', 'hud', 'key')
    KEY_AGENT_LIST: ClassVar[str] = 'c'
    KEY_PRESS_TIME: ClassVar[float] = 0.2
    HUD_AREA: ClassVar[str] = '预备编队'
    WORLD_TIMEOUT_SECONDS: ClassVar[float] = 120

    def __init__(self, ctx: ZContext, session: AgentSkillSession) -> None:
        ZOperation.__init__(self, ctx, op_name='打开代理人列表', timeout_seconds=180)
        self.session: AgentSkillSession = session
        self.open_attempts: int = 0

    def handle_init(self) -> None:
        self.open_attempts = 0

    def _screen_list(self) -> list[str]:
        return [self.SCREEN_LIST, *self.BACK_AREA.keys(), self.SCREEN_WORLD, self.SCREEN_KANYU]

    @operation_node(name='识别画面', is_start_node=True, node_max_retry_times=6)
    def check_screen(self) -> OperationRoundResult:
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=self._screen_list())
        if name == self.SCREEN_LIST:
            return self.round_success(self.STATUS_OPENED)
        if name == self.SCREEN_WORLD:
            return self.round_success(self.STATUS_IN_WORLD)
        if name == self.SCREEN_KANYU:
            log.info('在勘域，先回大世界')
            return self.round_success(self.STATUS_NEED_WORLD)
        last_try = self.node_retry_times >= self.node_max_retry_times
        if name in self.BACK_AREA and not last_try:
            focus_game(self.ctx)
            self.round_by_find_and_click_area(self.last_screenshot, name, self.BACK_AREA[name], pre_delay=0)
            return self.round_retry(f'从 {name} 退回', wait=1.5)
        if not last_try:
            return self.round_retry(f'认不出当前画面（{name}），等一下再看', wait=1)
        path = self.session.shot('unknown_screen', self.last_screenshot)
        log.error(f'{self.STATUS_UNKNOWN_SCREEN}（{name}），截图 {path}')
        return self.round_fail(self.STATUS_UNKNOWN_SCREEN, data=f"截图 {path.name if path else '无'}")

    @node_from(from_name='识别画面', status=STATUS_NEED_WORLD)
    @operation_node(name='回大世界', node_max_retry_times=0)
    def back_to_world(self) -> OperationRoundResult:
        op = BackToNormalWorld(self.ctx, ensure_normal_world=True, timeout_seconds=self.WORLD_TIMEOUT_SECONDS)
        result = op.execute()
        if self.ctx.run_context.is_context_stop:  # 回大世界途中按了停止：记成人工结束，不截图
            return self.round_fail(self.STATUS_USER_STOP)
        if not result.success:
            self.session.shot('back_to_world_fail', self.last_screenshot)
            return self.round_fail(self.STATUS_WORLD_FAIL)
        return self.round_success(self.STATUS_IN_WORLD, wait=1)

    @node_from(from_name='识别画面', status=STATUS_IN_WORLD)
    @node_from(from_name='回大世界')
    @operation_node(name='打开代理人列表', node_max_retry_times=5)
    def open_list(self) -> OperationRoundResult:
        name = self.check_and_update_current_screen(
            self.last_screenshot, screen_name_list=[self.SCREEN_LIST, self.SCREEN_WORLD, self.SCREEN_KANYU])
        if name == self.SCREEN_LIST:
            return self.round_success(self.STATUS_OPENED)
        if self.node_retry_times >= self.node_max_retry_times:
            path = self.session.shot('agent_list_not_open', self.last_screenshot)
            log.error(f'打不开代理人列表，截图 {path}')
            return self.round_fail(self.STATUS_LIST_NOT_OPEN)
        if name != self.SCREEN_WORLD or self.open_attempts >= len(self.OPEN_ATTEMPTS):
            return self.round_retry(f'等代理人列表（当前 {name}）', wait=1)  # 不在大世界不按键、不点击
        how = self.OPEN_ATTEMPTS[self.open_attempts]
        self.open_attempts += 1
        focus_game(self.ctx)
        if how == 'hud':
            log.info('按 C 没打开代理人列表，改点右上角的代理人图标')
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_WORLD, self.HUD_AREA, pre_delay=0)
        else:
            self.ctx.controller.btn_press(self.KEY_AGENT_LIST, press_time=self.KEY_PRESS_TIME)
        return self.round_retry(f'第 {self.open_attempts} 次打开代理人列表', wait=1.5)

    @node_from(from_name='识别画面', status=STATUS_OPENED)
    @operation_node(name='完成', screenshot_before_round=False)
    def done(self) -> OperationRoundResult:
        return self.round_success(self.STATUS_OPENED)
