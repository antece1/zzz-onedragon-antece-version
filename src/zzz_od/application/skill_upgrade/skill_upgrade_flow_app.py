from __future__ import annotations

import json
import time
from pathlib import Path
from typing import ClassVar

from one_dragon.base.operation.application import application_const
from one_dragon.base.operation.operation_base import OperationResult
from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.log_utils import log
from zzz_od.application.character_growth.planner_core import (
    EXP_A,
    PlannerState,
    effective_own,
    load_bag_adjust,
    observe_bag,
)
from zzz_od.application.character_growth.skill_steps import (
    DENNY,
    bag_source_text,
    build_skill_steps,
    format_steps,
    load_level_gates,
    load_priority,
    record_level_gate,
    results_dir,
    skill_bag,
    write_verified_level,
    write_verified_skill,
)
from zzz_od.application.skill_upgrade import skill_upgrade_const
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_data import (
    add_to_queue,
    describe,
    remove_from_queue,
    remove_from_queue_prefix,
)
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_session import (
    AgentSkillSession,
    SkillStepOutcome,
    SkillStepStatus,
    focus_game,
)
from zzz_od.application.skill_upgrade.agent_skill.goto_agent_skill_page import GotoAgentSkillPage
from zzz_od.application.skill_upgrade.agent_skill.select_agent import SelectAgent
from zzz_od.application.skill_upgrade.agent_skill.upgrade_agent_level_once import UpgradeAgentLevelOnce
from zzz_od.application.skill_upgrade.agent_skill.upgrade_skill_once import UpgradeSkillOnce
from zzz_od.application.skill_upgrade.skill_upgrade_config import SkillUpgradeConfig
from zzz_od.application.zzz_application import ZApplication
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.back_to_normal_world import BackToNormalWorld


