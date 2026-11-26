from __future__ import annotations

from typing import ClassVar

from one_dragon.base.operation.operation import Operation
from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.log_utils import log
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_session import AgentSkillSession, focus_game
from zzz_od.application.skill_upgrade.agent_skill.open_agent_list import OpenAgentList
from zzz_od.application.skill_upgrade.agent_skill.select_agent import SelectAgent
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.zzz_operation import ZOperation


class GotoAgentSkillPage(ZOperation):
    """
    进入某个代理人的技能页（停在「代理人-技能」）。上一步已经停在他的技能页就不动；
    否则打开代理人列表 → 选中他 → 点「技能」。
    找不到这个代理人时成功结束并返回 SelectAgent.STATUS_AGENT_NOT_FOUND（还停在列表）。
    本身不设超时：打开列表、选代理人两个子操作各自有超时和次数上限（父操作超时比子操作短会把「找不到」变成整体失败）
    """

    STATUS_ON_SKILL_PAGE: ClassVar[str] = '已在技能页'
    STATUS_REENTER: ClassVar[str] = '重新进入'
    STATUS_SKILL_TAB_FAIL: ClassVar[str] = '点了「技能」但没进到技能页'

    SCREEN_LIST: ClassVar[str] = '代理人-列表'
    SCREEN_SKILL: ClassVar[str] = '代理人-技能'
    AREA_SKILL_BUTTON: ClassVar[str] = '按钮-技能'

    def __init__(self, ctx: ZContext, session: AgentSkillSession, step: dict) -> None:
        ZOperation.__init__(self, ctx, op_name=f"进入 {step.get('name_cn') or step['agent']} 的技能页",
                            timeout_seconds=-1)
        self.session: AgentSkillSession = session
        self.step: dict = step
        self.result_status: str = self.STATUS_ON_SKILL_PAGE
        self.result_data: str | None = None

    def handle_init(self) -> None:
        self.result_status = self.STATUS_ON_SKILL_PAGE
        self.result_data = None

    @operation_node(name='检查当前页', is_start_node=True)
    def check_current(self) -> OperationRoundResult:
        if self.session.at_skill_page == self.step['agent']:
            name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_SKILL])
            if name == self.SCREEN_SKILL:
                log.info(f"还在 {self.step.get('name_cn')} 的技能页")
                return self.round_success(self.STATUS_ON_SKILL_PAGE)
        self.session.forget_position()
        return self.round_success(self.STATUS_REENTER)

    @node_from(from_name='检查当前页')
    @operation_node(name='打开列表', screenshot_before_round=False)
    def open_list(self) -> OperationRoundResult:
        op = OpenAgentList(self.ctx, self.session)
        result = op.execute()
        if not result.success:
            self.result_data = str(result.data) if result.data else None
            return self.round_fail(result.status, data=self.result_data)
        return self.round_success(result.status)

    @node_from(from_name='打开列表')
    @operation_node(name='选择代理人', screenshot_before_round=False)
    def select_agent(self) -> OperationRoundResult:
        op = SelectAgent(self.ctx, self.session, self.step.get('name_cn') or self.step['agent'],
                         self.step['agent'], self.step.get('agent_lv'))
        result = op.execute()
        if (not result.success and result.status == Operation.STATUS_TIMEOUT
                and not self.ctx.run_context.is_context_stop):  # 找了太久：当作找不到（下一步重新从列表进）
            path = self.session.shot('select_agent_timeout', op.last_screenshot)
            self.result_status = SelectAgent.STATUS_AGENT_NOT_FOUND
            self.result_data = (f"找 {op.name_cn} 超时（点了 {op.clicks} 张卡，翻了 {op.scrolls} 次），"
                                f"截图 {path.name if path else '无'}")
            return self.round_success(SelectAgent.STATUS_AGENT_NOT_FOUND)
        if not result.success:
            self.result_data = op.message or None
            return self.round_fail(result.status, data=self.result_data)
        if result.status == SelectAgent.STATUS_AGENT_NOT_FOUND:
            self.result_status = SelectAgent.STATUS_AGENT_NOT_FOUND
            self.result_data = op.message or result.data
        return self.round_success(result.status)

    @node_from(from_name='选择代理人', status=SelectAgent.STATUS_SELECTED)
    @operation_node(name='点技能', node_max_retry_times=2)
    def click_skill(self) -> OperationRoundResult:
        focus_game(self.ctx)
        result = self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_LIST, self.AREA_SKILL_BUTTON,
                                                   pre_delay=0, success_wait=1.5)
        if result.is_success:
            return result
        if self.node_retry_times >= self.node_max_retry_times:
            return self._fail_tab()
        return self.round_retry(result.status, wait=1)

    @node_from(from_name='点技能')
    @operation_node(name='确认技能页', node_max_retry_times=3)
    def confirm_skill_page(self) -> OperationRoundResult:
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_SKILL])
        if name == self.SCREEN_SKILL:
            self.session.at_skill_page = self.step['agent']
            self.result_status = self.STATUS_ON_SKILL_PAGE
            return self.round_success(self.STATUS_ON_SKILL_PAGE)
        if self.node_retry_times >= self.node_max_retry_times:
            return self._fail_tab()
        return self.round_retry('等技能页', wait=1)

    @node_from(from_name='检查当前页', status=STATUS_ON_SKILL_PAGE)
    @node_from(from_name='选择代理人', status=SelectAgent.STATUS_AGENT_NOT_FOUND)
    @node_from(from_name='确认技能页')
    @operation_node(name='完成', screenshot_before_round=False)
    def done(self) -> OperationRoundResult:
        return self.round_success(self.result_status, data=self.result_data)

    def _fail_tab(self) -> OperationRoundResult:
        path = self.session.shot('skill_tab_fail', self.last_screenshot)
        self.result_data = f"截图 {path.name if path else '无'}"
        return self.round_fail(self.STATUS_SKILL_TAB_FAIL, data=self.result_data)
