from __future__ import annotations

import json
import time
from pathlib import Path

from one_dragon.base.operation.application import application_const
from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils import os_utils
from one_dragon.utils.log_utils import log
from zzz_od.application.agent_upgrade.agent_upgrade_game import StopRun, UpgradeGame
from zzz_od.application.agent_upgrade.agent_upgrade_runner import (
    AgentLevelLow,
    AgentNotFound,
    LevelMismatch,
    ReserveReached,
    Shortage,
    SkillMaxed,
    Skip,
    Upgrader,
    describe,
)
from zzz_od.application.character_growth.planner_core import PlannerState, load_bag_adjust, observe_bag
from zzz_od.application.character_growth.skill_steps import (
    DENNY,
    bag_source_text,
    build_skill_steps,
    format_steps,
    load_priority,
    results_dir,
    skill_bag,
    write_verified_skill,
)
from zzz_od.application.skill_upgrade import skill_upgrade_const
from zzz_od.application.skill_upgrade.skill_upgrade_config import SkillUpgradeConfig
from zzz_od.application.zzz_application import ZApplication
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.back_to_normal_world import BackToNormalWorld

STATUS_NO_STEPS: str = '没有要升级的技能'


class SkillUpgradeApp(ZApplication):
    """
    角色技能提升：每次运行按「角色培养」的数据（计入的代理人、★ 优先、培养目标、背包）自动算出要升的技能，
    在游戏里一级一级地强化。默认只演练（不点确定）；设置里打开「真的升级」后才花材料和丁尼。
    """

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
        self.state: PlannerState | None = None
        self.steps: list[dict] = []
        self.step_idx: int = 0
        self.apply: bool = False
        self.fails: dict[str, int] = {}  # 每个代理人连续失败的次数
        self.skipped_agents: dict[str, str] = {}  # 不再处理的代理人 -> 原因
        self.dead_skills: dict[tuple[str, str], str] = {}  # 前一级没升成的 (代理人, 技能) -> 原因
        self.result: dict = {}
        self.result_path: Path | None = None
        self.game: UpgradeGame | None = None
        self.upgrader: Upgrader | None = None
        self.touched_game: bool = False  # 有没有操作过游戏画面（没有就不用返回大世界）
        self.failed_reason: str | None = None  # 画面不对等需要整体停下的原因

    def handle_init(self) -> None:
        self.state = None
        self.steps = []
        self.step_idx = 0
        self.apply = False
        self.fails = {}
        self.skipped_agents = {}
        self.dead_skills = {}
        self.result = {}
        self.result_path = None
        self.game = None
        self.upgrader = None
        self.touched_game = False
        self.failed_reason = None

    @operation_node(name='生成今日步骤', is_start_node=True, screenshot_before_round=False)
    def build_steps(self) -> OperationRoundResult:
        stamp = time.strftime('%Y-%m-%d_%H%M%S')
        self.result_path = results_dir() / f'{stamp}.json'
        self.game = UpgradeGame(self.ctx, Path(os_utils.get_path_under_work_dir('.log', 'skill_upgrade')) / stamp)
        self.apply = bool(self.config.apply)
        reserve = max(0, self.config.dennies_reserve)
        max_steps = max(1, self.config.max_steps)
        self.result = {
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'applied': self.apply, 'plan': 'auto',
            'settings': {'apply': self.apply, 'dennies_reserve': reserve, 'max_steps': max_steps,
                         'research_first': bool(self.config.research_first)},
            'planned': [], 'blocked': [], 'steps': [], 'stopped_reason': None,
            'dennies_before': None, 'dennies_after': None, 'current_updated': [],
        }
        try:
            # 读体力计划：今晚刚刷完、明天推送才记账的次数也算进背包估算
            self.state = PlannerState.load(charge_plan=True, discs=False)
        except (OSError, ValueError, KeyError) as e:
            return self._fail_early(f'读不了角色培养数据 config/planner/state：{e!r}')
        prio = load_priority()
        bag = skill_bag(self.state)
        source = bag_source_text(self.state)
        log.info(source)
        plan = build_skill_steps(self.state, prio, reserve=reserve, max_steps=max_steps,
                                 research_first=bool(self.config.research_first), bag=bag)
        for line in format_steps(plan, self.state, prio):
            log.info(line)
        self.steps = plan['steps']
        self.result.update(planned=plan['steps'], blocked=plan['blocked'], plan_stopped=plan['stopped'],
                           bag_source=source, bag_adjust=load_bag_adjust(self.state),
                           bag_after_plan=plan['bag_after'], state_snap_at=self.state.snap_at)
        if not self.steps:
            log.info(STATUS_NO_STEPS)
            return self.round_success(STATUS_NO_STEPS)

        try:
            self.game.precheck()
        except StopRun as e:
            return self._fail_early(str(e))
        self.upgrader = Upgrader(self.game, self.apply, dennies_reserve=reserve, queue_on_short=True)
        log.info(f"{'真的升级' if self.apply else '演练（设置里没开「真的升级」，不会点确定）'}，共 {len(self.steps)} 步")
        return self.round_success(f'共 {len(self.steps)} 步')

    @node_from(from_name='生成今日步骤')
    @operation_node(name='开始前回大世界')
    def back_to_world_first(self) -> OperationRoundResult:
        """上一个应用或上次中断可能留在别的画面（例如技能强化弹窗），先回大世界，再按 C 打开代理人列表。
        在勘域（锄大地等留下的战斗区域）按 C 打不开代理人列表，ensure_normal_world 会先传送回录像店（2026-11-23 实测）"""
        self.touched_game = True
        op = BackToNormalWorld(self.ctx, ensure_normal_world=True)
        result = self.round_by_op_result(op.execute())
        if not result.is_success:
            self.failed_reason = '开始前回不到大世界'
            self.result['stopped_reason'] = self.failed_reason
        return result

    @node_from(from_name='开始前回大世界')
    @operation_node(name='执行步骤', screenshot_before_round=False, node_max_retry_times=1)
    def run_steps(self) -> OperationRoundResult:
        """每一轮执行一步，执行完回到本节点做下一步；用户点停止时在两步之间停下"""
        if self.step_idx >= len(self.steps):
            return self.round_success('步骤执行完')

        step = self.steps[self.step_idx]
        self.step_idx += 1
        aid, sk = step['agent'], step['skill']
        what = describe(step)
        # 字段和代理人升级的结果一样，资源调度员 2.0 网页「导入升级结果」也能读
        record: dict = {
            'order': step['order'], 'what': what, 'agent': aid, 'type': step['what'], 'skill': sk,
            'from': step['from'], 'to': step['to'],
            'display_from': step.get('display_from'), 'display_to': step.get('display_to'),
            'cost': step.get('cost', {}),
        }
        if step.get('check'):
            record['check'] = step['check']
        if aid in self.skipped_agents:
            record.update(status='skipped', reason=self.skipped_agents[aid])
            return self._next(record)
        if (aid, sk) in self.dead_skills:
            record.update(status='skipped', reason=self.dead_skills[(aid, sk)])
            return self._next(record)

        try:
            note = self.upgrader.run_step(step)
            record.update(status='ok', note=note, verified=self.upgrader.last_spent is not None)
            self.fails[aid] = 0
            log.info(f'完成 {what}：{note}')
            if self.upgrader.last_spent is not None:  # 真的升级并且游戏里核对过
                self._record_verified(step, record)
        except Shortage as e:
            record.update(status='skipped', reason=str(e), needs=e.needs, queued=e.queued)
            self.dead_skills[(aid, sk)] = f'前一级材料不足：{e}'
            log.info(f'跳过 {what}：{e}')
        except (SkillMaxed, AgentLevelLow) as e:  # 不算失败：这个技能今天升不了，同一代理人的其他技能照常
            record.update(status='skipped', reason=str(e))
            self.dead_skills[(aid, sk)] = str(e)
            log.info(f'跳过 {what}：{e}')
        except AgentNotFound as e:
            record.update(status='skipped', reason=str(e))
            self.skipped_agents[aid] = f'找不到这个代理人：{e}'
            self.upgrader.forget_position()
            log.info(f'跳过 {what}：{e}')
        except (LevelMismatch, Skip) as e:
            record.update(status='skipped', reason=str(e))
            self.dead_skills[(aid, sk)] = f'前一级没升成：{e}'
            self._count_fail(aid, str(e))
            log.info(f'跳过 {what}：{e}')
        except ReserveReached as e:
            record.update(status='skipped', reason=str(e))
            self.result['stopped_reason'] = str(e)
            log.info(str(e))
            return self._next(record, finish=True)
        except StopRun as e:
            record.update(status='failed', reason=str(e))
            if self.upgrader.last_spent is not None:  # 已经点了确定、等级也核对过（例如丁尼对不上才停），照样记下
                self._record_verified(step, record)
            self._observe(step, record)
            self.failed_reason = str(e)
            self.result['stopped_reason'] = str(e)
            log.error(f'停止：{e}')
            self._next(record)
            return self.round_fail(str(e))
        except Exception as e:  # noqa: BLE001 单步意外出错只记失败，回大世界后继续下一步
            record.update(status='failed', reason=repr(e))
            self.dead_skills[(aid, sk)] = '前一级出错'
            self.upgrader.forget_position()
            self._count_fail(aid, repr(e))
            log.error(f'失败 {what}', exc_info=True)
            self._observe(step, record)
            # 不知道现在停在什么画面（可能是还开着的强化弹窗），先回大世界，回不去就整体停下，不在未知画面上按键
            if not self.round_by_op_result(BackToNormalWorld(self.ctx, ensure_normal_world=True).execute()).is_success:
                self.failed_reason = f'{what} 出错后回不到大世界'
                self.result['stopped_reason'] = self.failed_reason
                self._next(record)
                return self.round_fail(self.failed_reason)
            return self._next(record)
        self._observe(step, record)
        return self._next(record)

    @node_from(from_name='生成今日步骤', status=STATUS_NO_STEPS, ignore_status=False)
    @node_from(from_name='生成今日步骤', success=False)
    @node_from(from_name='开始前回大世界', success=False)
    @node_from(from_name='执行步骤')
    @node_from(from_name='执行步骤', success=False)
    @operation_node(name='保存结果', screenshot_before_round=False)
    def save_result(self) -> OperationRoundResult:
        self._write_result()
        return self.round_success(str(self.result_path))

    @node_from(from_name='保存结果')
    @operation_node(name='返回大世界')
    def back_to_world(self) -> OperationRoundResult:
        if not self.touched_game:
            return self._finish(self.round_success(self.result.get('stopped_reason') or STATUS_NO_STEPS))
        op = BackToNormalWorld(self.ctx, ensure_normal_world=True)
        return self._finish(self.round_by_op_result(op.execute()))

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

    def _record_verified(self, step: dict, record: dict) -> None:
        """游戏里核对过的升级：current.json 里的技能等级 +1（花掉的材料由 _observe 按弹窗数字记进背包校正）"""
        record['spent'] = self.upgrader.last_spent
        try:
            if write_verified_skill(step['agent'], step['skill'], step['to']):
                self.result['current_updated'].append(
                    {'agent': step['agent'], 'skill': step['skill'], 'base_level': step['to']})
        except (OSError, ValueError, KeyError) as e:
            record['write_error'] = repr(e)
            log.error('写 current.json 失败', exc_info=True)

    def _observe(self, step: dict, record: dict) -> None:
        """
        把强化弹窗里看到的真实数量记进背包校正（config/skill_upgrade/bag_adjust.json），每日推送和下次生成步骤都会用：
        真的升级成功的记升完剩下的（有 − 需，丁尼优先用升级后读到的），其余记弹窗里的「有」。
        只记步骤花费里有名字的材料（芯片、仓鼠笼、丁尼），认不出名字的不记。和写 current.json 分开，一个失败不影响另一个。
        """
        up = self.upgrader
        if up is None:
            return
        if up.last_dialog is not None:
            record['dialog'] = up.last_dialog
        if up.last_levels is not None:
            record['levels'] = up.last_levels
        if not up.last_dialog or self.state is None:
            return
        spent = up.last_spent or {}
        names = set(step.get('cost') or {}) | {DENNY}
        seen: dict[str, int] = {name: v['have'] - spent.get(name, 0)
                                for name, v in up.last_dialog.items() if name in names}
        if spent and up.dennies_after is not None:
            seen[DENNY] = up.dennies_after
        if not seen:
            return
        try:
            observe_bag(self.state, seen, why=record['what'])
            record['seen'] = seen
        except (OSError, ValueError, KeyError) as e:
            record['observe_error'] = repr(e)
            log.error('写背包校正 bag_adjust.json 失败', exc_info=True)

    def _next(self, record: dict, finish: bool = False) -> OperationRoundResult:
        self.result['steps'].append(record)
        self._write_result()
        if finish:
            return self.round_success('丁尼到保留线')
        return self.round_wait(record['what'])

    def _write_result(self) -> None:
        """结果写到 config/skill_upgrade/results/<时间>.json，逐步记录 ok / skipped / failed 和原因"""
        if self.result_path is None or not self.result:
            return
        if self.upgrader is not None:
            self.result['dennies_before'] = self.upgrader.dennies_before
            self.result['dennies_after'] = self.upgrader.dennies_after
        self.result['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        self.result_path.write_text(json.dumps(self.result, ensure_ascii=False, indent=2), encoding='utf-8')
