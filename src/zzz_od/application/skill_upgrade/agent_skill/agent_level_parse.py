from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from zzz_od.application.character_growth.planner_core import EXP_A, LV_BANDS, exp_between
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_parse import AgentSkillParse
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_session import SkillStepStatus

if TYPE_CHECKING:
    from cv2.typing import MatLike

    from one_dragon.base.geometry.rectangle import Rect
    from one_dragon.base.matcher.ocr.ocr_match_result import OcrMatchResult


@dataclass
class BasicReading:
    """代理人基础页的等级框：「等级52」「/60」「>>」或「60」「MAX」"""

    level: int | None = None
    cap: int | None = None
    maxed: bool = False
    raw: str = ''


@dataclass
class LevelDialogReading:
    """「代理人培训」弹窗：等级条「当前 / 经验 a/b / 加材料后能到的等级」，3 个经验材料「已加/拥有」，按钮行"""

    from_lv: int | None = None
    exp: tuple[int, int] | None = None
    target_lv: int | None = None
    slots: list[tuple[int, int]] = field(default_factory=list)  # (已加, 拥有)，从左到右 = 见习 / 正式 / 资深
    den_pair: tuple[int, int] | None = None  # 按钮行附近的「a/b」（丁尼？顺序没在游戏里确认过，见 den_have_cost）
    extra: list[str] = field(default_factory=list)  # 按钮行附近其他看不懂的数字

    @property
    def ok(self) -> bool:
        return (self.exp is not None and self.from_lv is not None and self.target_lv is not None
                and 1 <= self.from_lv <= self.target_lv <= 60 and len(self.slots) == len(AgentLevelParse.EXP_ITEMS)
                and all(0 <= a <= o for a, o in self.slots))

    @property
    def added(self) -> list[int]:
        return [a for a, _ in self.slots]

    @property
    def owned(self) -> list[int]:
        return [o for _, o in self.slots]

    @property
    def exp_used(self) -> int:
        """自动添加选中的材料折成的经验"""
        return sum(a * AgentLevelParse.EXP_EACH[i] for i, (a, _) in enumerate(self.slots[:3]))

    @property
    def exp_owned(self) -> int:
        return sum(o * AgentLevelParse.EXP_EACH[i] for i, (_, o) in enumerate(self.slots[:3]))


@dataclass
class LevelDecision:
    status: str
    message: str
    partial: bool = False
    exp_used: int = 0
    exp_budget: int = 0
    den_short: bool = False  # SHORT 是因为丁尼不够（不是经验）：刷取队列记丁尼，不记调查员记录
    den_cost: int = 0


