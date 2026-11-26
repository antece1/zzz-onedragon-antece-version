from __future__ import annotations

from typing import ClassVar

from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.log_utils import log
from zzz_od.application.character_growth.skill_steps import DENNY, EXP
from zzz_od.application.skill_upgrade.agent_skill.agent_level_parse import (
    AgentLevelParse,
    BasicReading,
    LevelDialogReading,
)
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_session import (
    AgentSkillSession,
    SkillStepOutcome,
    SkillStepStatus,
    focus_game,
)
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.zzz_operation import ZOperation

S = SkillStepStatus
P = AgentLevelParse


class UpgradeAgentLevelOnce(ZOperation):
    """
    把代理人升到一步要的等级（开始和正常结束都停在「代理人-技能」，下一步同一代理人的技能不用重新进）。
    技能页点「基础」→ 读「等级NN/上限」→ 够了 / 满级 / 要突破：不点别的，回技能页；
    否则点「>>」→ 确认是「代理人培训」弹窗 → 点「自动添加」（只是选材料，不花）→ 读等级条和 3 个经验材料 →
    演练：点 ✕ 关掉（选的材料作废）；真升级并且核对都通过：在新截图上重读一致、找到白字的「升级」后只点一次 →
    关掉结果弹窗（只点认得的关闭按钮 / 完全符合的「点击空白处关闭」提示）→ 回基础页连读两次等级，必须等于预计 → 回技能页。
    点「升级」之前出的意外（读不出、弹窗不对、画面变了）都关弹窗回技能页，结果是 LEVEL_SKIP（应用跳过这步、不停下）；
    点了「升级」以后出问题才整体停下。结果在 self.outcome。不写任何文件（除了截图）
    """

    SCREEN_SKILL: ClassVar[str] = '代理人-技能'
    SCREEN_BASIC: ClassVar[str] = '代理人-基础'
    SCREEN_DIALOG: ClassVar[str] = '代理人-培训弹窗'
    SCREEN_COMMON: ClassVar[str] = '画面-通用'
    SCREEN_WORLD: ClassVar[str] = '大世界'
    AREA_TAB_BASIC: ClassVar[str] = '按钮-基础'
    AREA_TAB_SKILL: ClassVar[str] = '按钮-技能'
    AREA_LEVEL: ClassVar[str] = '区域-等级'
    AREA_CAP: ClassVar[str] = '区域-等级上限'
    AREA_MAX: ClassVar[str] = '区域-满级'
    AREA_OPEN: ClassVar[str] = '按钮-培训'
    AREA_CONTENT: ClassVar[str] = '区域-培训内容'
    AREA_DENNY: ClassVar[str] = '区域-丁尼'
    AREA_AUTO: ClassVar[str] = '按钮-自动添加'
    AREA_UPGRADE: ClassVar[str] = '按钮-升级'
    AREA_CLOSE: ClassVar[str] = '按钮-关闭'
    AREA_HINT: ClassVar[str] = '区域-全屏提示'
    STATUS_ON_BASIC: ClassVar[str] = '到基础页'
    STATUS_OPENED: ClassVar[str] = '已点>>'
    STATUS_ADDED: ClassVar[str] = '已点自动添加'
    STATUS_CLICKED: ClassVar[str] = '已点升级'
    WHERE_DIALOG: ClassVar[str] = 'dialog'
    WHERE_CONFIRM: ClassVar[str] = 'confirm'
    WHERE_POPUP: ClassVar[str] = 'popup'
    WHERE_BASIC: ClassVar[str] = 'basic'
    WHERE_SKILL: ClassVar[str] = 'skill'

    def __init__(self, ctx: ZContext, session: AgentSkillSession, step: dict, goal_lv: int | None = None,
                 allow_overshoot: bool = False) -> None:
        ZOperation.__init__(self, ctx, op_name=f"升级 {step.get('name_cn') or step['agent']} 等级",
                            timeout_seconds=120)
        self.session: AgentSkillSession = session
        self.step: dict = step
        self.agent: str = step['agent']
        self.need: int = int(step['to'])
        self.step_exp: int = int((step.get('cost') or {}).get(EXP, 0) or 0)
        self.goal_lv: int | None = goal_lv
        self.allow_overshoot: bool = allow_overshoot
        self._reset_state()

    def _reset_state(self) -> None:
        self.outcome: SkillStepOutcome = SkillStepOutcome()
        self.basic: BasicReading | None = None
        self.reading0: LevelDialogReading | None = None
        self.reading1: LevelDialogReading | None = None
        self.open_clicked: bool = False
        self.auto_clicked: bool = False
        self.confirm_clicked: bool = False
        self.after_shot_done: bool = False
        self.after_same_seen: int = 0
        self.not_applied: bool = False
        self.cancelled: bool = False
        self.verify_prev: int | None = None

    def handle_init(self) -> None:
        self._reset_state()

    # ---------- 基础页 ----------
    @operation_node(name='去基础页', is_start_node=True, node_max_retry_times=2)
    def goto_basic(self) -> OperationRoundResult:
        if self.step.get('what') != 'level':
            return self._fail(S.REHEARSAL_GUARD, f"这一步不是升级代理人（{self.step.get('what')}）", 'not_level_step')
        if self._is(self.SCREEN_BASIC):
            return self.round_success(self.STATUS_ON_BASIC)
        if not self._is(self.SCREEN_SKILL):
            return self._retry_or_fail(S.SCREEN_WRONG, '开始升级代理人时不在技能页', 'level_not_skill_page')
        focus_game(self.ctx)
        result = self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_SKILL, self.AREA_TAB_BASIC,
                                                   pre_delay=0, success_wait=1.5)
        if result.is_success:
            return self.round_success(self.STATUS_ON_BASIC, wait=1.5)
        return self._retry_or_skip(f'技能页找不到「基础」（{result.status}）', 'no_basic_tab')

    @node_from(from_name='去基础页')
    @operation_node(name='读基础页', node_max_retry_times=3)
    def read_basic(self) -> OperationRoundResult:
        if not self._is(self.SCREEN_BASIC):
            return self._retry_or_skip('点了「基础」但没到基础页', 'level_no_basic_page')
        basic, chevrons = self._read_basic()
        status, msg = P.classify_basic(basic, self.need, chevrons)
        self.outcome.agent_level = {'basic': basic.level, 'cap': basic.cap, 'maxed': basic.maxed, 'chevrons': chevrons,
                                    'need': self.need, 'after': None}
        log.info(f"{self.step.get('name_cn')} 基础页：{basic.raw}，「>>」绿色像素 {chevrons}")
        if status == S.LEVELS_UNREADABLE:
            return self._retry_or_skip(msg, 'level_unreadable')
        self.basic = basic
        if basic.level != self.step.get('from'):
            log.info(f"基础页等级 {basic.level}，步骤写的是 {self.step.get('from')}（按游戏里的算）")
        if status == P.OPEN:
            return self.round_success(P.OPEN)
        return self._finish(status, msg)

    @node_from(from_name='读基础页', status=AgentLevelParse.OPEN)
    @operation_node(name='打开培训', node_max_retry_times=1)
    def open_dialog(self) -> OperationRoundResult:
        if self.open_clicked:  # 点过一次「>>」就不再点（第二次会点在弹窗里）
            return self.round_success(self.STATUS_OPENED)
        if self._is(self.SCREEN_DIALOG):
            return self.round_success(self.STATUS_OPENED)
        if not self._is(self.SCREEN_BASIC):
            return self._skip('点「>>」前不在基础页', 'level_open_not_basic')
        again, chevrons = self._read_basic()
        if again.level != self.basic.level or chevrons < P.CHEVRON_MIN:
            return self._skip(f'点「>>」前基础页变了（{again.raw}，绿色像素 {chevrons}）', 'level_open_changed')
        focus_game(self.ctx)
        self.open_clicked = True
        self.round_by_click_area(self.SCREEN_BASIC, self.AREA_OPEN)
        return self.round_success(self.STATUS_OPENED, wait=1.5)

    @node_from(from_name='打开培训')
    @operation_node(name='确认培训弹窗', node_max_retry_times=3)
    def confirm_dialog(self) -> OperationRoundResult:
        if self._is(self.SCREEN_DIALOG):
            r0 = self._read_dialog()
            problem = P.check_opened(r0, self.basic.level)
            if problem is not None:
                return self._retry_or_skip(f'代理人培训弹窗不对：{problem}', f'{self.agent}_level_dialog_wrong')
            self.reading0 = r0
            self.outcome.agent_level['exp'] = list(r0.exp)
            log.info(f'代理人培训：{P.describe(r0)}')
            return self.round_success('代理人培训')
        last = self.node_retry_times >= self.node_max_retry_times
        if self.round_by_find_area(self.last_screenshot, self.SCREEN_COMMON, '关闭').is_success:
            if not last:
                return self.round_retry('「>>」打开了别的弹窗？再看一次', wait=1)
            path = self.session.shot(f'{self.agent}_level_unknown_dialog', self.last_screenshot)
            self.outcome.status = S.DIALOG_UNKNOWN
            self.outcome.message = f"「>>」打开的不是代理人培训（可能要突破），没有点别的，截图 {path.name if path else '无'}"
            log.info(self.outcome.message)
            return self.round_success(S.DIALOG_UNKNOWN)
        return self._retry_or_skip('点了「>>」没打开代理人培训', f'{self.agent}_level_no_dialog')

    @node_from(from_name='确认培训弹窗')
    @operation_node(name='自动添加', node_max_retry_times=1)
    def auto_add(self) -> OperationRoundResult:
        if self.auto_clicked:  # 点第二次可能把选好的材料改掉
            return self.round_success(self.STATUS_ADDED)
        if not self._is(self.SCREEN_DIALOG):
            return self._skip('点「自动添加」前弹窗不见了', f'{self.agent}_level_auto_no_dialog')
        btn = P.exact_button(self._ocr(self.SCREEN_DIALOG, self.AREA_AUTO), '自动添加')
        if btn is None:
            return self._skip('培训弹窗里找不到「自动添加」', f'{self.agent}_level_no_auto')
        focus_game(self.ctx)
        self.auto_clicked = True
        self.ctx.controller.click(btn.center)
        return self.round_success(self.STATUS_ADDED, wait=1.5)

    @node_from(from_name='自动添加')
    @operation_node(name='读添加后', node_max_retry_times=3)
    def read_added(self) -> OperationRoundResult:
        if not self._is(self.SCREEN_DIALOG):
            return self._retry_or_skip('点「自动添加」后不是代理人培训弹窗', f'{self.agent}_level_after_auto')
        r1 = self._read_dialog()
        problem = P.check_added(self.reading0, r1, self.basic.cap)
        if problem is not None:
            return self._retry_or_skip(problem, f'{self.agent}_level_{self.basic.level}_added_wrong')
        self.reading1 = r1
        path = self.session.shot(f'{self.agent}_level_{self.basic.level}_auto_added', self.last_screenshot)
        self.outcome.shot = path.name if path else None
        d = P.decide(self.reading0, r1, self.basic, self.need, self.goal_lv, self.step_exp, self.session.apply,
                     self.session.dennies_reserve, self.allow_overshoot, known_dennies=self.session.dennies_last)
        if d.status == S.CONFIRM and not self.session.apply:  # decide 不会这样返回，这里再拦一次
            d.status, d.message = S.REHEARSED, f'演练：{d.message}'
        self.outcome.dialog = {name: {'have': o, 'need': a} for name, (a, o) in zip(P.EXP_ITEMS, r1.slots)}
        self.outcome.agent_level.update(target=r1.target_lv, exp_used=d.exp_used, exp_budget=d.exp_budget,
                                        added=r1.added, owned=r1.owned, exp_after_add=list(r1.exp),
                                        den_pair=list(r1.den_pair) if r1.den_pair else None, extra=list(r1.extra))
        self.outcome.partial = d.partial
        self.outcome.short = d.status == S.SHORT or d.partial
        if d.den_short:  # 缺的是丁尼：刷取队列记丁尼（总共要有的数），不记调查员记录
            self.outcome.needs = [(DENNY, d.den_cost)]
        elif self.outcome.short:
            self.outcome.needs = P.shortage_needs(self.reading0, r1, self.need, self.basic.cap, applied=False)
        self.outcome.status, self.outcome.message = d.status, d.message
        log.info(f'等级 {self.basic.level}→{self.need}：{d.status} {d.message}')
        return self.round_success(d.status)

    # ---------- 点升级 ----------
    @node_from(from_name='读添加后', status=SkillStepStatus.CONFIRM)  # 唯一进入「点升级」的边
    @operation_node(name='点升级', node_max_retry_times=1)
    def click_upgrade(self) -> OperationRoundResult:
        if self.confirm_clicked:  # 绝不点第二次
            return self.round_success(self.STATUS_CLICKED)
        if (not self.session.apply or self.outcome.status != S.CONFIRM or self.reading1 is None
                or self.reading0 is None):
            return self._fail(S.REHEARSAL_GUARD, '没有通过核对，不能点升级', 'level_rehearsal_guard')
        again = self._read_dialog() if self._is(self.SCREEN_DIALOG) else None
        if not P.same_reading(again, self.reading1):
            return self._skip(f'点升级前重新读弹窗，和刚才不一样（{P.describe(again)}），没有点',
                              f'{self.agent}_level_changed_before_upgrade')
        auto = P.exact_button(self._ocr(self.SCREEN_DIALOG, self.AREA_AUTO), '自动添加')
        up = P.exact_button(self._ocr(self.SCREEN_DIALOG, self.AREA_UPGRADE), '升级')
        if up is None:  # 区域只认白字：灰的「升级」读不到
            p = self.session.shot(f'{self.agent}_level_upgrade_disabled', self.last_screenshot)
            self.outcome.status = S.CONFIRM_DISABLED
            self.outcome.message = f"「升级」是灰的，没有点（{self.outcome.message}），截图 {p.name if p else '无'}"
            return self.round_success(S.CONFIRM_DISABLED)
        problem = P.upgrade_button_ok(auto, up)
        if problem is not None:
            return self._skip(f'{problem}，没有点', f'{self.agent}_level_upgrade_button_wrong')
        focus_game(self.ctx)
        self.confirm_clicked = True  # 点之前就记下：点击后出任何异常都不会再点
        self.outcome.confirm_clicked = True
        self.session.dennies_last = None  # 升级可能花了丁尼：下一次技能弹窗读到之前不再用旧的数
        log.info(f"确认升级 {self.step.get('name_cn')} 等级 {self.reading1.from_lv}→{self.reading1.target_lv}")
        if not self.ctx.controller.click(up.center):  # 不知道到底点没点，按点过处理（停下，不再点）
            return self._fail(S.SCREEN_CHANGED, '点升级失败', 'level_upgrade_click_fail')
        return self.round_success(self.STATUS_CLICKED, wait=2.5)

    @node_from(from_name='点升级')
    @operation_node(name='升级后收尾', node_max_retry_times=8)
    def after_upgrade(self) -> OperationRoundResult:
        if not self.after_shot_done:
            self.after_shot_done = True
            self.session.shot(f'{self.agent}_level_{self.reading1.from_lv}_after_upgrade', self.last_screenshot)
        where = self._where(confirm=True)
        if where == self.WHERE_BASIC:
            return self.round_success('回到基础页')
        if self.node_retry_times >= self.node_max_retry_times:
            return self._fail(S.SCREEN_WRONG, f'点升级后回不到基础页（{where}）', 'level_after_upgrade_fail')
        if where == self.WHERE_DIALOG:
            nxt = self._read_dialog()
            if nxt.from_lv == self.reading1.from_lv and self.after_same_seen < 1:
                self.after_same_seen += 1  # 可能还在动画：等一轮再看
                return self.round_retry('升级后弹窗还是原来的等级，等一下', wait=1)
            if nxt.from_lv == self.reading1.from_lv:
                self.not_applied = True
                log.error('点升级后弹窗还是同一级，升级可能没有生效')
            focus_game(self.ctx)
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_DIALOG, self.AREA_CLOSE, pre_delay=0)
        elif where == self.WHERE_CONFIRM:  # 「确认 / 取消」框（例如经验溢出提示）：只点取消，不替用户确认
            focus_game(self.ctx)
            self.cancelled = True
            self.not_applied = True
            log.error('点升级后弹出确认框，点了取消')
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_WORLD, '对话框取消', pre_delay=0)
        elif where == self.WHERE_POPUP:
            focus_game(self.ctx)
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_COMMON, '关闭', pre_delay=0)
        else:  # 认不出的画面：只点完全符合的「点击空白处关闭」提示本身，绝不盲点
            hint = P.close_hint(self._ocr(self.SCREEN_DIALOG, self.AREA_HINT))
            if hint is not None:
                focus_game(self.ctx)
                self.ctx.controller.click(hint.center)
        return self.round_retry(f'升级后回基础页（当前 {where}）', wait=1)

    @node_from(from_name='升级后收尾')
    @operation_node(name='核对', node_max_retry_times=4)
    def verify(self) -> OperationRoundResult:
        if self._is(self.SCREEN_DIALOG) or not self._is(self.SCREEN_BASIC):
            return self._retry_or_fail(S.VERIFY_FAILED, '升级后不在基础页', 'level_verify_not_basic')
        basic, _ = self._read_basic()
        if basic.level is None:
            return self._retry_or_fail(S.VERIFY_FAILED, f'升级后读不到等级（{basic.raw}）', 'level_verify_unreadable')
        if basic.level != self.verify_prev:  # 连着两张截图读到同一个等级才算（升级动画可能还没完）
            self.verify_prev = basic.level
            return self._retry_or_fail(S.VERIFY_FAILED, f'升级后等级 {basic.level}，再读一次', 'level_verify_unstable')
        r0, r1 = self.reading0, self.reading1
        self.outcome.agent_level['after'] = basic.level
        if basic.level != r1.target_lv:
            if basic.level == r1.from_lv and (self.not_applied or self.cancelled):
                why = '弹出确认框，点了取消' if self.cancelled else '弹窗还是同一级'
                return self._fail(S.VERIFY_FAILED, f'点了升级但没有生效（{why}），还是 {basic.level} 级，停下检查',
                                  'level_not_applied')
            return self._fail(S.VERIFY_FAILED, f'升级后是 {basic.level} 级，应该是 {r1.target_lv} 级，停下检查',
                              'level_verify_failed')
        # 等级已经到了：材料确实花掉了
        self.outcome.spent = {name: a for name, a in zip(P.EXP_ITEMS, r1.added) if a}
        if r1.den_pair is not None:
            self.outcome.dennies_unverified = True  # 按钮行的数字格式没确认过，只记在 agent_level 里，不算进花费
        if self.outcome.partial:
            self.outcome.needs = P.shortage_needs(r0, r1, self.need, self.basic.cap, applied=True)
        part = f'，还没到要的 {self.need} 级' if self.outcome.partial else ''
        return self._finish(S.DONE, f'等级 {r1.from_lv}→{basic.level} 完成，用 {r1.exp_used:,} 经验（{P.describe(r1)}）{part}')

    # ---------- 回技能页 ----------
    @node_from(from_name='去基础页', status=SkillStepStatus.LEVEL_SKIP)
    @node_from(from_name='读基础页')  # 已够 / 满级 / 要突破 / 读不出：不点别的
    @node_from(from_name='打开培训', status=SkillStepStatus.LEVEL_SKIP)
    @node_from(from_name='确认培训弹窗', status=SkillStepStatus.LEVEL_SKIP)
    @node_from(from_name='确认培训弹窗', status=SkillStepStatus.DIALOG_UNKNOWN)
    @node_from(from_name='自动添加', status=SkillStepStatus.LEVEL_SKIP)
    @node_from(from_name='读添加后')  # 除了「点升级」，其它决定都是关弹窗（选的材料作废）回技能页
    @node_from(from_name='点升级', status=SkillStepStatus.CONFIRM_DISABLED)
    @node_from(from_name='点升级', status=SkillStepStatus.LEVEL_SKIP)
    @node_from(from_name='核对')
    @operation_node(name='回技能页', node_max_retry_times=6)
    def back_to_skill_page(self) -> OperationRoundResult:
        where = self._where(confirm=False)
        if where == self.WHERE_SKILL:
            return self.round_success(self.outcome.status)
        if self.node_retry_times >= self.node_max_retry_times:
            return self._fail(S.SCREEN_WRONG, f'回不到技能页（{where}）', 'level_back_to_skill_fail')
        focus_game(self.ctx)
        if where == self.WHERE_DIALOG:
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_DIALOG, self.AREA_CLOSE, pre_delay=0)
        elif where == self.WHERE_POPUP:
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_COMMON, '关闭', pre_delay=0)
        elif where == self.WHERE_BASIC:
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_BASIC, self.AREA_TAB_SKILL, pre_delay=0)
        return self.round_retry(f'回技能页（当前 {where}）', wait=1)

    @node_from(from_name='回技能页')
    @operation_node(name='完成', screenshot_before_round=False)
    def done(self) -> OperationRoundResult:
        return self.round_success(self.outcome.status, data=self.outcome)

    # ---------- 内部 ----------
    def _is(self, screen_name: str) -> bool:
        """
        一次只判断一个画面（一条龙按加载顺序判断列表里的画面，没压暗的弹窗后面基础页也认得出，所以弹窗要单独先判断）。
        保留这个小包装是为了让每处都只传一个画面，免得以后有人传整个列表
        """
        return self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[screen_name]) == screen_name

    def _where(self, confirm: bool) -> str | None:
        """先认弹窗（培训弹窗 → 确认框 → 任何带 ✕ 的弹窗），最后才认基础页、技能页"""
        if self._is(self.SCREEN_DIALOG):
            return self.WHERE_DIALOG
        if confirm and all(self.round_by_find_area(self.last_screenshot, self.SCREEN_WORLD, a).is_success
                           for a in ('对话框取消', '对话框确认')):
            return self.WHERE_CONFIRM
        if self.round_by_find_area(self.last_screenshot, self.SCREEN_COMMON, '关闭').is_success:
            return self.WHERE_POPUP
        if self._is(self.SCREEN_BASIC):
            return self.WHERE_BASIC
        if self._is(self.SCREEN_SKILL):
            return self.WHERE_SKILL
        return None

    def _ocr(self, screen_name: str, area_name: str) -> list:
        """
        区域里的全部 OCR 结果（带区域的颜色过滤）。round_by_ocr_and_click 按 lcs 模糊匹配、点第一个，
        做不到「正好一个、完全等于」（「升级」会匹配到「可升级」「升级材料」），所以「自动添加」「升级」和关闭提示
        都用这里的结果自己核对后再点它的中心
        """
        area = self.ctx.screen_loader.get_area(screen_name, area_name)
        return self.ctx.ocr_service.get_ocr_result_list(self.last_screenshot, rect=area.rect,
                                                        color_range=area.color_range)

    def _read_basic(self) -> tuple[BasicReading, int]:
        basic = P.parse_basic(self._ocr(self.SCREEN_BASIC, self.AREA_LEVEL), self._ocr(self.SCREEN_BASIC, self.AREA_CAP),
                              self._ocr(self.SCREEN_BASIC, self.AREA_MAX))
        rect = self.ctx.screen_loader.get_area(self.SCREEN_BASIC, self.AREA_OPEN).rect
        return basic, P.chevron_pixels(self.last_screenshot, rect)

    def _read_dialog(self) -> LevelDialogReading:
        return P.parse_level_dialog(self._ocr(self.SCREEN_DIALOG, self.AREA_CONTENT),
                                    self._ocr(self.SCREEN_DIALOG, self.AREA_DENNY))

    def _finish(self, status: str, message: str) -> OperationRoundResult:
        self.outcome.status, self.outcome.message = status, message
        log.info(f"{self.step.get('name_cn')} 等级 →{self.need}：{status} {message}")
        return self.round_success(status)

    def _skip(self, message: str, shot_label: str) -> OperationRoundResult:
        """点「升级」之前的意外：截图，关弹窗回技能页，这一步跳过（应用不停下）"""
        if self.confirm_clicked:
            return self._fail(S.SCREEN_WRONG, message, shot_label)
        path = self.session.shot(shot_label, self.last_screenshot)
        self.outcome.status = S.LEVEL_SKIP
        self.outcome.message = f"{message}，截图 {path.name if path else '无'}"
        log.error(f'{S.LEVEL_SKIP}：{self.outcome.message}')
        return self.round_success(S.LEVEL_SKIP)

    def _retry_or_skip(self, message: str, shot_label: str) -> OperationRoundResult:
        if self.node_retry_times >= self.node_max_retry_times:
            return self._skip(message, shot_label)
        return self.round_retry(message, wait=1)

    def _fail(self, status: str, message: str, shot_label: str) -> OperationRoundResult:
        path = self.session.shot(shot_label, self.last_screenshot)
        self.outcome.status = status
        self.outcome.message = f"{message}，截图 {path.name if path else '无'}"
        log.error(f'{status}：{self.outcome.message}')
        return self.round_fail(status, data=self.outcome)

    def _retry_or_fail(self, status: str, message: str, shot_label: str) -> OperationRoundResult:
        if self.node_retry_times >= self.node_max_retry_times:
            return self._fail(status, message, shot_label)
        return self.round_retry(message, wait=1)
