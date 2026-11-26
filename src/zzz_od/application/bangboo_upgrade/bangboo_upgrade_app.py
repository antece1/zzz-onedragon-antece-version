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
from zzz_od.application.bangboo_upgrade import bangboo_upgrade_const
from zzz_od.application.bangboo_upgrade.bangboo_upgrade_config import BangbooUpgradeConfig
from zzz_od.application.bangboo_upgrade.bangboo_upgrade_runner import BangbooUpgrader
from zzz_od.application.zzz_application import ZApplication
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.back_to_normal_world import BackToNormalWorld
from zzz_od.operation.transport import Transport


class BangbooUpgradeApp(ZApplication):

    """邦布升级：传送到六分街改装店，找最新的几个带红点邦布，自动添加材料升级并做系统优化。默认只演练，不花材料。"""

    def __init__(self, ctx: ZContext) -> None:
        ZApplication.__init__(
            self,
            ctx=ctx,
            app_id=bangboo_upgrade_const.APP_ID,
            op_name=bangboo_upgrade_const.APP_NAME,
        )
        self.config: BangbooUpgradeConfig = self.ctx.run_context.get_config(
            app_id=bangboo_upgrade_const.APP_ID,
            instance_idx=self.ctx.current_instance_idx,
            group_id=application_const.DEFAULT_GROUP_ID,
        )
        self.game: UpgradeGame | None = None
        self.upgrader: BangbooUpgrader | None = None
        self.targets: list[dict] = []
        self.target_idx: int = 0
        self.budget: dict = {}
        self.result: dict = {}
        self.result_path: Path | None = None

    def handle_init(self) -> None:
        self.game = None
        self.upgrader = None
        self.targets = []
        self.target_idx = 0
        self.budget = {}
        self.result = {}
        self.result_path = None

    @operation_node(name='开始前检查', is_start_node=True, screenshot_before_round=False)
    def precheck(self) -> OperationRoundResult:
        stamp = time.strftime('%Y-%m-%d_%H%M%S')
        log_dir = Path(os_utils.get_path_under_work_dir('.log', 'bangboo_upgrade'))
        self.result_path = log_dir / f'{stamp}.json'
        self.game = UpgradeGame(self.ctx, log_dir / stamp)
        try:
            self.game.precheck()
        except StopRun as e:
            return self.round_fail(str(e))
        apply = self.config.apply
        self.upgrader = BangbooUpgrader(self.game, apply)
        self.budget = {'left': self.config.max_dennies, 'spent': 0}
        self.result = {
            'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'applied': apply,
            'count': self.config.count, 'target_level': self.config.target_level,
            'optimize': self.config.optimize, 'max_dennies': self.config.max_dennies,
            'bangboos': [], 'stopped_reason': None,
        }
        log.info(f"{'真的升级' if apply else '演练'}：最新 {self.config.count} 个带红点邦布，"
                 f"目标 {self.config.target_level} 级，系统优化{'开' if self.config.optimize else '关'}，"
                 f"丁尼上限 {self.config.max_dennies}")
        return self.round_success()

    @node_from(from_name='开始前检查')
    @operation_node(name='传送到改装店')
    def transport(self) -> OperationRoundResult:
        if self.upgrader.in_bangboo_screen():
            return self.round_success('已在邦布界面')
        op = Transport(self.ctx, '六分街', '改装店')
        return self.round_by_op_result(op.execute())

    @node_from(from_name='传送到改装店')
    @operation_node(name='进入邦布界面', node_max_retry_times=5)
    def enter(self) -> OperationRoundResult:
        if self.upgrader.in_bangboo_screen():
            return self.round_success()
        self.ctx.controller.interact(press=True, press_time=0.2, release=True)
        return self.round_retry('按 F 交互', wait=3)

    @node_from(from_name='进入邦布界面')
    @operation_node(name='找带红点的邦布', screenshot_before_round=False)
    def find(self) -> OperationRoundResult:
        try:
            self.targets = self.upgrader.find_targets(self.config.count)
        except StopRun as e:
            self.result['stopped_reason'] = str(e)
            return self.round_fail(str(e))
        if not self.targets:
            return self.round_success('没有带红点的邦布')
        return self.round_success(f'{len(self.targets)} 个')

    @node_from(from_name='找带红点的邦布')
    @operation_node(name='升级邦布', screenshot_before_round=False, node_max_retry_times=1)
    def upgrade(self) -> OperationRoundResult:
        """每一轮升级一个邦布，做完回到本节点做下一个；用户点停止时在两个邦布之间停下"""
        if self.target_idx >= len(self.targets):
            return self.round_success('全部处理完')
        target = self.targets[self.target_idx]
        self.target_idx += 1
        try:
            self.upgrader.select(target)
            rec = self.upgrader.upgrade_one(self.config.target_level, self.config.optimize, self.budget)
            log.info(f"邦布 {rec['name']}：{rec['before']} → {rec.get('after')}，{rec.get('stopped') or ''}")
        except StopRun as e:
            self.result['stopped_reason'] = str(e)
            log.error(f'停止：{e}')
            self._write_result()
            return self.round_fail(str(e))
        except Exception as e:  # noqa: BLE001 单个邦布意外出错只记失败，继续下一个
            rec = {'name': '?', 'error': repr(e)}
            log.error('邦布升级出错', exc_info=True)
        self.result['bangboos'].append(rec)
        return self.round_wait(rec.get('name', ''))

    @node_from(from_name='升级邦布')
    @operation_node(name='保存结果', screenshot_before_round=False)
    def save_result(self) -> OperationRoundResult:
        self._write_result()
        return self.round_success(str(self.result_path))

    @node_from(from_name='保存结果')
    @node_from(from_name='升级邦布', success=False)
    @node_from(from_name='找带红点的邦布', success=False)
    @operation_node(name='返回大世界')
    def back_to_world(self) -> OperationRoundResult:
        op = BackToNormalWorld(self.ctx)
        return self.round_by_op_result(op.execute())

    def _write_result(self) -> None:
        """结果写到 .log/bangboo_upgrade/<时间>.json"""
        if self.result_path is None or not self.result:
            return
        self.result['dennies_spent'] = self.budget.get('spent', 0)
        self.result['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        self.result_path.write_text(json.dumps(self.result, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info(f'结果写到 {self.result_path}')