class AgentLevelParse:
    """
    升级代理人等级用到的纯计算：基础页等级框、「代理人培训」弹窗的解析，自动添加后怎么做。
    不碰游戏和上下文，单测可以直接调用。
    """

    # 弹窗里 3 个经验材料从左到右（绿 / 蓝 / 粉）= 见习 / 正式 / 资深调查员记录，每个 100 / 600 / 3000 经验
    EXP_ITEMS: ClassVar[list[str]] = list(EXP_A)
    EXP_EACH: ClassVar[list[int]] = [EXP_A[n] for n in EXP_A]
    BIG_ITEM: ClassVar[str] = max(EXP_A, key=lambda n: EXP_A[n])
    # 「>>」按钮的绿色箭头（RGB，和截图一致）：诺姆 52/60 有 334 个像素，满级的简、耀嘉音都是 0
    CHEVRON_RANGE: ClassVar[tuple[tuple[int, int, int], tuple[int, int, int]]] = ((110, 150, 0), (255, 255, 110))
    CHEVRON_MIN: ClassVar[int] = 150
    CHEVRON_MARGIN: ClassVar[int] = 5
    LEVEL_CAPS: ClassVar[tuple[int, ...]] = (10, 20, 30, 40, 50, 60)
    BASIC_LEVEL_RE: ClassVar[re.Pattern] = re.compile(r'等级(\d{1,2})(?:/(\d{1,2}))?')
    CAP_RE: ClassVar[re.Pattern] = re.compile(r'/?(\d{1,2})')
    ROW_BAND_PX: ClassVar[int] = 20  # 等级条上的数字和经验 a/b 的中心高度差
    MERGE_GAP_PX: ClassVar[int] = 15  # OCR 把「a/b」拆成两段时，两段之间最多隔这么多像素
    PAIR_RE: ClassVar[re.Pattern] = re.compile(r'\d+/\d+')
    SLOT_BELOW_PX: ClassVar[int] = 100  # 材料的「已加/拥有」比等级条低 100~270 像素（用户截图是 203）
    SLOT_BELOW_MAX_PX: ClassVar[int] = 270
    DEN_BELOW_PX: ClassVar[int] = 30  # 比材料行再低这么多的数字算按钮行
    BUDGET_SLACK: ClassVar[int] = 3000  # 预算多给一个资深调查员记录
    # 升级后可能出现的「点击空白处关闭」一类提示：整句完全符合才点（点提示文字本身）
    CLOSE_HINT_RE: ClassVar[re.Pattern] = re.compile(r'点击(空白|任意|屏幕)(处|位置|区域)?(即可)?(关闭|继续)')
    HINT_STRIP: ClassVar[str] = '~～。.!！…'
    BUTTON_TEXTS: ClassVar[tuple[str, ...]] = ('自动添加', '升级')
    # 「升级」相对「自动添加」的位置（用户截图：自动添加 (828,823)，升级 (1179,822)）
    UPGRADE_DX: ClassVar[tuple[int, int]] = (250, 450)
    UPGRADE_DY_MAX: ClassVar[int] = 20
    BUTTON_W_MAX: ClassVar[int] = 150

    norm = staticmethod(AgentSkillParse.norm)
    pair = staticmethod(AgentSkillParse.pair)

    # ---------- 基础页 ----------
    @classmethod
    def _join(cls, toks: list[OcrMatchResult]) -> str:
        return cls.norm(''.join(t.data or '' for t in sorted(toks, key=lambda t: t.rect.x1)))

    @classmethod
    def parse_basic(cls, level_toks: list[OcrMatchResult], cap_toks: list[OcrMatchResult],
                    max_toks: list[OcrMatchResult]) -> BasicReading:
        """
        「等级52」（白字，OCR 拆成两段也拼起来）、「60」或「/60」（上限）、「MAX」。
        等级不在 1..60、上限不是 10 的整数倍、等级比上限大时 level=None（当作读不出）
        """
        lv_text = cls._join(level_toks)
        cap_text = cls._join(cap_toks)
        r = BasicReading(raw=f'{lv_text}|{cap_text}|{cls._join(max_toks)}')
        r.maxed = any(cls.norm(t.data).upper() == 'MAX' for t in max_toks)
        m = cls.BASIC_LEVEL_RE.fullmatch(lv_text)
        level = int(m.group(1)) if m else None
        cap = int(m.group(2)) if m and m.group(2) else None
        if cap is None:
            mc = cls.CAP_RE.fullmatch(cap_text)
            cap = int(mc.group(1)) if mc else None
        r.cap = cap
        if level is not None and 1 <= level <= 60 and cap in cls.LEVEL_CAPS and level <= cap:
            r.level = level
        return r

    @classmethod
    def chevron_rect(cls, rect: Rect) -> tuple[int, int, int, int]:
        m = cls.CHEVRON_MARGIN
        return max(0, rect.x1 - m), max(0, rect.y1 - m), rect.x2 + m, rect.y2 + m

    @classmethod
    def chevron_pixels(cls, image: MatLike, rect: Rect) -> int:
        """「>>」按钮绿色箭头的像素数（image 是 RGB 截图，和 ctx 的截图一样）"""
        import cv2
        import numpy as np
        x1, y1, x2, y2 = cls.chevron_rect(rect)
        part = image[y1:y2, x1:x2, :3]
        lo, hi = cls.CHEVRON_RANGE
        return int(cv2.countNonZero(cv2.inRange(part, np.array(lo, dtype=np.uint8), np.array(hi, dtype=np.uint8))))

    @classmethod
    def classify_basic(cls, r: BasicReading, need: int, chevrons: int) -> tuple[str, str]:
        """
        基础页读完后怎么做：读不出 → 已经够 → 满级 → 到上限要突破 → 要的等级超过上限 → 「>>」不在 → 打开培训
        :return: (状态, 说明)；可以打开培训时状态是 OPEN
        """
        S = SkillStepStatus
        if r.level is None:
            return S.LEVELS_UNREADABLE, f'基础页的等级读不出（{r.raw}）'
        if r.level >= need:
            return S.ALREADY, f'代理人已经 {r.level} 级（要 {need} 级）'
        if r.maxed:
            return S.LEVEL_MAXED, f'代理人已满级（{r.level}）'
        if r.level >= r.cap:
            return S.PROMOTE, f'代理人 {r.level}/{r.cap} 已到上限，要先突破（自动突破还没做）'
        if need > r.cap:
            return S.PROMOTE, f'代理人 {r.level}/{r.cap}，要 {need} 级得先突破（自动突破还没做）'
        if chevrons < cls.CHEVRON_MIN:
            return S.PROMOTE, f'代理人 {r.level}/{r.cap}，但「>>」不在（绿色像素 {chevrons}），可能要突破，没有点'
        return cls.OPEN, f'代理人 {r.level}/{r.cap}'

    OPEN: ClassVar[str] = '打开培训'

    # ---------- 培训弹窗 ----------
    @staticmethod
    def _cy(t: OcrMatchResult) -> float:
        return (t.rect.y1 + t.rect.y2) / 2

    @classmethod
    def merge_split_pairs(cls, toks: list[OcrMatchResult]) -> list[OcrMatchResult]:
        """
        OCR 有时把「11700/39000」拆成「11700」「/39000」或「11700/」「39000」（材料的「0/180」也一样）：
        同一行（中心高度差 ≤ ROW_BAND_PX）、左右挨着（间隔 ≤ MERGE_GAP_PX）、一段以「/」结尾或开头、
        拼起来正好是「数字/数字」的两段拼回一个。每次拼间隔最小的一对，直到没有可拼的
        """
        from one_dragon.base.matcher.ocr.ocr_match_result import OcrMatchResult as Ocr
        out = list(toks)
        while True:
            best = None
            for a in out:
                sa = cls.norm(a.data)
                for b in out:
                    if a is b or b.rect.x1 < a.rect.x1:
                        continue
                    sb = cls.norm(b.data)
                    if sa.endswith('/') == sb.startswith('/'):  # 正好一段带着拆开处的「/」
                        continue
                    if not cls.PAIR_RE.fullmatch(sa + sb) or abs(cls._cy(a) - cls._cy(b)) > cls.ROW_BAND_PX:
                        continue
                    gap = b.rect.x1 - a.rect.x2
                    if abs(gap) <= cls.MERGE_GAP_PX and (best is None or abs(gap) < best[0]):
                        best = (abs(gap), a, b, sa + sb)
            if best is None:
                return out
            _, a, b, s = best
            x1, y1 = min(a.rect.x1, b.rect.x1), min(a.rect.y1, b.rect.y1)
            x2, y2 = max(a.rect.x2, b.rect.x2), max(a.rect.y2, b.rect.y2)
            merged = Ocr(min(a.confidence, b.confidence), x1, y1, x2 - x1, y2 - y1, data=s)
            out = [t for t in out if t is not a and t is not b] + [merged]

    @classmethod
    def parse_level_dialog(cls, content: list[OcrMatchResult], den: list[OcrMatchResult]) -> LevelDialogReading:
        """
        内容区按一整块读，用位置区分（弹窗位置没在游戏里核对过，规则对 ±80 像素的偏移都成立）：
        最上面的「a/b」是经验；和它同一高度的纯数字，左边的是当前等级、右边的是加材料后能到的等级；
        比经验低 100~270 像素的「a/b」是 3 个材料的「已加/拥有」；再往下（含按钮行区域）的数字是丁尼之类，
        只记录。属性值（9981 等）不在等级条那一行，「!」图标读成的「0」不到 3 位，都不算
        """
        r = LevelDialogReading()
        content = cls.merge_split_pairs(content)
        den = cls.merge_split_pairs(den)
        items = [(cls.norm(t.data), t) for t in content]
        pairs = [(s, t, cls.pair(s)) for s, t in items if '/' in s and cls.pair(s) is not None]
        if not pairs:
            return r
        es, et, ep = min(pairs, key=lambda x: x[1].rect.y1)
        r.exp = ep
        ecy = cls._cy(et)
        row = [t for s, t in items if s.isdigit() and abs(cls._cy(t) - ecy) <= cls.ROW_BAND_PX]
        left = [t for t in row if t.rect.x2 <= et.rect.x1]
        right = [t for t in row if t.rect.x1 >= et.rect.x2]
        if len(left) == 1:
            r.from_lv = int(cls.norm(left[0].data))
        if len(right) == 1:
            r.target_lv = int(cls.norm(right[0].data))
        slot_toks = sorted([(t, p) for s, t, p in pairs if cls.SLOT_BELOW_PX <= cls._cy(t) - ecy <= cls.SLOT_BELOW_MAX_PX],
                           key=lambda x: x[0].rect.x1)
        r.slots = [p for _, p in slot_toks]
        below = max((cls._cy(t) for t, _ in slot_toks), default=ecy + cls.SLOT_BELOW_PX) + cls.DEN_BELOW_PX
        cands = [t for t in content if cls._cy(t) > below and t not in [x for x, _ in slot_toks]] + list(den)
        den_pairs: list[tuple[int, int]] = []
        for t in cands:
            s = cls.norm(t.data)
            if not any(c.isdigit() for c in s):
                continue
            p = cls.pair(s) if '/' in s else None
            if p is not None:
                den_pairs.append(p)
            elif sum(c.isdigit() for c in s) >= 3:
                r.extra.append(s)
        if len(den_pairs) == 1:
            r.den_pair = den_pairs[0]
        else:
            r.extra += [f'{a}/{b}' for a, b in den_pairs]
        return r

    @classmethod
    def same_reading(cls, a: LevelDialogReading | None, b: LevelDialogReading | None) -> bool:
        """点「升级」前在新截图上重新读，每个字段都要一样"""
        if a is None or b is None:
            return False
        return (a.from_lv, a.exp, a.target_lv, a.slots, a.den_pair, a.extra) == \
            (b.from_lv, b.exp, b.target_lv, b.slots, b.den_pair, b.extra)

    @classmethod
    def check_opened(cls, r0: LevelDialogReading, basic_level: int) -> str | None:
        """刚打开（还没加材料）的弹窗：读全、等级和基础页一样、经验 a<b、什么都没加。返回问题或 None"""
        if not r0.ok:
            return f'弹窗读不全（{cls.describe(r0)}）'
        if r0.from_lv != basic_level:
            return f'弹窗等级 {r0.from_lv}，基础页是 {basic_level}'
        if not r0.exp[0] < r0.exp[1]:
            return f'经验 {r0.exp[0]}/{r0.exp[1]} 不对'
        if r0.target_lv != r0.from_lv:
            return f'还没加材料就显示能到 {r0.target_lv} 级'
        return None

    @classmethod
    def check_added(cls, r0: LevelDialogReading, r1: LevelDialogReading, cap: int | None) -> str | None:
        """
        自动添加后的弹窗：等级和拥有数量跟加之前一样（经验 a/b 可能变成预览，不要求一样）、目标不超过上限，
        并且「能升级」和「当前经验 + 加的经验 ≥ 下一级」一致（材料顺序或数字读错时对不上）。返回问题或 None
        """
        if not r1.ok:
            return f'自动添加后弹窗读不全（{cls.describe(r1)}）'
        if r1.from_lv != r0.from_lv or r1.owned != r0.owned:
            return f'自动添加后等级或拥有数量变了（加之前 {cls.describe(r0)}，之后 {cls.describe(r1)}）'
        if cap is not None and r1.target_lv > cap:
            return f'目标 {r1.target_lv} 级超过上限 {cap}'
        a, b = r0.exp
        if (r1.target_lv > r1.from_lv) != (a + r1.exp_used >= b):
            return f'目标 {r1.target_lv} 级和经验对不上（{a}+{r1.exp_used:,} 对 {b}）'
        return None

    @classmethod
    def exp_need_to(cls, r0: LevelDialogReading, to: int) -> int:
        """从弹窗的当前经验升到 to 级大约要多少经验（这一级按弹窗，后面按 LV_BANDS 估算）"""
        if r0.exp is None or r0.from_lv is None or to <= r0.from_lv:
            return 0
        a, b = r0.exp
        return int(round(max(0, b - a) + exp_between(LV_BANDS, r0.from_lv + 1, to)))

    @classmethod
    def exp_budget(cls, r0: LevelDialogReading, need: int, cap: int | None, step_exp: int) -> int:
        to = min(need, cap) if cap else need
        return max(int(step_exp or 0), cls.exp_need_to(r0, to)) + cls.BUDGET_SLACK

    @classmethod
    def exp_shortage(cls, r0: LevelDialogReading, owned_exp: int, need: int, cap: int | None) -> int:
        """升到 min(need, 上限) 还差的经验（至少一个资深调查员记录）"""
        to = min(need, cap) if cap else need
        return max(cls.BUDGET_SLACK, cls.exp_need_to(r0, to) - owned_exp)

    @classmethod
    def shortage_needs(cls, r0: LevelDialogReading, r1: LevelDialogReading, need: int, cap: int | None,
                       applied: bool) -> list[tuple[str, int]]:
        """
        写刷取队列：只用资深调查员记录表示（每日推送把它换算成调查专项）。数量是「总共要有」的数，
        因为推送把队列和背包里的数量比较：= 现在剩的资深 + 还差的经验 / 3000
        """
        short = cls.exp_shortage(r0, r0.exp_owned, need, cap)
        i = cls.EXP_ITEMS.index(cls.BIG_ITEM)
        added, owned = r1.slots[i] if len(r1.slots) > i else (0, 0)
        left = owned - (added if applied else 0)
        return [(cls.BIG_ITEM, int(left + math.ceil(short / EXP_A[cls.BIG_ITEM])))]

    @staticmethod
    def den_have_cost(pair: tuple[int, int] | None, known: int | None) -> tuple[int, int] | None:
        """
        培训弹窗按钮行的「a/b」哪个是现有丁尼：格式没在游戏里确认过（材料是「已加/拥有」，技能弹窗是「有/需」），
        只有其中一个正好等于技能弹窗里读到的现有丁尼时才认。返回 (现有, 花费)，分不出时 None
        """
        if pair is None or known is None:
            return None
        a, b = pair
        if a == known:
            return a, b
        if b == known:
            return b, a
        return None

    @classmethod
    def decide(cls, r0: LevelDialogReading, r1: LevelDialogReading, basic: BasicReading, need: int,
               goal_lv: int | None, step_exp: int, apply: bool, reserve: int,
               allow_overshoot: bool = False, known_dennies: int | None = None) -> LevelDecision:
        """
        自动添加后怎么做，按顺序：读不对 → （真升级）有看不懂的数字 → 什么都加不上 / 升不了一级 →
        丁尼（如果有）分不出现有和花费（真升级不点）/ 不够 / 到保留线 → 超过培养目标 → 用的经验超过预算 → 演练 / 点升级。
        超过目标、超过预算演练时也一样报（让第一次演练就看到自动添加加了多少）
        :param known_dennies: 这次运行里技能强化弹窗读到的现有丁尼（None = 还没读过）
        """
        S = SkillStepStatus
        cap = basic.cap
        if not r1.ok or r1.from_lv != basic.level or r0.exp is None:
            return LevelDecision(S.DIALOG_WRONG, f'培训弹窗读不对（{cls.describe(r1)}）')
        used = r1.exp_used
        budget = cls.exp_budget(r0, need, cap, step_exp)
        txt = cls.describe(r1)
        note = ''
        if r1.extra:
            if apply:
                return LevelDecision(S.DIALOG_UNKNOWN, f'按钮附近有看不懂的数字 {r1.extra}，没有点升级（{txt}）',
                                     exp_used=used, exp_budget=budget)
            note = f'；按钮附近有看不懂的数字 {r1.extra}'
        if used == 0 or r1.target_lv == r1.from_lv:
            return LevelDecision(S.SHORT, f'经验材料不够升 1 级（{txt}）{note}', exp_used=used, exp_budget=budget)
        if r1.den_pair is not None:
            hc = cls.den_have_cost(r1.den_pair, known_dennies)
            a, b = r1.den_pair
            if hc is None:
                why = f'按钮行 {a}/{b} 分不出哪个是现有丁尼（技能弹窗读到的现有丁尼：{known_dennies}）'
                if apply:
                    return LevelDecision(S.DIALOG_UNKNOWN, f'{why}，没有点升级（{txt}）', exp_used=used, exp_budget=budget)
                note += f'；{why}'
            else:
                have, cost = hc
                if have < cost:
                    return LevelDecision(S.SHORT, f'丁尼不够：有 {have}，要 {cost}（{txt}）', exp_used=used,
                                         exp_budget=budget, den_short=True, den_cost=cost)
                if reserve and have - cost < reserve:
                    return LevelDecision(S.RESERVE, f'丁尼 {have} 花 {cost} 后低于保留 {reserve}，停止升级',
                                         exp_used=used, exp_budget=budget)
        top = min(goal_lv or 60, cap or 60)
        if r1.target_lv > top:
            return LevelDecision(S.TARGET_OVER, f'自动添加会升到 {r1.target_lv} 级，超过培养目标 {top} 级，没有点（{txt}）',
                                 exp_used=used, exp_budget=budget)
        if not allow_overshoot and used > budget:
            return LevelDecision(S.OVER_BUDGET,
                                 f'自动添加会用 {used:,} 经验升到 {r1.target_lv} 级，这一步只要到 {need} 级'
                                 f'（预算 {budget:,}），没有点（{txt}）{note}', exp_used=used, exp_budget=budget)
        partial = r1.target_lv < need
        part = f'（只到 {r1.target_lv} 级，要 {need} 级）' if partial else ''
        if not apply:
            return LevelDecision(S.REHEARSED, f'演练：会用 {used:,} 经验升到 {r1.target_lv} 级{part}（{txt}）{note}',
                                 partial, used, budget)
        return LevelDecision(S.CONFIRM, f'用 {used:,} 经验升到 {r1.target_lv} 级{part}（{txt}）', partial, used, budget)

    @classmethod
    def describe(cls, r: LevelDialogReading | None) -> str:
        if r is None:
            return '没读'
        exp = f'{r.exp[0]}/{r.exp[1]}' if r.exp else '?'
        slots = ' '.join(f'{n[:2]} {a}/{o}' for n, (a, o) in zip(cls.EXP_ITEMS, r.slots)) or '材料没读到'
        if len(r.slots) != len(cls.EXP_ITEMS):
            slots += f'（读到 {len(r.slots)} 个）'
        den = f'，按钮行 {r.den_pair[0]}/{r.den_pair[1]}' if r.den_pair else ''
        return f'{r.from_lv}→{r.target_lv}，经验 {exp}，已加/拥有 {slots}{den}'

    # ---------- 按钮 ----------
    @classmethod
    def exact_button(cls, toks: list[OcrMatchResult], text: str) -> OcrMatchResult | None:
        """区域里正好一个完全等于 text 的 OCR 结果（「升级」用 lcs 匹配时「可升级」「升级材料」也会算，这里不算）"""
        hits = [t for t in toks if cls.norm(t.data) == text]
        return hits[0] if len(hits) == 1 else None

    @classmethod
    def upgrade_button_ok(cls, auto: OcrMatchResult | None, up: OcrMatchResult | None) -> str | None:
        """「升级」和同一张截图上的「自动添加」在同一行、相隔 250~450 像素、宽度正常。返回问题或 None"""
        if auto is None:
            return '同一张截图上找不到「自动添加」'
        if up is None:
            return '找不到白字的「升级」'
        dx = up.center.x - auto.center.x
        dy = abs(up.center.y - auto.center.y)
        if not (cls.UPGRADE_DX[0] <= dx <= cls.UPGRADE_DX[1]) or dy > cls.UPGRADE_DY_MAX:
            return f'「升级」位置不对（相对「自动添加」{dx},{dy}）'
        if up.rect.width >= cls.BUTTON_W_MAX:
            return f'「升级」文字太宽（{up.rect.width}）'
        return None

    @classmethod
    def close_hint(cls, toks: list[OcrMatchResult]) -> OcrMatchResult | None:
        """整句是「点击空白处关闭」一类的提示时返回它（点它本身）"""
        for t in toks:
            s = cls.norm(t.data).strip(cls.HINT_STRIP)
            if cls.CLOSE_HINT_RE.fullmatch(s):
                return t
        return None