class SkillUpgradeApp(ZApplication):
    """
    角色技能提升：每次运行按「角色培养」的数据（计入的代理人、★ 优先、培养目标、背包）自动算出要升的技能，
    在游戏里一级一级地强化。默认只演练（不点确定）；设置里打开「真的升级」后才花材料和丁尼。
    游戏内部分用一条龙的流程引擎：每步 = 进入技能页（GotoAgentSkillPage）+ 强化一级（UpgradeSkillOnce）
    或升级代理人等级（UpgradeAgentLevelOnce，技能要的代理人等级不够时由步骤生成排在前面），
    画面定义在本应用目录的 screen_info/*.yml（screen_id 都以 skill_upgrade_ 开头，app_id 是 skill_upgrade，
    只在本应用运行时生效；用的是一条龙给应用自带画面的加载方式，和插件一样，不改全局 assets/game_data/screen_info），
    OCR 只读配置好的区域。
    """

    STATUS_NO_STEPS: ClassVar[str] = '没有要升级的技能'
    STATUS_ALL_DONE: ClassVar[str] = '步骤执行完'
    STATUS_START_STEP: ClassVar[str] = '开始一步'
    STATUS_NEXT: ClassVar[str] = '下一步'
    STATUS_RESERVE: ClassVar[str] = '丁尼到保留线'
    STATUS_RECOVER: ClassVar[str] = '出错后回大世界'
    STATUS_USER_STOP: ClassVar[str] = '人工结束'
    STATUS_EXCEPTION: ClassVar[str] = '异常'
    SCREEN_DIALOG: ClassVar[str] = '代理人-技能强化弹窗'
    SCREEN_LEVEL_DIALOG: ClassVar[str] = '代理人-培训弹窗'
    SCREEN_CONVERT: ClassVar[str] = '代理人-材料转化弹窗'
    SCREEN_OBTAINED: ClassVar[str] = '道具处理-获得'  # 全局画面：确认转化后游戏弹出的「获得」
    STATUS_LEVEL_STEP: ClassVar[str] = '升级代理人等级'
    # 升级代理人：不算失败的跳过（这一步今天做不了，后面要这个等级的技能会按等级门槛跳过）
    LEVEL_SKIP_NO_FAIL: ClassVar[tuple[str, ...]] = (SkillStepStatus.PROMOTE, SkillStepStatus.TARGET_OVER,
                                                     SkillStepStatus.OVER_BUDGET, SkillStepStatus.CONFIRM_DISABLED)
    # 升级代理人：点「升级」之前出的意外（已回技能页），跳过并记一次失败，不停下
    LEVEL_SKIP_FAIL: ClassVar[tuple[str, ...]] = (SkillStepStatus.LEVEL_SKIP, SkillStepStatus.DIALOG_UNKNOWN,
                                                  SkillStepStatus.DIALOG_WRONG)
    # 不算失败、这个技能今天不再试的结果
    DEAD_NO_FAIL: ClassVar[tuple[str, ...]] = (SkillStepStatus.MAXED, SkillStepStatus.AGENT_LV_LOW,
                                               SkillStepStatus.CONFIRM_DISABLED)
    WINDOW_SIZE: ClassVar[tuple[int, int]] = (1920, 1080)
    WORLD_TIMEOUT_SECONDS: ClassVar[float] = 180  # 嵌套的回大世界有不受重试次数限制的等待，一定要设超时
    STATUS_BOOKKEEPING_ERROR: ClassVar[str] = '记录结果出错'

    def __init__(self, ctx: ZContext, group_id: str = application_const.DEFAULT_GROUP_ID) -> None:
        ZApplication.__init__(
            self,
            ctx=ctx,
            app_id=skill_upgrade_const.APP_ID,
            op_name=skill_upgrade_const.APP_NAME,
        )
        self.config: SkillUpgradeConfig = self.ctx.run_context.get_config(
            app_id=skill_upgrade_const.APP_ID,
            instance_idx=self.ctx.current_instance_idx,
            group_id=group_id,
        )
        self._reset()

    def _reset(self) -> None:
        self.state: PlannerState | None = None
        self.steps: list[dict] = []
        self.step_idx: int = 0
        self.apply: bool = False
        self.fails: dict[str, int] = {}  # 每个代理人连续失败的次数
        self.skipped_agents: dict[str, str] = {}  # 不再处理的代理人 -> 原因
        self.dead_skills: dict[tuple[str, str], str] = {}  # 前一级没升成的 (代理人, 技能) -> 原因
        self.result: dict = {}
        self.result_path: Path | None = None
        self.session: AgentSkillSession | None = None
        self.cur_step: dict | None = None
        self.cur_record: dict | None = None  # 正在做、还没写进结果的一步
        self.touched_game: bool = False  # 有没有操作过游戏画面（没有就不用返回大世界）
        self.failed_reason: str | None = None  # 画面不对等需要整体停下的原因
        self.gates: dict[int, int] = {}  # 技能等级 -> 要的代理人等级
        self.agent_lv: dict[str, int] = {}  # 这次运行里升级代理人这步之后的等级（真升级是核对过的，演练是预计的）

    def handle_init(self) -> None:
        self._reset()

    @operation_node(name='生成今日步骤', is_start_node=True, screenshot_before_round=False)
    def build_steps(self) -> OperationRoundResult:
        stamp = AgentSkillSession.stamp()
        self.result_path = results_dir() / f'{stamp}.json'
        self.apply = bool(self.config.apply)
        reserve = max(0, self.config.dennies_reserve)
        max_steps = max(1, self.config.max_steps)
        conv = bool(getattr(self.config, 'convert_materials', False))  # 没有这个设置的旧配置（测试）按关闭
        self.session = AgentSkillSession(self.apply, reserve, AgentSkillSession.default_run_dir(stamp), convert=conv)
        self.result = {
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'applied': self.apply, 'plan': 'auto',
            'settings': {'apply': self.apply, 'dennies_reserve': reserve, 'max_steps': max_steps,
                         'research_first': bool(self.config.research_first), 'convert_materials': conv},
            'planned': [], 'blocked': [], 'steps': [], 'stopped_reason': None,
            'dennies_before': None, 'dennies_after': None, 'current_updated': [],
        }
        try:
            # 读体力计划：今晚刚刷完、明天推送才记账的次数也算进背包估算
            self.state = PlannerState.load(charge_plan=True, discs=False)
        except (OSError, ValueError, KeyError) as e:
            return self._fail_early(f'读不了角色培养数据 config/planner/state：{e!r}')
        prio = load_priority()
        self.gates = load_level_gates()
        bag = skill_bag(self.state)
        source = bag_source_text(self.state)
        log.info(source)
        plan = build_skill_steps(self.state, prio, reserve=reserve, max_steps=max_steps,
                                 research_first=bool(self.config.research_first), bag=bag,
                                 auto_level=bool(self.config.auto_level), gates=dict(self.gates), convert=conv)
        for line in format_steps(plan, self.state, prio):
            log.info(line)
        self.steps = list(plan['steps'])  # 自己的列表：升级代理人后会把同一代理人的步骤换成新字典，结果里的 planned 不变
        self.result.update(planned=plan['steps'], blocked=plan['blocked'], plan_stopped=plan['stopped'],
                           bag_source=source, bag_adjust=load_bag_adjust(self.state),
                           bag_after_plan=plan['bag_after'], state_snap_at=self.state.snap_at)
        if not self.steps:
            log.info(self.STATUS_NO_STEPS)
            return self.round_success(self.STATUS_NO_STEPS)

        problem = self._window_problem()
        if problem is not None:
            return self._fail_early(problem)
        log.info(f"{'真的升级' if self.apply else '演练（设置里没开「真的升级」，不会点确定）'}，共 {len(self.steps)} 步")
        return self.round_success(f'共 {len(self.steps)} 步')

    @node_from(from_name='生成今日步骤')
    @operation_node(name='开始前回大世界')
    def back_to_world_first(self) -> OperationRoundResult:
        """上一个应用或上次中断可能留在别的画面，先回大世界。
        在勘域（锄大地等留下的战斗区域）按 C 打不开代理人列表，ensure_normal_world 会先传送回录像店（2026-11-23 实测）。
        停在本应用的弹窗上（上次中断留下的材料转化弹窗、转化后的「获得」、强化弹窗、培训弹窗）先自己关：
        通用的回大世界会在转化弹窗上点到「取消/确认」类按钮"""
        self.touched_game = True
        if self._close_our_dialog():
            return self.round_retry('先关本应用的弹窗', wait=1)
        op = BackToNormalWorld(self.ctx, ensure_normal_world=True, timeout_seconds=self.WORLD_TIMEOUT_SECONDS)
        result = self.round_by_op_result(op.execute())
        if self.ctx.run_context.is_context_stop:  # 回大世界途中按了停止
            self.result['stopped_reason'] = '用户停止'
            return self.round_fail(self.STATUS_USER_STOP)
        if not result.is_success:
            self.failed_reason = '开始前回不到大世界'
            self.result['stopped_reason'] = self.failed_reason
        return result

    @node_from(from_name='开始前回大世界')
    @node_from(from_name='进入技能页', status=STATUS_NEXT)
    @node_from(from_name='升级技能', status=STATUS_NEXT)
    @node_from(from_name='升级代理人', status=STATUS_NEXT)
    @node_from(from_name='出错后回大世界')
    @operation_node(name='选择下一步', screenshot_before_round=False)
    def next_step(self) -> OperationRoundResult:
        """取下一步；不用进游戏就能判断跳过的步骤直接记下（每次运行的每一步都在结果里）"""
        while self.step_idx < len(self.steps):
            step = self.steps[self.step_idx]
            self.step_idx += 1
            aid, sk = step['agent'], step['skill']
            # 字段和代理人升级的结果一样，资源调度员 2.0 网页「导入升级结果」也能读
            record: dict = {
                'order': step['order'], 'what': describe(step), 'agent': aid, 'type': step['what'], 'skill': sk,
                'from': step['from'], 'to': step['to'],
                'display_from': step.get('display_from'), 'display_to': step.get('display_to'),
                'cost': step.get('cost', {}),
            }
            if step.get('check'):
                record['check'] = step['check']
            if step.get('convert'):
                record['convert_plan'] = step['convert']
            reason = self._skip_reason(step)
            if reason is not None:
                record.update(status='skipped', reason=reason)
                log.info(f"跳过 {record['what']}：{reason}")
                self._next(record)
                continue
            self.cur_step, self.cur_record = step, record
            log.info(f"开始 {record['what']}")
            return self.round_success(self.STATUS_START_STEP)
        return self.round_success(self.STATUS_ALL_DONE)

    # 进入技能页 / 升级技能 不重试：节点里出异常时引擎会重试整个节点，那样会对同一步重新操作一遍游戏
    @node_from(from_name='选择下一步', status=STATUS_START_STEP)
    @operation_node(name='进入技能页', screenshot_before_round=False, node_max_retry_times=0)
    def enter_skill_page(self) -> OperationRoundResult:
        op = GotoAgentSkillPage(self.ctx, self.session, self.cur_step)
        result = op.execute()
        try:
            return self._after_enter(op, result)
        except Exception:  # noqa: BLE001 记结果出错不能让引擎重做游戏操作
            return self._bookkeeping_error(SkillStepOutcome(status=result.status))

    def _after_enter(self, op: GotoAgentSkillPage, result: OperationResult) -> OperationRoundResult:
        if not result.success:
            outcome = SkillStepOutcome(status=result.status, message=str(op.result_data or ''))
            return self._apply_fail(result.status, outcome)
        if result.status == SelectAgent.STATUS_AGENT_NOT_FOUND:
            step, record = self.cur_step, self.cur_record
            msg = str(result.data or op.result_data or '')
            record.update(status='skipped', reason=msg)
            self.skipped_agents[step['agent']] = f'找不到这个代理人：{msg}'
            self.session.forget_position()
            log.info(f"跳过 {record['what']}：{msg}")
            self._next(record)
            return self.round_success(self.STATUS_NEXT)
        if self.cur_step.get('what') == 'level':
            return self.round_success(self.STATUS_LEVEL_STEP)
        return self.round_success(result.status)

    @node_from(from_name='进入技能页')
    @operation_node(name='升级技能', screenshot_before_round=False, node_max_retry_times=0)
    def upgrade_skill(self) -> OperationRoundResult:
        op = UpgradeSkillOnce(self.ctx, self.session, self.cur_step)
        result = op.execute()
        outcome = op.outcome  # 人工结束、超时时 result 不带数据，从操作实例上取
        try:
            if not result.success:
                return self._apply_fail(result.status, outcome)
            return self._apply_outcome(outcome)
        except Exception:  # noqa: BLE001 记结果出错不能让引擎重做游戏操作
            return self._bookkeeping_error(outcome)

    # 升级代理人：和升级技能一样不重试
    @node_from(from_name='进入技能页', status=STATUS_LEVEL_STEP)
    @operation_node(name='升级代理人', screenshot_before_round=False, node_max_retry_times=0)
    def upgrade_level(self) -> OperationRoundResult:
        step = self.cur_step
        op = UpgradeAgentLevelOnce(self.ctx, self.session, step, goal_lv=self._goal_lv(step['agent']),
                                   allow_overshoot=bool(getattr(self.config, 'level_allow_overshoot', False)))
        result = op.execute()
        outcome = op.outcome
        try:
            if not result.success:
                return self._apply_fail(result.status, outcome)
            return self._apply_outcome(outcome)
        except Exception:  # noqa: BLE001 记结果出错不能让引擎重做游戏操作
            return self._bookkeeping_error(outcome)

    @node_from(from_name='进入技能页', success=False, status=STATUS_RECOVER)
    @node_from(from_name='升级技能', success=False, status=STATUS_RECOVER)
    @node_from(from_name='升级代理人', success=False, status=STATUS_RECOVER)
    @operation_node(name='出错后回大世界', node_max_retry_times=3)
    def recover(self) -> OperationRoundResult:
        """单步意外出错：先关掉可能还开着的强化弹窗 / 培训弹窗，再回大世界，然后继续下一步；回不去就整体停下，不在未知画面上按键"""
        if self._close_our_dialog():
            return self.round_retry('先关强化弹窗', wait=1)
        result = BackToNormalWorld(self.ctx, ensure_normal_world=True, timeout_seconds=self.WORLD_TIMEOUT_SECONDS).execute()
        if self.ctx.run_context.is_context_stop:  # 回大世界途中按了停止
            self.result['stopped_reason'] = '用户停止'
            return self.round_fail(self.STATUS_USER_STOP)
        if not result.success:
            self.failed_reason = f"{self.cur_record['what'] if self.cur_record else '这一步'} 出错后回不到大世界"
            self.result['stopped_reason'] = self.failed_reason
            return self.round_fail(self.failed_reason)
        return self.round_success()

    @node_from(from_name='生成今日步骤', status=STATUS_NO_STEPS, ignore_status=False)
    @node_from(from_name='生成今日步骤', success=False)
    @node_from(from_name='开始前回大世界', success=False)
    @node_from(from_name='选择下一步', status=STATUS_ALL_DONE)
    @node_from(from_name='升级技能', status=STATUS_RESERVE)
    @node_from(from_name='升级代理人', status=STATUS_RESERVE)
    @node_from(from_name='进入技能页', success=False)
    @node_from(from_name='升级技能', success=False)
    @node_from(from_name='升级代理人', success=False)
    @node_from(from_name='出错后回大世界', success=False)
    @operation_node(name='保存结果', screenshot_before_round=False)
    def save_result(self) -> OperationRoundResult:
        self._write_result()
        return self.round_success(str(self.result_path))

    @node_from(from_name='保存结果')
    @operation_node(name='返回大世界', node_max_retry_times=3)
    def back_to_world(self) -> OperationRoundResult:
        if not self.touched_game:
            return self._finish(self.round_success(self.result.get('stopped_reason') or self.STATUS_NO_STEPS))
        if self._close_our_dialog():
            return self.round_retry('先关强化弹窗', wait=1)
        op = BackToNormalWorld(self.ctx, ensure_normal_world=True, timeout_seconds=self.WORLD_TIMEOUT_SECONDS)
        return self._finish(self.round_by_op_result(op.execute()))

    def after_operation_done(self, result: OperationResult) -> None:
        """人工结束时后面的节点都不会再跑：把正在做的一步记成「用户停止」并写结果"""
        try:
            if self.cur_record is not None:
                self.cur_record.update(status='failed', reason='用户停止' if result.status == self.STATUS_USER_STOP
                                       else str(result.status))
                self.result['stopped_reason'] = self.result.get('stopped_reason') or self.cur_record['reason']
                self.result.setdefault('steps', []).append(self.cur_record)
                self.cur_record = None
            self._write_result()
        except Exception:  # noqa: BLE001 写结果失败不能影响运行记录
            log.error('写角色技能提升结果失败', exc_info=True)
        ZApplication.after_operation_done(self, result)

    # ---------- 结果处理 ----------
    def _apply_outcome(self, outcome: SkillStepOutcome) -> OperationRoundResult:
        """UpgradeSkillOnce 正常结束（游戏停在技能页）：按状态记结果"""
        step, record = self.cur_step, self.cur_record
        if step.get('what') == 'level':
            return self._apply_level_outcome(outcome)
        aid, sk = step['agent'], step['skill']
        status, msg = outcome.status, outcome.message
        S = SkillStepStatus
        self._sync_queue(step, outcome)
        if outcome.convert is not None:
            record['convert'] = outcome.convert
        if status in (S.DONE, S.REHEARSED, S.SIMULATED, S.CONVERT_REHEARSED):
            record.update(status='ok', note=msg, verified=outcome.spent is not None)
            if outcome.dennies_unverified and outcome.spent is not None:
                record['dennies_unverified'] = True
            self.fails[aid] = 0
            log.info(f"完成 {record['what']}：{msg}")
            if outcome.spent is not None:  # 真的升级并且游戏里核对过
                self._record_verified(step, record, outcome)
            if status == S.CONVERT_REHEARSED:  # 演练没有真的转化：材料还是不够，照样记进刷取队列（下一级按模拟继续）
                self._queue_needs(step, record, outcome)
        elif status in (S.SHORT, S.CONVERT_SKIPPED):
            record.update(status='skipped', reason=msg)
            self._queue_needs(step, record, outcome)
            self.dead_skills[(aid, sk)] = f'前一级材料不足：{msg}'
        elif status in self.DEAD_NO_FAIL:  # 不算失败：这个技能今天升不了，同一代理人的其他技能照常
            record.update(status='skipped', reason=msg)
            if status == S.AGENT_LV_LOW and outcome.need_level:  # 记下门槛，下次生成步骤时先排升级代理人
                try:
                    if record_level_gate(int(step['to']), int(outcome.need_level), record['what']):
                        log.info(f"记下：技能 {step['to']} 级要代理人 {outcome.need_level} 级")
                except (OSError, ValueError):
                    log.warning('记技能等级门槛失败', exc_info=True)
            if outcome.short:
                self._queue_needs(step, record, outcome)
            self.dead_skills[(aid, sk)] = msg
        elif status in (S.LEVEL_MISMATCH, S.COST_MISMATCH):
            record.update(status='skipped', reason=msg)
            self.dead_skills[(aid, sk)] = f'前一级没升成：{msg}'
            self._count_fail(aid, msg)
        elif status == S.RESERVE:
            record.update(status='skipped', reason=msg)
            self.result['stopped_reason'] = msg
            log.info(msg)
            self._observe(step, record, outcome)
            self._next(record)
            return self.round_success(self.STATUS_RESERVE)
        else:  # 不认识的状态：当作失败停下
            return self._apply_fail(str(status), outcome)
        if status not in (S.DONE, S.REHEARSED, S.SIMULATED, S.CONVERT_REHEARSED):
            log.info(f"跳过 {record['what']}：{msg}")
        self._observe(step, record, outcome)
        self._next(record)
        return self.round_success(self.STATUS_NEXT)

    def _apply_fail(self, status: str, outcome: SkillStepOutcome) -> OperationRoundResult:
        """进入技能页或强化失败：记失败；意外异常回大世界后继续，其余（画面不对、核对失败、人工结束等）整体停下"""
        step, record = self.cur_step, self.cur_record
        aid, sk = step['agent'], step['skill']
        reason = f'{status}：{outcome.message}' if outcome.message else str(status)
        record.update(status='failed', reason=reason)
        if outcome.confirm_clicked:
            record['confirm_clicked'] = True
            record['verified'] = outcome.spent is not None
        if outcome.conversion_clicked:
            record['conversion_clicked'] = True
        if outcome.convert is not None:
            record['convert'] = outcome.convert
        if outcome.spent is not None:  # 已经点了确定、等级也核对过（例如丁尼对不上才停），照样记下
            self._record_verified(step, record, outcome)
        if step.get('what') == 'level':
            record['agent_level'] = outcome.agent_level
            self._set_agent_lv(aid, self._level_now(step, outcome))
        elif outcome.dialog is not None and not outcome.short and not outcome.need_level:
            self._sync_queue(step, outcome)  # 只会去掉已经够了的条目，失败时从不加进队列
        self._observe(step, record, outcome)
        if status == self.STATUS_USER_STOP:
            record['reason'] = '用户停止'
            self.result['stopped_reason'] = '用户停止'
            log.info('用户停止')
            self._next(record)
            return self.round_fail(status)
        if status == self.STATUS_EXCEPTION and not outcome.confirm_clicked and not outcome.conversion_clicked:
            self.dead_skills[(aid, sk)] = '前一级出错'
            self.session.forget_position()
            self._count_fail(aid, reason)
            log.error(f"失败 {record['what']}：{reason}")
            self._next(record)
            return self.round_fail(self.STATUS_RECOVER)
        if outcome.confirm_clicked and outcome.spent is None:
            reason = f'点了确定但没核对完：{reason}'
            record['reason'] = reason
        elif outcome.conversion_clicked and outcome.spent is None:
            reason = f'确认了转化但没升级完：{reason}'
            record['reason'] = reason
        self.failed_reason = reason
        self.result['stopped_reason'] = reason
        log.error(f'停止：{reason}')
        self._next(record)
        return self.round_fail(str(status))

    def _bookkeeping_error(self, outcome: SkillStepOutcome) -> OperationRoundResult:
        """游戏里的操作已经结束，记结果时出了异常：这一步记为失败，整体停下（不重做这一步，免得再操作一遍游戏）"""
        log.error('记录这一步的结果出错，停止', exc_info=True)
        reason = f'{self.STATUS_BOOKKEEPING_ERROR}（游戏里的操作已结束，没有重做）'
        record = self.cur_record
        if record is not None:
            record.update(status='failed', reason=reason)
            if outcome.confirm_clicked:
                record['confirm_clicked'] = True
            if outcome.conversion_clicked:
                record['conversion_clicked'] = True
            if outcome.convert is not None:
                record['convert'] = outcome.convert
            self.result['steps'].append(record)
            self.cur_record = None
        self.failed_reason = reason
        self.result['stopped_reason'] = reason
        return self.round_fail(self.STATUS_BOOKKEEPING_ERROR)

    def _sync_queue(self, step: dict, outcome: SkillStepOutcome) -> None:
        """弹窗里材料已经够了（也没有代理人等级限制）：把这一步之前记进刷取队列的条目去掉"""
        if outcome.dialog is None or outcome.short or outcome.need_level:
            return
        try:
            if remove_from_queue(describe(step)):
                log.info('材料已经够了，从刷取队列去掉这一步')
        except (OSError, ValueError) as e:
            log.error(f'更新刷取队列失败：{e!r}')

    def _queue_needs(self, step: dict, record: dict, outcome: SkillStepOutcome) -> None:
        """材料不足（弹窗已经关掉、回到技能页之后才写）：这一步要的材料写进刷取队列"""
        queued = False
        try:
            added = add_to_queue(describe(step), outcome.needs)
            queued = True
            log.info(f"材料不足，{'已加入' if added else '已在'}刷取队列：{outcome.needs}")
        except (OSError, ValueError) as e:
            log.error(f'材料不足，但写刷取队列失败：{e!r}')
        record.update(needs=outcome.needs, queued=queued)

    def _skip_reason(self, step: dict) -> str | None:
        aid, sk = step['agent'], step['skill']
        if aid in self.skipped_agents:
            return self.skipped_agents[aid]
        if step['what'] == 'level':  # 不看 dead_skills：这步的 skill 只是第一个要这个等级的技能，后面别的技能也要它
            reason = self._level_unneeded(step)
            if reason is not None and aid not in self.agent_lv:  # 没升：后面要这个等级的技能不进游戏直接跳过
                self._set_agent_lv(aid, step.get('from'))
            return reason
        if (aid, sk) in self.dead_skills:
            return self.dead_skills[(aid, sk)]
        if step['what'] != 'skill':
            reason = f"{step['what']} 还没实现"
            self._count_fail(aid, reason)
            return reason
        frm = step.get('display_from', step['from'])
        to = step.get('display_to', step['to'])
        need = self.gates.get(int(step['to']), 0)
        if aid in self.agent_lv and self.agent_lv[aid] < need:  # 前面升级代理人这步没升到：不进游戏，不算失败
            return f"代理人 {self.agent_lv[aid]} 级，{sk} {to} 级要 {need} 级（升级没到）"
        if to != frm + 1:
            reason = '一步只升 1 级，请把清单拆开'
            self.dead_skills[(aid, sk)] = f'前一级没升成：{reason}'
            self._count_fail(aid, reason)
            return reason
        return None

    def _level_unneeded(self, step: dict) -> str | None:
        """
        升级代理人这步还有没有用：后面同一代理人、门槛在 (from, to] 之间的技能步骤都已卡住时返回跳过原因（免得白花经验）。
        有一个还没卡住、或者后面根本没有这样的步骤（这次的步数用完了，明天的技能要这个等级），都照做
        """
        aid = step['agent']
        frm, to = int(step.get('from') or 0), int(step['to'])
        dead: list[str] = []
        for s in self.steps[self.step_idx:]:
            if s['agent'] != aid or s['what'] != 'skill':
                continue
            if not frm < self.gates.get(int(s['to']), 0) <= to:
                continue
            if (aid, s['skill']) not in self.dead_skills:
                return None
            dead.append(f"{s['skill']}：{self.dead_skills[(aid, s['skill'])]}")
        if dead:
            return f"要这个等级的技能都已跳过（{'；'.join(dict.fromkeys(dead))}）"
        return None

    def _close_our_dialog(self) -> bool:
        """
        停在材料转化弹窗上时只点「取消」（绝不点确认）；停在转化后的「获得」弹窗上时点它的「确认」（只是收下，不花东西）；
        停在技能强化弹窗或代理人培训弹窗上时点关闭（回大世界前先关，免得通用的「确认」类按钮碰到弹窗）。
        通用的回大世界认不出「获得」，只会在最后点左上角的「返回」碰运气（2026-11-25 实测是这样关掉的）。
        演练也会这样收下留下来的「获得」（开始前回大世界、出错后回大世界、返回大世界这 3 处）：它只是收下奖励，不花东西；
        演练里本应用自己不会确认转化，所以不会自己弹出「获得」。
        弹窗分开判断（一条龙按加载顺序判断画面列表）：转化弹窗最先（全局「获得」的「确认」区域和转化的「确认」重叠），
        「获得」在强化弹窗前（它盖在强化弹窗上面）；像「获得」但转化弹窗的标题或「取消」还在（转化弹窗只认出一半）时，
        不点「确认」，只点转化弹窗的「取消」（认不出「取消」就什么都不点）。
        「获得」的「确认」只在本应用的窄区域里找白字「确认」（转化弹窗「确认」的字不在这个区域里）。
        用本轮开始时的截图。返回是否点了
        """
        if self.node_retry_times >= self.node_max_retry_times:
            return False
        for screen, area in ((self.SCREEN_CONVERT, '按钮-取消'), (self.SCREEN_OBTAINED, UpgradeSkillOnce.AREA_OBTAINED_BTN),
                             (self.SCREEN_DIALOG, '按钮-关闭'), (self.SCREEN_LEVEL_DIALOG, '按钮-关闭')):
            name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[screen])
            if name != screen:
                continue
            if screen == self.SCREEN_OBTAINED:
                if UpgradeSkillOnce.convert_parts_shown(self.ctx, self.last_screenshot):
                    log.warning('画面像「获得」，但转化弹窗的标题或「取消」还在：不点「确认」，只点转化弹窗的「取消」')
                    area = '按钮-取消'
                screen = self.SCREEN_CONVERT  # 「获得」的「确认」窄区域和「取消」都配在本应用的转化弹窗里
            focus_game(self.ctx)
            self.round_by_find_and_click_area(self.last_screenshot, screen, area, pre_delay=0)
            return True
        return False

    def _window_problem(self) -> str | None:
        """开始前检查：游戏窗口存在且是 1920×1080"""
        controller = self.ctx.controller
        if controller is None or not self.ctx.is_game_window_ready:
            return '找不到游戏窗口'
        rect = controller.game_win.win_rect
        if rect is None:
            return '读不到游戏窗口大小'
        if (rect.width, rect.height) != self.WINDOW_SIZE:
            return f'游戏窗口是 {rect.width}×{rect.height}，请在游戏设置里改成 1920×1080 再运行'
        return None

    # ---------- 内部 ----------
    def _finish(self, result: OperationRoundResult) -> OperationRoundResult:
        """画面不对等原因停下的，整个应用记为失败（一条龙继续跑后面的应用）"""
        if self.failed_reason:
            return self.round_fail(self.failed_reason)
        return result

    def _fail_early(self, reason: str) -> OperationRoundResult:
        self.failed_reason = reason
        self.result['stopped_reason'] = reason
        log.error(reason)
        return self.round_fail(reason)

    def _count_fail(self, agent_id: str, reason: str) -> None:
        """同一代理人连续失败两次，后面他的步骤都跳过"""
        self.fails[agent_id] = self.fails.get(agent_id, 0) + 1
        if self.fails[agent_id] >= 2:
            self.skipped_agents[agent_id] = f'该代理人连续失败两次（最后一次：{reason}）'

    def _record_verified(self, step: dict, record: dict, outcome: SkillStepOutcome) -> None:
        """游戏里核对过的升级：current.json 里的技能等级 +1 / 代理人等级（花掉的材料由 _observe 按弹窗数字记进背包校正）"""
        record['spent'] = outcome.spent
        conv = outcome.convert or {}
        if conv.get('confirmed') and conv.get('verified'):
            # spent 是强化弹窗上的花费（和没转化时一样）；spent_total 是这一步账号里实际少了多少（转化用掉的低一档芯片和丁尼也算上）
            total = dict(outcome.spent)
            to, frm = conv['to'], conv['from']
            total[to] = total.get(to, 0) - int(conv['made'])
            total[frm] = total.get(frm, 0) + int(conv['used'])
            total[DENNY] = total.get(DENNY, 0) + int(conv['dennies'])
            record['spent_total'] = total
        if step.get('what') == 'level':
            after = (outcome.agent_level or {}).get('after')
            try:
                if after and write_verified_level(step['agent'], int(after)):
                    self.result['current_updated'].append({'agent': step['agent'], 'level': int(after)})
            except (OSError, ValueError, KeyError, TypeError) as e:
                record['write_error'] = repr(e)
                log.error('写 current.json 失败', exc_info=True)
            return
        try:
            if write_verified_skill(step['agent'], step['skill'], step['to']):
                self.result['current_updated'].append(
                    {'agent': step['agent'], 'skill': step['skill'], 'base_level': step['to']})
        except (OSError, ValueError, KeyError) as e:
            record['write_error'] = repr(e)
            log.error('写 current.json 失败', exc_info=True)

    def _observe(self, step: dict, record: dict, outcome: SkillStepOutcome) -> None:
        """
        把强化弹窗里看到的真实数量记进背包校正（config/skill_upgrade/bag_adjust.json），每日推送和下次生成步骤都会用：
        真的升级成功的记升完剩下的（有 − 需，丁尼优先用升级后读到的），其余记弹窗里的「有」。
        只记步骤花费里有名字的材料（芯片、仓鼠笼、丁尼），认不出名字的不记。和写 current.json 分开，一个失败不影响另一个。
        点了确定（或升级）但没核对完：弹窗里是升级前的数量，不记（技能步骤只记升级后读到的丁尼）。
        确认了转化：转化用掉的低一档芯片弹窗里看不到，按估算扣；转化没核对完时芯片和丁尼按下限估算记
        （「获得」弹窗正好显示转化计划的芯片和数量时，芯片记转化前 + 转化出来的）。
        """
        if outcome.dialog is not None:
            record['dialog'] = outcome.dialog
        if outcome.dialog_before is not None:
            record['dialog_before'] = outcome.dialog_before
        if outcome.levels is not None:
            record['levels'] = outcome.levels
        if self.state is None:
            return
        seen = self._seen_counts(step, outcome)
        estimate = self._convert_estimate(outcome)
        if not seen and not estimate:
            return
        try:
            if estimate:
                conv = outcome.convert or {}
                if conv.get('verified'):
                    why = f"（估算：转化用掉 {conv.get('used')}）"
                elif self._obtained_matches(conv):
                    why = (f"（估算：确认了转化，强化弹窗未核对；「获得」弹窗确认转化出 {conv.get('made')} 个 {conv.get('to')}，"
                           f"按转化用掉 {conv.get('used')}）")
                else:
                    why = f"（估算：确认了转化，未核对；按转化用掉 {conv.get('used')}、没转化出来记下限）"
                observe_bag(self.state, estimate, why=f"{record['what']}{why}")
                record['seen_estimate'] = estimate
            if seen:
                observe_bag(self.state, seen, why=record['what'])
                record['seen'] = seen
        except (OSError, ValueError, KeyError) as e:
            record['observe_error'] = repr(e)
            log.error('写背包校正 bag_adjust.json 失败', exc_info=True)

    @staticmethod
    def _seen_counts(step: dict, outcome: SkillStepOutcome) -> dict[str, int]:
        """弹窗里看到、可以当作真实数量记下的：升级核对过的记升完剩下的，没点确定的记弹窗里的「有」"""
        if not outcome.dialog:
            return {}
        spent = outcome.spent or {}
        unverified = outcome.confirm_clicked and outcome.spent is None  # 点了确定/升级但没核对完
        if step.get('what') == 'level':
            if unverified:  # 弹窗里是升级前的数量，不记
                return {}
            names = set(EXP_A)  # 培训弹窗只记 3 种调查员记录；按钮行的数字格式没确认过，丁尼不记
        else:
            if unverified:  # 弹窗里是升级前的数量（升级可能已经生效），只记升级后在下一级弹窗里读到的丁尼
                return {DENNY: outcome.dennies_after} if outcome.dennies_after is not None else {}
            names = set(step.get('cost') or {}) | {DENNY}
        seen: dict[str, int] = {name: v['have'] - spent.get(name, 0)
                                for name, v in outcome.dialog.items() if name in names}
        if spent and outcome.dennies_after is not None:
            seen[DENNY] = outcome.dennies_after
        return seen

    def _convert_estimate(self, outcome: SkillStepOutcome) -> dict[str, int]:
        """
        确认了转化：低一档芯片 = 估算的数量 − 转化用掉的（不低于 0）。
        转化后的强化弹窗没核对过：游戏弹出的「获得」正好是转化计划的芯片和数量时（游戏确认转化出来了），
        要的芯片记 转化前的数量 + 转化出来的；没看到「获得」或对不上（不知道转化成没成）时按下限记转化前的数量。
        丁尼都记 转化前 − 转化花的。低估只会让排步骤更保守，下次读到强化弹窗就会改成真实数量
        """
        conv = outcome.convert or {}
        if not (conv.get('confirmed') and conv.get('from') and conv.get('used')):
            return {}
        own = effective_own(self.state, pending=True)
        est = {conv['from']: max(0, int(round(float(own.get(conv['from'], 0) or 0))) - int(conv['used']))}
        if not conv.get('verified'):
            before = outcome.dialog_before or {}
            to = conv.get('to')
            if to and isinstance(before.get(to), dict) and before[to].get('have') is not None:
                est[to] = int(before[to]['have'])
                if self._obtained_matches(conv):
                    est[to] += int(conv['made'])
            den = before.get(DENNY) if isinstance(before.get(DENNY), dict) else None
            if den and den.get('have') is not None and conv.get('dennies') is not None:
                est[DENNY] = max(0, int(den['have']) - int(conv['dennies']))
        return est

    @staticmethod
    def _obtained_matches(conv: dict) -> bool:
        """转化后游戏弹出的「获得」显示的名字和数量正好是转化计划的（要的芯片、转化出来的数量）"""
        got = conv.get('obtained') or {}
        return (got.get('name') is not None and got.get('name') == conv.get('to')
                and got.get('count') is not None and conv.get('made') is not None
                and int(got['count']) == int(conv['made']))

    # ---------- 升级代理人 ----------
    def _apply_level_outcome(self, outcome: SkillStepOutcome) -> OperationRoundResult:
        """UpgradeAgentLevelOnce 正常结束（游戏停在技能页）：按状态记结果。后面要这个等级的技能按 self.agent_lv 决定做不做"""
        step, record = self.cur_step, self.cur_record
        aid = step['agent']
        status, msg = outcome.status, outcome.message
        S = SkillStepStatus
        info = outcome.agent_level or {}
        if info:
            record['agent_level'] = info
        ok = False
        if status in (S.DONE, S.REHEARSED):
            ok = True
            record.update(status='ok', note=msg, verified=outcome.spent is not None)
            if outcome.partial:
                record['partial'] = True
            self.fails[aid] = 0
            if outcome.spent is not None:  # 真的升级并且游戏里核对过
                self._record_verified(step, record, outcome)
                self._set_agent_lv(aid, info.get('after'))
            else:  # 演练：按自动添加后的预计等级（后面的技能会进游戏，在强化弹窗里看到真实门槛）
                self._set_agent_lv(aid, info.get('target'), real=info.get('basic'))
            if outcome.partial:
                self._queue_level_needs(step, record, outcome)
            else:
                self._clear_level_queue(step)
        elif status in (S.ALREADY, S.LEVEL_MAXED):  # 只读了一次页面：不写 current.json（每晚 HoYoLAB 同步会更新）
            ok = True
            record.update(status='ok', note=msg, verified=False)
            self.fails[aid] = 0
            self._set_agent_lv(aid, info.get('basic') or (60 if status == S.LEVEL_MAXED else None))
            self._clear_level_queue(step)
        elif status == S.SHORT:
            record.update(status='skipped', reason=msg)
            self._set_agent_lv(aid, self._level_now(step, outcome))
            self._queue_level_needs(step, record, outcome)
        elif status in self.LEVEL_SKIP_NO_FAIL:
            record.update(status='skipped', reason=msg)
            self._set_agent_lv(aid, self._level_now(step, outcome))
        elif status in self.LEVEL_SKIP_FAIL:  # 点「升级」之前的意外，已回技能页：跳过，记一次失败，不停下
            record.update(status='skipped', reason=f'{status}：{msg}')
            self._set_agent_lv(aid, self._level_now(step, outcome))
            self.dead_skills[(aid, step['skill'])] = f'升级代理人没做成：{msg}'
            self._count_fail(aid, msg)
        elif status == S.RESERVE:
            record.update(status='skipped', reason=msg)
            self.result['stopped_reason'] = msg
            log.info(msg)
            self._observe(step, record, outcome)
            self._next(record)
            return self.round_success(self.STATUS_RESERVE)
        else:  # 不认识的状态：当作失败停下
            return self._apply_fail(str(status), outcome)
        log.info(f"{'完成' if ok else '跳过'} {record['what']}：{msg}")
        self._observe(step, record, outcome)
        self._next(record)
        return self.round_success(self.STATUS_NEXT)

    @staticmethod
    def _level_now(step: dict, outcome: SkillStepOutcome) -> int | None:
        """游戏里读到的代理人等级（没读到用步骤的 from）"""
        info = outcome.agent_level or {}
        return info.get('after') or info.get('basic') or step.get('from')

    def _set_agent_lv(self, aid: str, lv: int | None, real: int | None = None) -> None:
        """记下这个代理人现在（演练：预计）的等级；后面同一代理人的步骤用游戏里的真实等级找列表卡片"""
        if lv is None:
            return
        self.agent_lv[aid] = int(lv)
        real = int(real if real is not None else lv)
        for i in range(self.step_idx, len(self.steps)):
            if self.steps[i]['agent'] == aid:
                self.steps[i] = dict(self.steps[i], agent_lv=real)  # 换成新字典，结果里的 planned 不变

    def _goal_lv(self, aid: str) -> int:
        """培养目标等级（读不出按 60）"""
        try:
            return int((self.state.tgt.get(aid) or {}).get('lv') or 60)
        except (AttributeError, TypeError, ValueError):
            return 60

    @staticmethod
    def _level_queue_prefix(step: dict) -> str:
        return f"{step.get('name_cn') or step['agent']} 等级 "

    def _clear_level_queue(self, step: dict) -> None:
        """等级已经够了：去掉这个代理人之前记进刷取队列的经验条目"""
        try:
            if remove_from_queue_prefix(self._level_queue_prefix(step)):
                log.info('等级已经够了，从刷取队列去掉之前的经验条目')
        except (OSError, ValueError) as e:
            log.error(f'更新刷取队列失败：{e!r}')

    def _queue_level_needs(self, step: dict, record: dict, outcome: SkillStepOutcome) -> None:
        """经验不够（弹窗已经关掉、回到技能页之后才写）：先去掉这个代理人旧的等级条目，再记这一步还差的资深调查员记录"""
        queued = False
        try:
            remove_from_queue_prefix(self._level_queue_prefix(step))
            add_to_queue(describe(step), outcome.needs)
            queued = True
            log.info(f'经验不够，已加入刷取队列：{outcome.needs}')
        except (OSError, ValueError) as e:
            log.error(f'经验不够，但写刷取队列失败：{e!r}')
        record.update(needs=outcome.needs, queued=queued)

    def _next(self, record: dict) -> None:
        self.result['steps'].append(record)
        if record is self.cur_record:
            self.cur_record = None
        self._write_result()

    def _write_result(self) -> None:
        """结果写到 config/skill_upgrade/results/<时间>.json，逐步记录 ok / skipped / failed 和原因"""
        if self.result_path is None or not self.result:
            return
        if self.session is not None:
            self.result['dennies_before'] = self.session.dennies_before
            self.result['dennies_after'] = self.session.dennies_after
        self.result['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        try:
            self.result_path.parent.mkdir(parents=True, exist_ok=True)
            self.result_path.write_text(json.dumps(self.result, ensure_ascii=False, indent=2), encoding='utf-8')
        except (OSError, TypeError, ValueError):  # 写结果失败（例如文件被占用）只记日志，下一次写会补上
            log.error(f'写角色技能提升结果失败 {self.result_path}', exc_info=True)
