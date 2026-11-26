from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from one_dragon.base.geometry.point import Point
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils import cv2_utils
from one_dragon.utils.log_utils import log
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_data import CARD_TEMPLATE_SUB_DIR
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_parse import AgentSkillParse
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_session import AgentSkillSession, focus_game
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.zzz_operation import ZOperation

if TYPE_CHECKING:
    from one_dragon.base.screen.template_info import TemplateInfo


class SelectAgent(ZOperation):
    """
    在代理人列表里选中某个代理人（结束时停在列表，左下角是他的名字）。每一轮只做一个动作（点一张卡或滚一次），
    下一轮用新截图核对，所以随时可以停止。
    - 左下角已经是他：直接结束（名字完全一样才算）
    - 有头像模板（assets/template/agent_card/<id>，用 ctx.tm 在卡片区域里匹配）：每页找一次模板，点中后名字不对就当找不到
    - 没有模板：按卡片右下角的「等级NN」逐张点头像，知道代理人等级时先只点等级相同的卡，再全部点一遍；
      一页没有可点的卡时换一张新截图再读一次才算找完（一次 OCR 偶尔漏一个标签）
    - 当前页找完：先往上滚到顶，再一页页往下；点卡和翻页次数有上限，找不到返回 STATUS_AGENT_NOT_FOUND（还停在列表）
    - 点到已选中的卡会进详情页（代理人-基础）：点返回
    """

    STATUS_SELECTED: ClassVar[str] = '已选中代理人'
    STATUS_AGENT_NOT_FOUND: ClassVar[str] = '找不到这个代理人'
    STATUS_SCREEN_WRONG: ClassVar[str] = '画面不对'

    SCREEN_LIST: ClassVar[str] = '代理人-列表'
    SCREEN_DETAIL_TABS: ClassVar[tuple[str, ...]] = ('代理人-基础', '代理人-技能')
    AREA_NAME: ClassVar[str] = '区域-代理人名字'
    AREA_CARDS: ClassVar[str] = '区域-代理人卡片'
    AREA_SCROLL: ClassVar[str] = '区域-列表滚动'
    AREA_BACK: ClassVar[str] = '按钮-返回'

    MAX_CLICKS: ClassVar[int] = 30
    MAX_SCROLLS: ClassVar[int] = 8
    SCROLL_UP: ClassVar[int] = -9
    SCROLL_DOWN: ClassVar[int] = 3
    # 头像在「等级NN」标签的左上方；最上面只露出下半张的卡点标签上方一点（坐标由 OCR 结果算出）
    FACE_OFFSET: ClassVar[tuple[int, int]] = (-30, -100)
    FACE_OFFSET_TOP: ClassVar[tuple[int, int]] = (-30, -40)
    FACE_TOP_LIMIT: ClassVar[int] = 40
    MIN_Y: ClassVar[int] = 20
    NEAR_PX: ClassVar[int] = 60
    TEMPLATE_THRESHOLD: ClassVar[float] = 0.85
    MAX_BACKS: ClassVar[int] = 4
    MAX_BAD_FRAMES: ClassVar[int] = 3
    MAX_UNREADABLE: ClassVar[int] = 2
    PAGE_REREADS: ClassVar[int] = 1  # 一页没有可点的卡时，换新截图再读几次才算这一页找完

    def __init__(self, ctx: ZContext, session: AgentSkillSession, name_cn: str, agent_id: str,
                 agent_lv: int | None = None) -> None:
        ZOperation.__init__(self, ctx, op_name=f'选择代理人 {name_cn}', timeout_seconds=400)
        self.session: AgentSkillSession = session
        self.name_cn: str = name_cn
        self.agent_id: str = agent_id
        self.agent_lv: int | None = agent_lv
        self.template: TemplateInfo | None = None
        self._reset()

    def _reset(self) -> None:
        self.clicks: int = 0
        self.scrolls: int = 0
        self.backs: int = 0
        self.bad_frames: int = 0
        self.unreadable: int = 0
        self.phase: str = 'all'
        self.direction: str = 'up'
        self.page_clicked: list[Point] = []
        self.page_template_tried: bool = False
        self.page_empty_reads: int = 0
        self.visited: set[tuple] = set()
        self.just_scrolled: bool = False
        self.sig_before_scroll: tuple | None = None
        self.last_was_template: bool = False
        self.seen_names: list[str] = []
        self.message: str = ''

    def handle_init(self) -> None:
        self._reset()
        tpl = self.ctx.tm.template_loader.get_template(CARD_TEMPLATE_SUB_DIR, self.agent_id)
        self.template = tpl if tpl is not None and tpl.raw is not None else None
        self.phase = 'level' if self.template is None and self.agent_lv else 'all'

    @operation_node(name='查找代理人', is_start_node=True, node_max_retry_times=1)
    def find_agent(self) -> OperationRoundResult:
        name = self.check_and_update_current_screen(
            self.last_screenshot, screen_name_list=[self.SCREEN_LIST, *self.SCREEN_DETAIL_TABS])
        if name in self.SCREEN_DETAIL_TABS:  # 点到已选中的卡进了详情页
            if self.backs >= self.MAX_BACKS:
                return self._fail_screen(f'回不到代理人列表（{name}）')
            self.backs += 1
            focus_game(self.ctx)
            self.round_by_find_and_click_area(self.last_screenshot, name, self.AREA_BACK, pre_delay=0)
            return self.round_wait('点卡进了详情页，退回列表', wait=1.5)
        if name != self.SCREEN_LIST:
            self.bad_frames += 1
            if self.bad_frames > self.MAX_BAD_FRAMES:
                return self._fail_screen(f'不在代理人列表（{name}）')
            return self.round_wait('等代理人列表', wait=1)
        self.bad_frames = 0

        area = self.ctx.screen_loader.get_area(self.SCREEN_LIST, self.AREA_NAME)
        got = AgentSkillParse.selected_name(
            self.ctx.ocr_service.get_ocr_result_list(self.last_screenshot, rect=area.rect), area.rect)
        if got is None:
            self.unreadable += 1
            if self.unreadable > self.MAX_UNREADABLE:
                return self._fail_screen('读不出列表左下角的代理人名字')
            return self.round_wait('读不出名字', wait=1)
        self.unreadable = 0
        if got not in self.seen_names:
            self.seen_names.append(got)
        if AgentSkillParse.name_equal(got, self.name_cn):
            log.info(f'选中 {self.name_cn}（点了 {self.clicks} 张卡，翻了 {self.scrolls} 次）')
            return self.round_success(self.STATUS_SELECTED)
        if self.last_was_template:
            path = self.session.shot('card_name_mismatch', self.last_screenshot)
            return self._not_found(f'头像像 {self.name_cn}，点开后左下角是 {got}，截图 {path.name if path else "无"}')
        if self.clicks >= self.MAX_CLICKS:
            return self._not_found_with_shot()

        cards_area = self.ctx.screen_loader.get_area(self.SCREEN_LIST, self.AREA_CARDS)
        cards = AgentSkillParse.card_levels(
            self.ctx.ocr_service.get_ocr_result_list(self.last_screenshot, rect=cards_area.rect))
        sig = AgentSkillParse.card_signature(cards)
        if self.just_scrolled:
            self.just_scrolled = False
            moved = AgentSkillParse.signature_moved(self.sig_before_scroll, sig)
            if self.direction == 'up':
                if moved:
                    return self._scroll(sig)  # 继续往上滚到顶
                self.direction = 'down'  # 到顶了：找这一页，然后往下翻
            elif not moved:
                return self._pass_done()  # 到底了

        if sig not in self.visited:
            if self.template is not None:
                if not self.page_template_tried:
                    self.page_template_tried = True
                    hit = self._match_template(cards_area)
                    if hit is not None:
                        return self._click(hit, template=True)
            else:
                pos = self._next_card(cards)
                if pos is not None:
                    self.page_empty_reads = 0
                    return self._click(pos, template=False)
                if self.page_empty_reads < self.PAGE_REREADS:
                    self.page_empty_reads += 1
                    return self.round_wait('这一页的卡片标签再读一次', wait=0.5)
            self.page_empty_reads = 0
            self.visited.add(sig)
        return self._scroll(sig)

    # ---------- 内部 ----------
    def _match_template(self, cards_area) -> Point | None:
        crop = cv2_utils.crop_image_only(self.last_screenshot, cards_area.rect)
        mrl = self.ctx.tm.match_template(crop, CARD_TEMPLATE_SUB_DIR, self.agent_id,
                                         threshold=self.TEMPLATE_THRESHOLD, only_best=True)
        if mrl.max is None:
            return None
        log.info(f'头像模板匹配 {mrl.max.confidence:.2f}')
        return mrl.max.center + cards_area.left_top

    def _next_card(self, cards) -> Point | None:
        for lv, label in cards:
            if self.phase == 'level' and lv != self.agent_lv:
                continue
            dx, dy = self.FACE_OFFSET if label.y + self.FACE_OFFSET[1] >= self.FACE_TOP_LIMIT else self.FACE_OFFSET_TOP
            pos = Point(label.x + dx, max(self.MIN_Y, label.y + dy))
            if any(abs(pos.x - p.x) < self.NEAR_PX and abs(pos.y - p.y) < self.NEAR_PX for p in self.page_clicked):
                continue
            return pos
        return None

    def _click(self, pos: Point, template: bool) -> OperationRoundResult:
        focus_game(self.ctx)
        self.ctx.controller.click(pos)
        self.clicks += 1
        self.page_clicked.append(pos)
        self.last_was_template = template
        log.info(f"第 {self.clicks} 次点卡（{'头像模板' if template else '等级标签'}）@{pos.x},{pos.y}")
        return self.round_wait('点了卡片', wait=1.5)

    def _scroll(self, sig: tuple) -> OperationRoundResult:
        if self.scrolls >= self.MAX_SCROLLS:
            return self._pass_done()
        area = self.ctx.screen_loader.get_area(self.SCREEN_LIST, self.AREA_SCROLL)
        focus_game(self.ctx)
        self.ctx.controller.scroll(self.SCROLL_UP if self.direction == 'up' else self.SCROLL_DOWN, area.center)
        self.scrolls += 1
        self.just_scrolled = True
        self.sig_before_scroll = sig
        self.page_clicked = []
        self.page_template_tried = False
        self.page_empty_reads = 0
        return self.round_wait(f"代理人列表往{'上' if self.direction == 'up' else '下'}滚", wait=1)

    def _pass_done(self) -> OperationRoundResult:
        if self.phase == 'level' and self.clicks < self.MAX_CLICKS:
            log.info(f'没找到等级 {self.agent_lv} 的 {self.name_cn}，不按等级再找一遍')
            self.phase = 'all'
            self.scrolls = 0
            self.visited = set()
            self.direction = 'up'
            self.page_clicked = []
            self.page_empty_reads = 0
            self.just_scrolled = False
            return self.round_wait('不按等级再找一遍')
        return self._not_found_with_shot()

    def _not_found_with_shot(self) -> OperationRoundResult:
        path = self.session.shot('agent_not_found', self.last_screenshot)
        return self._not_found(f"代理人列表里没找到 {self.name_cn}（点了 {self.clicks} 张卡，翻了 {self.scrolls} 次，"
                               f"看到 {'、'.join(sorted(self.seen_names)) or '无'}），截图 {path.name if path else '无'}")

    def _not_found(self, message: str) -> OperationRoundResult:
        self.message = message
        log.info(message)
        return self.round_success(self.STATUS_AGENT_NOT_FOUND, data=message)

    def _fail_screen(self, message: str) -> OperationRoundResult:
        path = self.session.shot('select_agent_screen_wrong', self.last_screenshot)
        self.message = f"{message}，截图 {path.name if path else '无'}"
        log.error(self.message)
        return self.round_fail(self.STATUS_SCREEN_WRONG, data=self.message)
