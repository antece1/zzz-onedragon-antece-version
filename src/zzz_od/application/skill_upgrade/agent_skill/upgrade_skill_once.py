from __future__ import annotations

from typing import ClassVar

from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.base.screen import screen_utils
from one_dragon.base.screen.screen_utils import FindAreaResultEnum
from one_dragon.utils.log_utils import log
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_data import DENNY, SKILLS, skill_item_name
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_parse import (
    AgentSkillParse,
    ConvertReading,
    DialogReading,
)
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_session import (
    AgentSkillSession,
    SkillStepOutcome,
    SkillStepStatus,
    focus_game,
)
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.zzz_operation import ZOperation


class UpgradeSkillOnce(ZOperation):
    """
    在代理人技能页把一个技能强化一级（开始和正常结束都停在「代理人-技能」）。
    读 5 个技能等级 → 核对步骤的 from → 点技能图标进详情 → 点「技能强化」→ 读弹窗（等级、材料、丁尼、红字等级不足）→
    演练：关弹窗；真升级且花费、保留线都核对通过：点一次「确定」→ 关掉下一级的弹窗 → 回技能页核对等级 +1 和丁尼。
    材料不够时自动转化（session.convert）：只差一种芯片、游戏给出「转化获取」时点它 → 读「材料转化」弹窗并核对 →
    演练：点「取消」（真升级才做的核对也要过才算「可以转化」，结论和真升级一样）；
    真升级且核对通过：点一次「确认」→ 游戏弹出「获得」（转化出来的芯片）时记下显示的名字和数量、只点它的「确认」→
    回到强化弹窗核对芯片和丁尼正好变了、「确定」是白字、「获得」的标题已经没了 → 走原来的「点确定」。
    对不上就点「取消」（绝不点确认）、关弹窗、按材料不足处理。
    结果在 self.outcome（SkillStepOutcome），status 见 SkillStepStatus。不写任何文件（除了截图）
    """

    SCREEN_SKILL: ClassVar[str] = '代理人-技能'
    SCREEN_DETAIL: ClassVar[str] = '代理人-技能详情'
    SCREEN_DIALOG: ClassVar[str] = '代理人-技能强化弹窗'
    SCREEN_CONVERT: ClassVar[str] = '代理人-材料转化弹窗'
    # 确认转化后游戏弹出的「获得」（全局画面，标题「获得」+ 一个「确认」，只用来认画面）；
    # 图标下的名字和数量、点的「确认」配在本应用的转化弹窗里（窄区域、只认白字「确认」，碰不到转化弹窗的「确认」）
    SCREEN_OBTAINED: ClassVar[str] = '道具处理-获得'
    AREA_OBTAINED_TITLE: ClassVar[str] = '标题-获得'
    AREA_OBTAINED_OK: ClassVar[str] = '按钮-确认'
    AREA_OBTAINED_BTN: ClassVar[str] = '按钮-获得确认'
    AREA_OBTAINED_ITEM: ClassVar[str] = '区域-获得物品'
    OBTAINED_MAX_CLICKS: ClassVar[int] = 3  # 「获得」的「确认」最多点几次（点了还不关就截图停下）
    DIM_CONFIRM_MAX_ROUNDS: ClassVar[int] = 3  # 转化后数字对上了但「确定」不是白字，最多再等几轮（「获得」在淡出）
    AREA_CONV_TITLE: ClassVar[str] = '标题-材料转化'
    AREA_CONVERT: ClassVar[str] = '按钮-转化获取'
    AREA_CONV_OK: ClassVar[str] = '按钮-确认'
    AREA_CONV_CANCEL: ClassVar[str] = '按钮-取消'
    AREA_CONV_SENTENCE: ClassVar[str] = '区域-转化说明'
    AREA_CONV_SRC: ClassVar[str] = '区域-源数量'
    AREA_CONV_DST: ClassVar[str] = '区域-目标数量'
    AREA_CONV_SRC_ICON: ClassVar[str] = '区域-源图标'
    AREA_CONV_DST_ICON: ClassVar[str] = '区域-目标图标'
    AREA_CONV_DENNY: ClassVar[str] = '区域-消耗丁尼'
    STATUS_CONVERT_OPENED: ClassVar[str] = '已点转化获取'
    STATUS_CONVERT_GO: ClassVar[str] = '核对转化通过'
    STATUS_CONVERT_CANCEL: ClassVar[str] = '取消转化'
    STATUS_CONVERTED: ClassVar[str] = '已确认转化'
    AREA_DETAIL_LEVEL: ClassVar[str] = '区域-技能等级'
    AREA_UPGRADE: ClassVar[str] = '按钮-技能强化'
    AREA_CONTENT: ClassVar[str] = '区域-弹窗内容'
    AREA_DENNY: ClassVar[str] = '区域-丁尼'
    AREA_CONFIRM: ClassVar[str] = '按钮-确定'
    AREA_CLOSE: ClassVar[str] = '按钮-关闭'
    AREA_BACK: ClassVar[str] = '按钮-返回'
    STATUS_OPEN_DETAIL: ClassVar[str] = '打开详情'
    STATUS_CONFIRMED: ClassVar[str] = '已点确定'

    def __init__(self, ctx: ZContext, session: AgentSkillSession, step: dict) -> None:
        ZOperation.__init__(self, ctx, op_name=f"强化 {step.get('name_cn') or step['agent']} {step['skill']}",
                            timeout_seconds=180)
        self.session: AgentSkillSession = session
        self.step: dict = step
        self.sk: str = step['skill']
        self.frm: int = step.get('display_from', step['from'])
        self.to: int = step.get('display_to', step['to'])
        self.base_from: int = step['from']
        self._reset_state()

    def _reset_state(self) -> None:
        self.outcome: SkillStepOutcome = SkillStepOutcome()
        self.reading: DialogReading | None = None
        self.named: list[tuple[str, int, int]] = []
        self.decision: str | None = None
        self.confirm_clicked: bool = False
        self.after_shot_done: bool = False
        self.after_unknown_rounds: int = 0
        self.confirm_not_applied: bool = False
        # 材料转化
        self.convert_opened: bool = False  # 已点过「转化获取」（绝不点第二次）
        self.conversion_clicked: bool = False  # 已点过转化弹窗的「确认」（绝不点第二次）
        self.convert_reading: ConvertReading | None = None
        self.reading_before: DialogReading | None = None  # 转化前的强化弹窗
        self.named_before: list[tuple[str, int, int]] = []
        self.convert_idx: int | None = None  # 不够的那种材料在弹窗里的序号
        self.apply_precheck_why: str | None = None  # 演练：真升级时点之前的核对就不会转化的原因（演练照样打开转化弹窗看）
        self.after_convert_shot_done: bool = False
        self.after_convert_unknown_rounds: int = 0
        self.after_convert_last_bad: str | None = None
        self.obtained_clicks: int = 0  # 点过几次「获得」的「确认」
        self.after_convert_dim_rounds: int = 0  # 转化后数字对上了、「确定」还不是白字的轮数

    def handle_init(self) -> None:
        self._reset_state()

    # ---------- 读技能页 ----------
    @operation_node(name='读技能等级', is_start_node=True, node_max_retry_times=2)
    def read_levels(self) -> OperationRoundResult:
        if self.step.get('what') != 'skill':  # 升级代理人等步骤绝不能走到强化技能
            return self._fail(SkillStepStatus.REHEARSAL_GUARD, f"这一步不是技能（{self.step.get('what')}）", 'not_skill_step')
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_SKILL])
        if name != self.SCREEN_SKILL:
            return self._retry_or_fail(SkillStepStatus.SCREEN_WRONG, '不在技能页', 'not_skill_page')
        levels = self._read_skill_levels()
        if levels is None:
            return self._retry_or_fail(SkillStepStatus.LEVELS_UNREADABLE, '技能页的 5 个等级没读全',
                                       'skill_levels_unreadable')
        lv = {k: v[0] for k, v in levels.items()}
        self.outcome.levels = lv
        log.info(f"{self.step.get('name_cn')} 当前技能 {levels}")
        key = (self.step['agent'], self.sk)
        if lv[self.sk] != self.frm:
            if not self.session.apply and self.session.sim.get(key) == self.frm:
                self.session.sim[key] = self.to
                return self._finish(SkillStepStatus.SIMULATED,
                                    f'演练：前一步升完后是 {self.frm}，这一步的花费要到那时才能读到')
            return self._finish(SkillStepStatus.LEVEL_MISMATCH,
                                f'当前 {self.sk} 是 {lv[self.sk]}，清单写的是 {self.frm}，账号数据变了')
        cap = levels[self.sk][1]
        if cap and self.frm >= cap:
            return self._finish(SkillStepStatus.MAXED, f'{self.sk} 已到当前上限 {cap}，要先突破')
        self.session.sim[key] = self.to
        self.session.dennies_after = None
        return self.round_success(self.STATUS_OPEN_DETAIL)

    @node_from(from_name='读技能等级', status=STATUS_OPEN_DETAIL)
    @operation_node(name='打开详情', node_max_retry_times=1)
    def open_detail(self) -> OperationRoundResult:
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_SKILL])
        if name != self.SCREEN_SKILL:
            return self._retry_or_fail(SkillStepStatus.SCREEN_WRONG, '点技能图标前不在技能页', 'not_skill_page')
        focus_game(self.ctx)
        result = self.round_by_click_area(self.SCREEN_SKILL, f'技能-{self.sk}', success_wait=1.5)
        if result.is_success:
            return result
        return self._retry_or_fail(SkillStepStatus.SCREEN_WRONG, f'点不了{self.sk}图标（{result.status}）', 'skill_icon')

    @node_from(from_name='打开详情')
    @operation_node(name='确认详情', node_max_retry_times=3)
    def confirm_detail(self) -> OperationRoundResult:
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_DETAIL])
        if name == self.SCREEN_DETAIL:
            area = self.ctx.screen_loader.get_area(self.SCREEN_DETAIL, self.AREA_DETAIL_LEVEL)
            got = AgentSkillParse.detail_level(self.ctx.ocr_service.get_ocr_result_list(
                self.last_screenshot, rect=area.rect, color_range=area.color_range))
            if got == self.frm:
                return self.round_success(f'详情页等级{got}')
            return self._retry_or_fail(SkillStepStatus.SCREEN_WRONG, f'{self.sk}详情页的等级是 {got}，应该是 {self.frm}',
                                       f'{self.sk}_detail_level')
        return self._retry_or_fail(SkillStepStatus.SCREEN_WRONG, f'没进到{self.sk}详情页', f'{self.sk}_detail')

    @node_from(from_name='确认详情')
    @operation_node(name='打开弹窗', node_max_retry_times=2)
    def open_dialog(self) -> OperationRoundResult:
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_DETAIL])
        if name != self.SCREEN_DETAIL:
            return self._retry_or_fail(SkillStepStatus.SCREEN_WRONG, '点技能强化前不在详情页', f'{self.sk}_detail')
        focus_game(self.ctx)
        result = self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_DETAIL, self.AREA_UPGRADE,
                                                   pre_delay=0, success_wait=1.5)
        if result.is_success:
            return result
        return self._retry_or_fail(SkillStepStatus.SCREEN_WRONG, f'详情页找不到「技能强化」（{result.status}）',
                                   f'{self.sk}_no_upgrade_button')

    # ---------- 弹窗 ----------
    @node_from(from_name='打开弹窗')
    @operation_node(name='读弹窗', node_max_retry_times=3)
    def read_dialog(self) -> OperationRoundResult:
        agent = self.step['agent']
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_DIALOG])
        if name != self.SCREEN_DIALOG:
            return self._retry_or_fail(SkillStepStatus.DIALOG_WRONG, '没打开技能强化弹窗', f'{agent}_{self.sk}_no_dialog')
        reading = self._read_dialog()
        if reading.from_lv != self.frm or reading.to_lv != self.frm + 1:
            return self._retry_or_fail(SkillStepStatus.DIALOG_WRONG,
                                       f'弹窗等级是 {reading.from_lv}→{reading.to_lv}，应该是 {self.frm}→{self.frm + 1}',
                                       f'{agent}_{self.sk}_{self.frm}_dialog_wrong')
        if reading.den_pair is None:
            return self._retry_or_fail(SkillStepStatus.NO_COST, '强化弹窗里读不到丁尼', f'{agent}_{self.sk}_{self.frm}_no_cost')
        path = self.session.shot(f'{agent}_{self.sk}_{self.frm}_dialog', self.last_screenshot)
        self.outcome.shot = path.name if path else None
        self.reading = reading
        self.session.dennies_last = reading.den_have
        self.named = AgentSkillParse.name_materials(reading.mats, agent, self.base_from, self.step.get('cost'))
        dialog: dict[str, dict[str, int]] = {DENNY: {'have': reading.den_have, 'need': reading.den_need}}
        for mat, h, n in self.named:
            dialog[mat] = {'have': h, 'need': n}
        self.outcome.dialog = dialog
        self.outcome.need_level = reading.need_level
        self.outcome.short = reading.den_have < reading.den_need or any(h < n for _, h, n in self.named)
        self.outcome.needs = [(mat, n) for mat, _, n in self.named] + [(DENNY, reading.den_need)]
        expected = self.step.get('cost') or {}
        diff = [f'{k} 预计 {expected[k]} 实际 {v["need"]}' for k, v in dialog.items()
                if k in expected and expected[k] != v['need']]
        if diff:
            log.info(f"弹窗花费和预计不一样：{'，'.join(diff)}")

        status, message = AgentSkillParse.decide(reading, self.named, self.step, self.session.apply,
                                                 self.session.dennies_reserve)
        if status == SkillStepStatus.COST_MISMATCH:
            p = self.session.shot(f'{agent}_{self.sk}_{self.frm}_cost_mismatch', self.last_screenshot)
            message += f"，截图 {p.name if p else '无'}"
        if status == SkillStepStatus.SHORT and self.session.convert:
            conv_status, why = self._precheck_convert(reading)
            if conv_status == SkillStepStatus.CONVERT:
                # 先当作材料不足记下（中间出意外时是安全的结果），转化核对通过、升级做完才改
                self.decision = SkillStepStatus.CONVERT
                self.outcome.status, self.outcome.message = SkillStepStatus.SHORT, message
                if self.session.dennies_before is None:
                    self.session.dennies_before = reading.den_have
                log.info(f'{self.sk} {self.frm}→{self.frm + 1}：材料不足，{why}')
                return self.round_success(SkillStepStatus.CONVERT)
            message += f'（不转化：{why}）'
        if status in (SkillStepStatus.REHEARSED, SkillStepStatus.CONFIRM) and self.session.dennies_before is None:
            self.session.dennies_before = reading.den_have
        if status == SkillStepStatus.CONFIRM and not self.session.apply:  # decide 不会这样返回，这里再拦一次
            status, message = SkillStepStatus.REHEARSED, f'演练：会花 {message}'
        self.decision = status
        self.outcome.status, self.outcome.message = status, message
        log.info(f'{self.sk} {self.frm}→{self.frm + 1}：{status} {message}')
        return self.round_success(status)

    # ---------- 材料转化 ----------
    @node_from(from_name='读弹窗', status=SkillStepStatus.CONVERT)  # 唯一进入「点转化获取」的边
    @operation_node(name='点转化获取', node_max_retry_times=1)
    def click_convert(self) -> OperationRoundResult:
        if self.convert_opened:  # 出过异常又进来：绝不再点，去取消（转化弹窗开着就点取消）
            self._convert_end(SkillStepStatus.CONVERT_SKIPPED, '「转化获取」已经点过一次（中间出了异常），没有再点，已取消')
            return self.round_success(self.STATUS_CONVERT_CANCEL)
        if not self.session.convert or self.decision != SkillStepStatus.CONVERT or self.reading is None:
            return self._fail(SkillStepStatus.CONVERT_GUARD, '没有通过核对，不能点转化获取', 'convert_guard')
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_DIALOG])
        again = self._read_dialog() if name == self.SCREEN_DIALOG else None
        if not self._same_dialog(again):
            self._convert_end(SkillStepStatus.CONVERT_SKIPPED, '点「转化获取」前重新读弹窗，和刚才读的不一样，没有点')
            return self.round_success(SkillStepStatus.CONVERT_SKIPPED)
        if not self._convert_button_shown():
            self._convert_end(SkillStepStatus.CONVERT_SKIPPED, '点之前没看到白字「转化获取」，没有点')
            return self.round_success(SkillStepStatus.CONVERT_SKIPPED)
        self.convert_opened = True  # 点之前就记下：之后出任何异常都不会再点
        self.outcome.convert = {'opened': True}
        self.reading_before = self.reading
        self.named_before = list(self.named)
        focus_game(self.ctx)
        log.info(f"点「转化获取」看 {self.step.get('name_cn')} {self.sk} {self.frm}→{self.frm + 1} 的材料转化")
        result = self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_DIALOG, self.AREA_CONVERT,
                                                   pre_delay=0)
        if not result.is_success:  # 不知道点没点到：去取消（转化弹窗开着就点取消，没开就关强化弹窗）
            self._convert_end(SkillStepStatus.CONVERT_SKIPPED, f'点「转化获取」失败（{result.status}）')
            return self.round_success(self.STATUS_CONVERT_CANCEL)
        return self.round_success(self.STATUS_CONVERT_OPENED, wait=1.5)

    @node_from(from_name='点转化获取', status=STATUS_CONVERT_OPENED)
    @operation_node(name='读转化弹窗', node_max_retry_times=3)
    def read_convert(self) -> OperationRoundResult:
        agent = self.step['agent']
        last = self.node_retry_times >= self.node_max_retry_times
        name = self.check_and_update_current_screen(self.last_screenshot,
                                                    screen_name_list=[self.SCREEN_CONVERT, self.SCREEN_DIALOG])
        if name != self.SCREEN_CONVERT:
            if not last:
                return self.round_retry(f'等转化弹窗（当前 {name}）', wait=1)
            p = self.session.shot(f'{agent}_{self.sk}_{self.frm}_convert_no_popup', self.last_screenshot)
            self._convert_end(SkillStepStatus.CONVERT_SKIPPED,
                              f"点了「转化获取」没出转化弹窗（当前 {name}），截图 {p.name if p else '无'}")
            return self.round_success(self.STATUS_CONVERT_CANCEL)
        conv = self._read_convert()
        if not conv.complete() and not last:  # 弹窗可能还在动画
            return self.round_retry(f'转化弹窗没读全 {conv.as_dict()}', wait=1)
        p = self.session.shot(f'{agent}_{self.sk}_{self.frm}_convert_popup', self.last_screenshot)
        status, msg, plan = AgentSkillParse.check_convert(conv, self.reading, self.named, self._chip_expected(),
                                                          self.step, self.session.apply, self.session.dennies_reserve)
        self.outcome.convert.update(plan)
        self.outcome.convert['popup'] = conv.as_dict()
        self.outcome.convert['shot'] = p.name if p else None
        cost_txt = AgentSkillParse.cost_text(self.reading.den_have, self.reading.den_need, self.named)
        if status != SkillStepStatus.CONVERT:
            self.outcome.convert['cancel_reason'] = msg
            self._convert_end(SkillStepStatus.CONVERT_SKIPPED, f'材料不足，转化核对没通过（{msg}），已取消；{cost_txt}')
            return self.round_success(self.STATUS_CONVERT_CANCEL)
        if not self.session.apply:
            # 演练的结论要和真升级一样：真升级才做的核对（只做 进阶→特化、正好每个 300、强化花费和预计一样、白字「确认」）也要过
            cancel = self._apply_would_cancel(conv)
            if cancel:
                self.outcome.convert['would'] = False
                self.outcome.convert['apply_would_cancel'] = cancel
                self._convert_end(SkillStepStatus.CONVERT_SKIPPED, f'演练：真升级时不会转化（{cancel}），已取消；{cost_txt}')
                return self.round_success(self.STATUS_CONVERT_CANCEL)
            self.outcome.convert['would'] = True
            self._convert_end(SkillStepStatus.CONVERT_REHEARSED, f"演练：会先{msg}，再花 {cost_txt}")
            return self.round_success(self.STATUS_CONVERT_CANCEL)
        self.convert_reading = conv
        log.info(f'转化核对通过：{msg}')
        return self.round_success(self.STATUS_CONVERT_GO)

    @node_from(from_name='读转化弹窗', status=STATUS_CONVERT_GO)  # 唯一进入「点确认转化」的边
    @operation_node(name='点确认转化', node_max_retry_times=1)
    def click_convert_ok(self) -> OperationRoundResult:
        if self.conversion_clicked:  # 绝不点第二次
            return self.round_success(self.STATUS_CONVERTED)
        if (not self.session.apply or not self.session.convert or self.decision != SkillStepStatus.CONVERT
                or self.convert_reading is None):
            return self._fail(SkillStepStatus.CONVERT_GUARD, '没有通过核对，不能点转化确认', 'convert_ok_guard')
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_CONVERT])
        again = self._read_convert() if name == self.SCREEN_CONVERT else None
        if again is None or again.key() != self.convert_reading.key():
            self.outcome.convert['cancel_reason'] = '确认前重新读转化弹窗不一样'
            self._convert_end(SkillStepStatus.CONVERT_SKIPPED,
                              f"确认前重新读转化弹窗，和刚才读的不一样（{again.as_dict() if again else name}），已取消")
            return self.round_success(self.STATUS_CONVERT_CANCEL)
        found = screen_utils.find_area(ctx=self.ctx, screen=self.last_screenshot, screen_name=self.SCREEN_CONVERT,
                                       area_name=self.AREA_CONV_OK)
        if found != FindAreaResultEnum.TRUE:  # 「确认」是灰的或读不出（区域只认白字），不点
            p = self.session.shot(f"{self.step['agent']}_{self.sk}_{self.frm}_convert_ok_disabled", self.last_screenshot)
            self.outcome.convert['cancel_reason'] = '「确认」不可点'
            self._convert_end(SkillStepStatus.CONVERT_SKIPPED,
                              f"转化弹窗的「确认」是灰的，没有点，已取消，截图 {p.name if p else '无'}")
            return self.round_success(self.STATUS_CONVERT_CANCEL)
        focus_game(self.ctx)
        self.conversion_clicked = True  # 点之前就记下：点击后出任何异常都不会再点
        self.outcome.conversion_clicked = True
        self.outcome.convert['confirmed'] = True
        self.outcome.dialog_before = self.outcome.dialog
        self.outcome.dialog = None  # 转化后的数量核对过之前，弹窗里的数字都不能拿来记背包
        self.session.dennies_last = None
        c = self.outcome.convert
        log.info(f"确认转化：{c.get('from')} {c.get('used')} → {c.get('to')} {c.get('made')}（丁尼 {c.get('dennies')}）")
        result = self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_CONVERT, self.AREA_CONV_OK,
                                                   pre_delay=0)
        if not result.is_success:  # 刚才还找得到：不知道到底点没点，按点过处理（停下，不再点）
            return self._fail(SkillStepStatus.CONVERT_FAILED, f'点转化「确认」失败（{result.status}）', 'convert_ok_click_fail')
        return self.round_success(self.STATUS_CONVERTED, wait=2)

    @node_from(from_name='点转化获取', status=STATUS_CONVERT_CANCEL)
    @node_from(from_name='点转化获取')  # 其它意外结果也去取消
    @node_from(from_name='读转化弹窗', status=STATUS_CONVERT_CANCEL)
    @node_from(from_name='读转化弹窗')
    @node_from(from_name='点确认转化', status=STATUS_CONVERT_CANCEL)
    @node_from(from_name='转化后读弹窗', status=STATUS_CONVERT_CANCEL)
    @operation_node(name='取消转化', node_max_retry_times=4)
    def cancel_convert(self) -> OperationRoundResult:
        """转化弹窗开着就只点「取消」（绝不点确认），回到强化弹窗后交给「回技能页」关弹窗"""
        if self.conversion_clicked:
            return self._fail(SkillStepStatus.CONVERT_FAILED, '已经确认了转化，不能当作取消', 'convert_cancel_after_ok')
        if self.decision == SkillStepStatus.CONVERT:  # 没有经过 _convert_end 的意外路径
            self._convert_end(SkillStepStatus.CONVERT_SKIPPED, '转化流程意外中断，已取消')
        name = self.check_and_update_current_screen(self.last_screenshot,
                                                    screen_name_list=[self.SCREEN_CONVERT, self.SCREEN_DIALOG])
        if name == self.SCREEN_DIALOG:
            return self.round_success(self.decision)
        if self.node_retry_times >= self.node_max_retry_times:
            return self._fail(SkillStepStatus.SCREEN_WRONG, f'取消不了转化弹窗（当前 {name}）', 'convert_cancel_fail')
        if name == self.SCREEN_CONVERT:
            focus_game(self.ctx)
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_CONVERT, self.AREA_CONV_CANCEL,
                                              pre_delay=0)
        return self.round_retry(f'取消转化（当前 {name}）', wait=1)

    @node_from(from_name='点确认转化', status=STATUS_CONVERTED)
    @node_from(from_name='点确认转化')  # 其它结果也先看一眼：没点确认时这里会转去取消
    # 重试次数：转化弹窗关闭动画 + 「获得」弹窗出现（实测确认后约 2 秒）+ 最多点 3 次「获得」的确认 + 关闭动画 + 强化弹窗数字刷新，
    # 每轮约 1 秒；原来的 6 次不够这一串（2026-11-25 16:37 实测卡在「获得」上）
    @operation_node(name='转化后读弹窗', node_max_retry_times=10)
    def after_convert(self) -> OperationRoundResult:
        """
        确认转化后回到强化弹窗：芯片正好多了转化出来的数、丁尼正好少了转化花的、什么都不缺、「确定」是白字，才交给「点确定」。
        中间游戏会弹出「获得」（转化出来的芯片和数量）：记下显示的内容，只点这个弹窗自己的「确认」（同一张截图认出是「获得」才点，最多 3 次）。
        「获得」的标题读得出时（正在出现或关掉，半透明盖在强化弹窗上，强化弹窗也认得出）：绝不读、不认强化弹窗；
        整个「获得」认全了才点它的「确认」，没认全就什么都不点、等下一轮。
        别的都不点：认不出的画面只等和截图（通用「关闭」和强化弹窗的 ✕ 是同一个模板，芯片已经换了，不能误关）
        """
        if not self.conversion_clicked:
            return self.round_success(self.STATUS_CONVERT_CANCEL)
        agent = self.step['agent']
        first = not self.after_convert_shot_done  # 第一轮存一张截图（这一帧是「获得」时由 _ack_obtained 存，不重复存）
        self.after_convert_shot_done = True
        last = self.node_retry_times >= self.node_max_retry_times
        name = self.check_and_update_current_screen(
            self.last_screenshot,
            screen_name_list=[self.SCREEN_CONVERT, self.SCREEN_DIALOG, self.SCREEN_SKILL, self.SCREEN_DETAIL])
        # 「获得」是全局画面，不放进上面的列表：转化弹窗还认得出时先等它关；其余情况先看「获得」的标题
        if name != self.SCREEN_CONVERT and self._obtained_title_shown():
            if self._obtained_shown():
                return self._ack_obtained(last)
            if first:
                self.session.shot(f'{agent}_{self.sk}_{self.frm}_after_convert', self.last_screenshot)
            if last:
                return self._fail(SkillStepStatus.CONVERT_FAILED, '确认转化后「获得」弹窗一直没完全出来或没关完（没认全，没有点）',
                                  'after_convert_obtained_partial')
            return self.round_retry(f'「获得」弹窗还在动画（当前 {name}），等一下', wait=1)
        if first:
            self.session.shot(f'{agent}_{self.sk}_{self.frm}_after_convert', self.last_screenshot)
        if name == self.SCREEN_CONVERT:
            if last:
                return self._fail(SkillStepStatus.CONVERT_FAILED, '确认转化后转化弹窗一直没关', 'after_convert_popup_stays')
            return self.round_retry('确认转化后等转化弹窗关掉', wait=1)
        if name in (self.SCREEN_SKILL, self.SCREEN_DETAIL):
            return self._fail(SkillStepStatus.CONVERT_FAILED, f'确认转化后强化弹窗关了（当前 {name}）', 'after_convert_closed')
        if name != self.SCREEN_DIALOG:
            self.after_convert_unknown_rounds += 1
            if self.after_convert_unknown_rounds == 1:
                self.session.shot(f'{agent}_{self.sk}_{self.frm}_after_convert_unknown', self.last_screenshot)
            if last:
                return self._fail(SkillStepStatus.CONVERT_FAILED, f'确认转化后认不出画面（{name}）', 'after_convert_unknown')
            return self.round_retry(f'确认转化后等强化弹窗（当前 {name}）', wait=1)
        after = self._read_dialog()
        r, msg = AgentSkillParse.check_after_convert(self.reading_before, after, self.convert_reading, self.convert_idx)
        if r != 'ok':
            repeated = r == 'bad' and msg == self.after_convert_last_bad
            self.after_convert_last_bad = msg if r == 'bad' else None
            if last or repeated:
                return self._fail(SkillStepStatus.CONVERT_FAILED, f'转化后强化弹窗对不上：{msg}', 'after_convert_mismatch')
            return self.round_retry(msg, wait=1)
        # 数字对上了，同一帧上「确定」也要是白字（「获得」刚点掉、还在淡出时「确定」发暗）；不是就再等几轮，
        # 一直不是白字就交给「点确定」（它重新读一次，还是灰的就停下，不点）
        confirm_white = screen_utils.find_area(ctx=self.ctx, screen=self.last_screenshot, screen_name=self.SCREEN_DIALOG,
                                               area_name=self.AREA_CONFIRM) == FindAreaResultEnum.TRUE
        if not confirm_white:
            self.after_convert_dim_rounds += 1
            if not last and self.after_convert_dim_rounds < self.DIM_CONFIRM_MAX_ROUNDS:
                return self.round_retry('转化后数字对上了，「确定」还不是白字，等一下', wait=1)
        self.session.shot(f'{agent}_{self.sk}_{self.frm}_dialog_after_convert', self.last_screenshot)
        self.outcome.convert['verified'] = True
        self.reading = after
        self.named = AgentSkillParse.name_materials(after.mats, agent, self.base_from, self.step.get('cost'))
        self.outcome.dialog = self._dialog_dict(after, self.named)
        self.outcome.short = False
        self.session.dennies_last = after.den_have
        status, message = AgentSkillParse.decide(after, self.named, self.step, self.session.apply,
                                                 self.session.dennies_reserve)
        if status != SkillStepStatus.CONFIRM:
            return self._fail(SkillStepStatus.CONVERT_FAILED, f'转化完成但没有升级：{status} {message}',
                              'after_convert_no_confirm')
        self.decision = SkillStepStatus.CONFIRM
        c = self.outcome.convert
        self.outcome.status = SkillStepStatus.CONFIRM
        self.outcome.message = f"先用 {c['from']} {c['used']} 转化 {c['to']} {c['made']}（丁尼 {c['dennies']}）；{message}"
        log.info(f'转化核对完成：{msg}')
        return self.round_success(SkillStepStatus.CONFIRM)

    @node_from(from_name='读弹窗')  # 除了「点确定」「点转化获取」，其它决定都是关弹窗回技能页
    @node_from(from_name='点确定', status=SkillStepStatus.CONFIRM_DISABLED)
    @node_from(from_name='点转化获取', status=SkillStepStatus.CONVERT_SKIPPED)
    @node_from(from_name='取消转化')
    @operation_node(name='回技能页', node_max_retry_times=4)
    def back_to_skill_page(self) -> OperationRoundResult:
        name = self.check_and_update_current_screen(
            self.last_screenshot,
            screen_name_list=[self.SCREEN_SKILL, self.SCREEN_DETAIL, self.SCREEN_CONVERT, self.SCREEN_DIALOG])
        if name == self.SCREEN_SKILL:
            return self.round_success(self.decision)
        if self.node_retry_times >= self.node_max_retry_times:
            return self._fail(SkillStepStatus.SCREEN_WRONG, f'回不到技能页（{name}）', 'back_to_skill_page_fail')
        focus_game(self.ctx)
        if name == self.SCREEN_CONVERT:  # 转化弹窗只点「取消」
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_CONVERT, self.AREA_CONV_CANCEL,
                                              pre_delay=0)
        elif name == self.SCREEN_DIALOG:
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_DIALOG, self.AREA_CLOSE, pre_delay=0)
        elif name == self.SCREEN_DETAIL:
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_DETAIL, self.AREA_BACK, pre_delay=0)
        return self.round_retry(f'回技能页（当前 {name}）', wait=1)

    # 只有两条边进入「点确定」：读弹窗核对通过；转化后重新读强化弹窗核对通过（都要真升级、决定是 CONFIRM）
    @node_from(from_name='读弹窗', status=SkillStepStatus.CONFIRM)
    @node_from(from_name='转化后读弹窗', status=SkillStepStatus.CONFIRM)
    @operation_node(name='点确定', node_max_retry_times=1)
    def click_confirm(self) -> OperationRoundResult:
        """
        点之前在新截图上重新读弹窗，要和刚才读的完全一样、「获得」的标题不在、「确定」是白字才点一次。
        确认过转化时（画面可能还在变：「获得」没关完、数字刚刷新）对不上先等一轮再读；没转化时和原来一样直接停下 / 跳过
        """
        if self.confirm_clicked:  # 绝不点第二次
            return self.round_success(self.STATUS_CONFIRMED)
        if not self.session.apply or self.decision != SkillStepStatus.CONFIRM or self.reading is None:
            return self._fail(SkillStepStatus.REHEARSAL_GUARD, '没有通过核对，不能点确定', 'rehearsal_guard')
        last = self.node_retry_times >= self.node_max_retry_times
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_DIALOG])
        again = self._read_dialog() if name == self.SCREEN_DIALOG else None
        covered = self._obtained_title_shown()  # 「获得」盖在上面时点「确定」会被它吃掉
        if (covered or again is None or again.from_lv != self.frm or again.to_lv != self.frm + 1
                or again.den_pair != self.reading.den_pair or again.mats != self.reading.mats
                or again.need_level):
            if self.conversion_clicked and not last:
                return self.round_retry('点确定前画面还在变（「获得」没关完或数字刚刷新），等一下再读', wait=1)
            why = '「获得」弹窗还盖在上面' if covered else '和刚才读的不一样'
            return self._fail(SkillStepStatus.SCREEN_CHANGED, f'点确定前重新读弹窗，{why}，没有点确定',
                              'screen_changed_before_confirm')
        found = screen_utils.find_area(ctx=self.ctx, screen=self.last_screenshot, screen_name=self.SCREEN_DIALOG,
                                       area_name=self.AREA_CONFIRM)
        if found != FindAreaResultEnum.TRUE and self.conversion_clicked:  # 已经转化了：再等一轮，还是灰的就停下，不当作普通的跳过
            if not last:
                return self.round_retry('转化后「确定」还不是白字，等一下再看', wait=1)
            return self._fail(SkillStepStatus.CONVERT_FAILED, '转化后「确定」是灰的，没有点', 'convert_confirm_disabled')
        if found != FindAreaResultEnum.TRUE:  # 「确定」是灰的（区域只认白字），不点
            p = self.session.shot(f"{self.step['agent']}_{self.sk}_{self.frm}_confirm_disabled", self.last_screenshot)
            self.decision = SkillStepStatus.CONFIRM_DISABLED
            self.outcome.status = SkillStepStatus.CONFIRM_DISABLED
            self.outcome.message = f"「确定」是灰的，没有点（{self.outcome.message}），截图 {p.name if p else '无'}"
            return self.round_success(SkillStepStatus.CONFIRM_DISABLED)
        focus_game(self.ctx)
        self.confirm_clicked = True  # 点之前就记下：点击后出任何异常都不会再点
        self.session.dennies_last = None  # 花了丁尼：读到升级后的数量之前不知道现有多少
        self.outcome.confirm_clicked = True
        log.info(f"确认强化 {self.step.get('name_cn')} {self.sk} {self.frm}→{self.frm + 1}")
        result = self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_DIALOG, self.AREA_CONFIRM,
                                                   pre_delay=0, success_wait=2.5)
        if not result.is_success:  # 刚才还找得到：不知道到底点没点，按点过处理（停下，不再点）
            return self._fail(SkillStepStatus.SCREEN_CHANGED, f'点确定失败（{result.status}）', 'confirm_click_fail')
        return self.round_success(self.STATUS_CONFIRMED)

    @node_from(from_name='点确定')
    @operation_node(name='确定后收尾', node_max_retry_times=8)
    def after_confirm(self) -> OperationRoundResult:
        if not self.after_shot_done:
            self.after_shot_done = True
            self.session.shot(f"{self.step['agent']}_{self.sk}_{self.frm}_after_confirm", self.last_screenshot)
        name = self.check_and_update_current_screen(
            self.last_screenshot, screen_name_list=[self.SCREEN_SKILL, self.SCREEN_DETAIL, self.SCREEN_DIALOG])
        if name == self.SCREEN_SKILL:
            return self.round_success('回到技能页')
        if self.node_retry_times >= self.node_max_retry_times:
            return self._fail(SkillStepStatus.SCREEN_WRONG, f'点确定后回不到技能页（{name}）', 'after_confirm_fail')
        focus_game(self.ctx)
        if self.conversion_clicked and self._obtained_title_shown():
            # 转化后的「获得」这时才盖上来：不点 ✕ 也不点别的，认全了只点它自己的「确认」（和转化后读弹窗共用 3 次上限）
            if self._obtained_shown():
                self._note_obtained()
                if self.obtained_clicks < self.OBTAINED_MAX_CLICKS and self._obtained_button_shown():
                    self.obtained_clicks += 1
                    self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_CONVERT, self.AREA_OBTAINED_BTN,
                                                      pre_delay=0)
            return self.round_retry(f'确定后「获得」弹窗盖着（当前 {name}），等它关掉', wait=1)
        if name == self.SCREEN_DIALOG:
            nxt = self._read_dialog()
            if nxt.from_lv == self.frm + 1 and nxt.den_have is not None:  # 弹窗变成下一级：读升级后的丁尼
                self.session.dennies_after = nxt.den_have
                self.session.dennies_last = nxt.den_have
                self.outcome.dennies_after = nxt.den_have
            elif nxt.from_lv == self.frm:
                self.confirm_not_applied = True
                log.error('点确定后弹窗还是同一级，强化可能没有生效')
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_DIALOG, self.AREA_CLOSE, pre_delay=0)
        elif name == self.SCREEN_DETAIL:
            self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_DETAIL, self.AREA_BACK, pre_delay=0)
        else:
            # 认不出的画面：先等画面稳定，第一次存截图；从第 2 轮起只点通用「关闭」（模板匹配到才点，用来关提示弹窗）。
            # 绝不点「返回」：技能页还没加载完/没认出来时点返回，会一路退到代理人列表、大世界（2026-11-25 柳 支援 1→2 实测）
            self.after_unknown_rounds += 1
            if self.after_unknown_rounds == 1:
                self.session.shot(f"{self.step['agent']}_{self.sk}_{self.frm}_after_confirm_unknown", self.last_screenshot)
            else:
                self.round_by_find_and_click_area(self.last_screenshot, '画面-通用', '关闭', pre_delay=0)
        return self.round_retry(f'确定后回技能页（当前 {name}）', wait=1)

    @node_from(from_name='确定后收尾')
    @operation_node(name='核对', node_max_retry_times=2)
    def verify(self) -> OperationRoundResult:
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_SKILL])
        levels = self._read_skill_levels() if name == self.SCREEN_SKILL else None
        if levels is None:
            return self._retry_or_fail(SkillStepStatus.VERIFY_FAILED, '强化后读不到技能等级', 'verify_unreadable')
        lv = {k: v[0] for k, v in levels.items()}
        self.outcome.levels = lv
        if lv[self.sk] != self.frm + 1:
            if self.confirm_not_applied and lv[self.sk] == self.frm:
                return self._fail(SkillStepStatus.VERIFY_FAILED,
                                  f'点了确定但强化没有生效：弹窗还是 {self.frm}→{self.frm + 1}，{self.sk} 还是 {self.frm}，停下检查',
                                  'confirm_not_applied')
            return self._fail(SkillStepStatus.VERIFY_FAILED,
                              f'强化后 {self.sk} 是 {lv[self.sk]}，应该是 {self.frm + 1}，停下检查', 'verify_failed')
        # 等级已经 +1：材料确实花掉了，先记下来，后面丁尼对不上要停下时也能写回
        den_have, den_need = self.reading.den_pair
        spent = {DENNY: den_need}
        for mat, _, n in self.named:
            spent[mat] = spent.get(mat, 0) + n
        self.outcome.spent = spent
        after = self.outcome.dennies_after
        if after is None:
            self.outcome.dennies_unverified = True
        elif after != den_have - den_need:
            return self._fail(SkillStepStatus.DENNY_MISMATCH, f'丁尼对不上：强化前 {den_have}，花 {den_need}，现在 {after}',
                              'denny_mismatch')
        cost_txt = AgentSkillParse.cost_text(den_have, den_need, self.named)
        return self._finish(SkillStepStatus.DONE, f'{self.sk} {self.frm}→{self.frm + 1} 完成，花 {cost_txt}')

    @node_from(from_name='读技能等级')  # 模拟 / 等级对不上 / 已到上限：不点任何东西
    @node_from(from_name='回技能页')
    @node_from(from_name='核对')
    @operation_node(name='完成', screenshot_before_round=False)
    def done(self) -> OperationRoundResult:
        return self.round_success(self.outcome.status, data=self.outcome)

    # ---------- 内部 ----------
    def _read_skill_levels(self) -> dict[str, tuple[int, int]] | None:
        """技能页 5 个技能的 (当前, 上限)；任何一个读不出返回 None"""
        out: dict[str, tuple[int, int]] = {}
        for sk in SKILLS:
            area = self.ctx.screen_loader.get_area(self.SCREEN_SKILL, f'技能等级-{sk}')
            v = AgentSkillParse.skill_level(self.ctx.ocr_service.get_ocr_result_list(
                self.last_screenshot, rect=area.rect, color_range=area.color_range))
            if v is None:
                return None
            out[sk] = v
        return out

    def _read_dialog(self) -> DialogReading:
        """强化弹窗：内容区（等级、材料、红字）和丁尼区各读一次"""
        content = self.ctx.screen_loader.get_area(self.SCREEN_DIALOG, self.AREA_CONTENT)
        denny = self.ctx.screen_loader.get_area(self.SCREEN_DIALOG, self.AREA_DENNY)
        return AgentSkillParse.parse_dialog(
            self.ctx.ocr_service.get_ocr_result_list(self.last_screenshot, rect=content.rect,
                                                     color_range=content.color_range),
            self.ctx.ocr_service.get_ocr_result_list(self.last_screenshot, rect=denny.rect,
                                                     color_range=denny.color_range))

    @staticmethod
    def _dialog_dict(reading: DialogReading, named: list[tuple[str, int, int]]) -> dict[str, dict[str, int]]:
        dialog: dict[str, dict[str, int]] = {DENNY: {'have': reading.den_have, 'need': reading.den_need}}
        for mat, h, n in named:
            dialog[mat] = {'have': h, 'need': n}
        return dialog

    def _same_dialog(self, again: DialogReading | None) -> bool:
        """新截图上重新读的强化弹窗和 self.reading 完全一样（等级、丁尼、材料，没有红字）"""
        return (again is not None and self.reading is not None and again.from_lv == self.frm
                and again.to_lv == self.frm + 1 and again.den_pair == self.reading.den_pair
                and again.mats == self.reading.mats and not again.need_level)

    def _convert_button_shown(self) -> bool:
        """这一帧上是白字「转化获取」、不是「确定」（两个按钮在同一个位置）"""
        conv = screen_utils.find_area(ctx=self.ctx, screen=self.last_screenshot, screen_name=self.SCREEN_DIALOG,
                                      area_name=self.AREA_CONVERT)
        ok = screen_utils.find_area(ctx=self.ctx, screen=self.last_screenshot, screen_name=self.SCREEN_DIALOG,
                                    area_name=self.AREA_CONFIRM)
        return conv == FindAreaResultEnum.TRUE and ok != FindAreaResultEnum.TRUE

    def _chip_expected(self) -> str | None:
        """这一级要的芯片名，只认 assets/agent_upgrade/agents/<id>.json（不用预计花费里猜的名字）"""
        try:
            return skill_item_name(self.step['agent'], self.base_from)
        except (OSError, ValueError, KeyError, TypeError):
            log.error('查这一级要的芯片名失败', exc_info=True)
            return None

    def _precheck_convert(self, reading: DialogReading) -> tuple[str, str]:
        """材料不足时：值不值得点「转化获取」（只差一种芯片、按钮确实是「转化获取」、丁尼和保留线按估算够）"""
        status, why = AgentSkillParse.convert_precheck(reading, self.named, self.step, self._chip_expected(),
                                                       self.session.apply, self.session.dennies_reserve)
        if status != SkillStepStatus.CONVERT:
            return status, why
        if not self._convert_button_shown():
            return SkillStepStatus.SHORT, '没看到白字「转化获取」'
        short = AgentSkillParse.short_materials(self.named)
        self.convert_idx = short[0][0]
        self.apply_precheck_why = None
        if not self.session.apply:  # 演练照样打开转化弹窗看，但记下真升级时这里就不会转化的原因
            a_status, a_why = AgentSkillParse.convert_precheck(reading, self.named, self.step, self._chip_expected(),
                                                               True, self.session.dennies_reserve)
            if a_status != SkillStepStatus.CONVERT:
                self.apply_precheck_why = a_why
        return status, why

    def _apply_would_cancel(self, conv: ConvertReading) -> str:
        """演练读完转化弹窗后：真升级时会因为什么取消（点「转化获取」前的核对、转化弹窗的核对、「确认」是不是白字）；都通过返回空串"""
        reasons: list[str] = []
        if self.apply_precheck_why:
            reasons.append(self.apply_precheck_why)
        status, msg, _ = AgentSkillParse.check_convert(conv, self.reading, self.named, self._chip_expected(), self.step,
                                                       True, self.session.dennies_reserve)
        if status != SkillStepStatus.CONVERT:
            reasons.append(msg)
        found = screen_utils.find_area(ctx=self.ctx, screen=self.last_screenshot, screen_name=self.SCREEN_CONVERT,
                                       area_name=self.AREA_CONV_OK)
        if found != FindAreaResultEnum.TRUE:
            reasons.append('转化弹窗的「确认」是灰的或读不出')
        return '；'.join(dict.fromkeys(reasons))

    def _read_convert(self) -> ConvertReading:
        """材料转化弹窗：说明句、两个数量（不过滤颜色，数字偏暗）、丁尼，档位按图标颜色"""
        def ocr(area_name: str) -> list:
            area = self.ctx.screen_loader.get_area(self.SCREEN_CONVERT, area_name)
            return self.ctx.ocr_service.get_ocr_result_list(self.last_screenshot, rect=area.rect,
                                                            color_range=area.color_range)

        def tier(area_name: str) -> str | None:
            r = self.ctx.screen_loader.get_area(self.SCREEN_CONVERT, area_name).rect
            return AgentSkillParse.tier_from_icon(self.last_screenshot[r.y1:r.y2, r.x1:r.x2])

        conv = AgentSkillParse.parse_convert(ocr(self.AREA_CONV_SENTENCE), ocr(self.AREA_CONV_SRC),
                                             ocr(self.AREA_CONV_DST), ocr(self.AREA_CONV_DENNY))
        conv.src_tier = tier(self.AREA_CONV_SRC_ICON)
        conv.dst_tier = tier(self.AREA_CONV_DST_ICON)
        return conv

    def _obtained_title_shown(self) -> bool:
        """这一帧上读得出「获得」的标题（弹窗半透明、还没认全时也算）：这时不能读、不能点底下的强化弹窗"""
        return screen_utils.find_area(ctx=self.ctx, screen=self.last_screenshot, screen_name=self.SCREEN_OBTAINED,
                                      area_name=self.AREA_OBTAINED_TITLE) == FindAreaResultEnum.TRUE

    def _obtained_shown(self) -> bool:
        """
        这一帧是「获得」弹窗，而且转化弹窗的标题和「取消」都不在
        （全局「获得」的「确认」区域和转化弹窗的「确认」位置重叠，绝不能点到第二次转化）
        """
        name = self.check_and_update_current_screen(self.last_screenshot, screen_name_list=[self.SCREEN_OBTAINED])
        if name != self.SCREEN_OBTAINED:
            return False
        return not self.convert_parts_shown(self.ctx, self.last_screenshot)

    def _obtained_button_shown(self) -> bool:
        """
        「获得」自己的白字「确认」（本应用的窄区域 x 880-1075、y 712-760，实测字在 (978, 730)）。
        转化弹窗「确认」的字在 (1129, 758)，不在这个区域里，点的是认出的字的中心，所以碰不到第二次转化
        """
        return screen_utils.find_area(ctx=self.ctx, screen=self.last_screenshot, screen_name=self.SCREEN_CONVERT,
                                      area_name=self.AREA_OBTAINED_BTN) == FindAreaResultEnum.TRUE

    @classmethod
    def convert_parts_shown(cls, ctx: ZContext, screen) -> bool:
        """
        这一帧上有材料转化弹窗的标题或「取消」（只认出一个时不算转化弹窗）。
        这时绝不能当作「获得」去点「确认」：两个「确认」位置重叠。应用关弹窗时也用这个判断
        """
        for area_name in (cls.AREA_CONV_TITLE, cls.AREA_CONV_CANCEL):
            found = screen_utils.find_area(ctx=ctx, screen=screen, screen_name=cls.SCREEN_CONVERT, area_name=area_name)
            if found == FindAreaResultEnum.TRUE:
                return True
        return False

    def _read_obtained(self) -> tuple[str | None, int | None]:
        """「获得」弹窗图标下的 (名字, 数量)"""
        area = self.ctx.screen_loader.get_area(self.SCREEN_CONVERT, self.AREA_OBTAINED_ITEM)
        return AgentSkillParse.parse_obtained(self.ctx.ocr_service.get_ocr_result_list(
            self.last_screenshot, rect=area.rect, color_range=area.color_range))

    def _note_obtained(self) -> None:
        """
        「获得」弹窗（这一帧已经认出来了）：第一次看到或上次没读全时，记下显示的名字和数量到 outcome.convert['obtained']，
        和转化计划对不上只记日志（这里只是提示，真正的核对是之后重新读强化弹窗）
        """
        conv = self.outcome.convert
        if conv is None:
            return
        got = conv.get('obtained')
        if got is not None and got.get('name') is not None and got.get('count') is not None:
            return
        name, num = self._read_obtained()
        shot = got.get('shot') if got is not None else None
        if got is None:
            p = self.session.shot(f"{self.step['agent']}_{self.sk}_{self.frm}_obtained", self.last_screenshot)
            shot = p.name if p else None
        conv['obtained'] = {'name': name, 'count': num, 'shot': shot}
        if (name, num) == (conv.get('to'), conv.get('made')):
            log.info(f'「获得」弹窗：{name} {num}')
        else:
            log.warning(f"「获得」弹窗显示 {name} {num}，转化的是 {conv.get('to')} {conv.get('made')}"
                        '（只当作提示，以重新读强化弹窗为准）')

    def _ack_obtained(self, last: bool) -> OperationRoundResult:
        """
        确认转化后的「获得」弹窗（这一帧已经认出来了）：记下显示的内容（_note_obtained），然后只点这个弹窗自己的白字「确认」，再等强化弹窗。
        最多点 OBTAINED_MAX_CLICKS 次；点够了还在、或者已经是最后一轮，就截图停下（不点）；
        「确认」还没变成白字（动画中）这一轮不点也不算次数
        """
        if not self.session.apply or not self.conversion_clicked:  # 演练不会确认转化，到不了这里；再拦一次
            return self._fail(SkillStepStatus.CONVERT_GUARD, '没有确认转化，不能点「获得」的确认', 'obtained_guard')
        self._note_obtained()
        if self.obtained_clicks >= self.OBTAINED_MAX_CLICKS:
            return self._fail(SkillStepStatus.CONVERT_FAILED,
                              f'确认转化后「获得」弹窗一直没关（点了 {self.obtained_clicks} 次「确认」）',
                              'after_convert_obtained_stays')
        if last:
            msg = (f'确认转化后「获得」弹窗一直没关（点了 {self.obtained_clicks} 次「确认」）' if self.obtained_clicks
                   else '确认转化后最后一轮才出现「获得」弹窗，没有点')
            return self._fail(SkillStepStatus.CONVERT_FAILED, msg, 'after_convert_obtained_stays')
        if not self._obtained_button_shown():
            return self.round_retry('「获得」弹窗的「确认」还不是白字，等一下', wait=1)
        focus_game(self.ctx)
        self.obtained_clicks += 1  # 点之前就记下：点击出异常也算一次
        result = self.round_by_find_and_click_area(self.last_screenshot, self.SCREEN_CONVERT, self.AREA_OBTAINED_BTN,
                                                   pre_delay=0)
        if not result.is_success:
            return self.round_retry(f'点「获得」的「确认」失败（{result.status}）', wait=1)
        return self.round_retry('已点「获得」的「确认」，等强化弹窗', wait=1.5)

    def _convert_end(self, status: str, message: str) -> None:
        """转化这条路的结束决定（取消 / 演练 / 转化不了）：回到强化弹窗后由「回技能页」关弹窗"""
        self.decision = status
        self.outcome.status, self.outcome.message = status, message
        if self.outcome.convert is not None and status == SkillStepStatus.CONVERT_SKIPPED:
            self.outcome.convert.setdefault('cancel_reason', message)
        log.info(f'{self.sk} {self.frm}→{self.frm + 1}：{status} {message}')

    def _finish(self, status: str, message: str) -> OperationRoundResult:
        self.outcome.status, self.outcome.message = status, message
        log.info(f'{self.sk} {self.frm}→{self.to}：{status} {message}')
        return self.round_success(status)

    def _fail(self, status: str, message: str, shot_label: str) -> OperationRoundResult:
        path = self.session.shot(shot_label, self.last_screenshot)
        self.outcome.status = status
        self.outcome.message = f"{message}，截图 {path.name if path else '无'}"
        log.error(f'{status}：{self.outcome.message}')
        return self.round_fail(status, data=self.outcome)

    def _retry_or_fail(self, status: str, message: str, shot_label: str) -> OperationRoundResult:
        """还有重试次数就等一下再读（画面可能还在动画），最后一次失败时截图并返回带状态的失败"""
        if self.node_retry_times >= self.node_max_retry_times:
            return self._fail(status, message, shot_label)
        return self.round_retry(message, wait=1)
