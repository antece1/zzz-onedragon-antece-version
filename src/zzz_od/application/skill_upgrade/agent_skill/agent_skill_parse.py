from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from zzz_od.application.skill_upgrade.agent_skill.agent_skill_data import (
    CAGE,
    CONVERT_DENNY_MAX_PER_CHIP,
    CONVERT_DENNY_PER_CHIP,
    CONVERT_RATIO,
    DENNY,
    clean_name,
    skill_item_name,
)
from zzz_od.application.skill_upgrade.agent_skill.agent_skill_session import SkillStepStatus

if TYPE_CHECKING:
    from cv2.typing import MatLike

    from one_dragon.base.geometry.point import Point
    from one_dragon.base.geometry.rectangle import Rect
    from one_dragon.base.matcher.ocr.ocr_match_result import OcrMatchResult


@dataclass
class DialogReading:
    """技能强化弹窗里读到的数字"""

    from_lv: int | None = None
    to_lv: int | None = None
    mats: list[tuple[int, int]] = field(default_factory=list)  # 材料 (有, 需)，从左到右
    need_level: int | None = None  # 「代理人等级不足N」里的 N
    den_have: int | None = None
    den_need: int | None = None

    @property
    def den_pair(self) -> tuple[int, int] | None:
        if self.den_have is None or self.den_need is None:
            return None
        return self.den_have, self.den_need


@dataclass
class ConvertReading:
    """材料转化弹窗里读到的：「仍需N个<芯片>」、左右两个图标下的数量和档位、「消耗：有 / 花」的丁尼"""

    missing: int | None = None
    chip: str | None = None  # 要转化出来的芯片（说明句里的名字）
    src_count: int | None = None  # 左边（低一档）图标下的数字
    dst_count: int | None = None  # 右边（要的那档）图标下的数字
    den_have: int | None = None
    den_cost: int | None = None
    src_tier: str | None = None  # 'C' / 'B' / 'A'，按图标底部色条认；认不出是 None
    dst_tier: str | None = None

    def key(self) -> tuple:
        """确认前在新截图上重新读一次，要和这个完全一样"""
        return (self.missing, self.chip, self.src_count, self.dst_count, self.den_have, self.den_cost,
                self.src_tier, self.dst_tier)

    def complete(self) -> bool:
        return all(v is not None for v in self.key())

    def as_dict(self) -> dict:
        return {'missing': self.missing, 'chip': self.chip, 'src_count': self.src_count, 'dst_count': self.dst_count,
                'den_have': self.den_have, 'den_cost': self.den_cost, 'src_tier': self.src_tier, 'dst_tier': self.dst_tier}


class AgentSkillParse:
    """
    角色技能提升用到的纯计算：把区域 OCR 结果解析成名字、等级、花费，判断这一级该怎么做。
    不碰游戏和上下文，单测可以直接调用。
    """

    # OCR 偶尔把简体认成繁体（实测「技能强化」→「技能強化」），统一成简体再比较
    T2S: ClassVar[dict[int, str]] = str.maketrans('強確級與擊認證產備裝鍵選擇數據攻擊轉個進階礎費換', '强确级与击认证产备装键选择数据攻击转个进阶础费换')
    CJK: ClassVar[re.Pattern] = re.compile(r'[一-鿿]')
    LEVEL_RE: ClassVar[re.Pattern] = re.compile(r'等级\s*(\d{1,2})')
    LEVEL_FULL_RE: ClassVar[re.Pattern] = re.compile(r'等级(\d{1,2})')
    NEED_LEVEL_RE: ClassVar[re.Pattern] = re.compile(r'代理人等级不足\s*(\d+)')
    NAME_LEFT_SLACK: ClassVar[int] = 50  # 名字总是从名字区域左边开始（x≈200），更靠右的汉字不是名字
    NAME_EDGE_PX: ClassVar[int] = 6  # 名字贴着区域右边：可能被截断，按读不出处理（截断的名字可能正好是别的代理人）
    NAME_IGNORE: ClassVar[tuple[str, ...]] = ('影画',)

    # ---------- 通用 ----------
    @classmethod
    def norm(cls, text: str | None) -> str:
        return (text or '').translate(cls.T2S).replace(' ', '')

    @staticmethod
    def pair(text: str) -> tuple[int, int] | None:
        """「有/需」形式的数字，如 640959/9000"""
        s = AgentSkillParse.norm(text)
        if '/' not in s:
            return None
        a, _, b = s.partition('/')
        a = ''.join(c for c in a if c.isdigit())
        b = ''.join(c for c in b if c.isdigit())
        if not a or not b:
            return None
        return int(a), int(b)

    # ---------- 代理人列表 ----------
    @classmethod
    def selected_name(cls, results: list[OcrMatchResult], rect: Rect) -> str | None:
        """
        列表左下角选中代理人的名字。取最左边那条含汉字的 OCR 结果，去掉符号；
        没读到、位置不对或贴着区域边缘（可能截断）时返回 None（当作读不出，绝不当作相同）
        """
        toks = [r for r in results if cls.CJK.search(cls.norm(r.data))
                and not any(w in r.data for w in cls.NAME_IGNORE)]
        if not toks:
            return None
        left = min(toks, key=lambda r: r.rect.x1)
        if left.rect.x1 > rect.x1 + cls.NAME_LEFT_SLACK:
            return None
        if left.rect.x2 >= rect.x2 - cls.NAME_EDGE_PX or left.rect.x1 <= rect.x1 + 1:
            return None
        return clean_name(cls.norm(left.data)) or None

    @classmethod
    def name_equal(cls, ocr_name: str | None, name_cn: str | None) -> bool:
        """名字完全一样才算（只比汉字、字母、数字）；不用包含关系，免得「零号·安比」被当成「安比」"""
        a = clean_name(cls.norm(ocr_name))
        b = clean_name(cls.norm(name_cn))
        return a != '' and a == b

    @classmethod
    def card_levels(cls, results: list[OcrMatchResult]) -> list[tuple[int, Point]]:
        """列表里每张卡右下角的「等级NN」：(等级, 标签中心)，按 OCR 顺序"""
        out: list[tuple[int, Point]] = []
        for r in results:
            m = cls.LEVEL_RE.search(cls.norm(r.data))
            if m is not None:
                out.append((int(m.group(1)), r.center))
        return out

    @staticmethod
    def card_signature(cards: list[tuple[int, Point]]) -> tuple:
        """列表画面的指纹：滚动后不变说明到头了（只用等级数字和粗略位置）"""
        return tuple(sorted((lv, p.x // 20, p.y // 20) for lv, p in cards))

    SIGNATURE_MOVED_MIN: ClassVar[int] = 3

    @classmethod
    def signature_moved(cls, before: tuple | None, after: tuple) -> bool:
        """
        滚动后列表动了没有：至少 3 个标签（等级+位置）不一样才算动了。
        一次 OCR 偶尔漏读或多读一个标签，不能因此当成「还没到头」多滚一次；真滚动时几乎所有标签都会变
        """
        if before is None:
            return True
        return len(set(before) ^ set(after)) >= cls.SIGNATURE_MOVED_MIN

    # ---------- 技能页 ----------
    SKILL_LEVEL_DIGITS: ClassVar[int] = 4
    SKILL_LEVEL_OVERLAP_PX: ClassVar[int] = 2

    @classmethod
    def skill_level(cls, results: list[OcrMatchResult]) -> tuple[int, int] | None:
        """
        技能图标下的「当前/上限」如 0512：返回 (5, 12)。
        只认刚好 4 位数字；OCR 把一串数字拆成横向重叠的两段时（重叠处的数字会被读两次，如 0112 → 01 + 112）返回 None，
        由调用方换一张新截图再读
        """
        toks = sorted((r for r in results if any(c.isdigit() for c in r.data)), key=lambda r: r.rect.x1)
        for a, b in zip(toks, toks[1:]):
            if b.rect.x1 < a.rect.x2 - cls.SKILL_LEVEL_OVERLAP_PX:
                return None
        digits = ''.join(c for r in toks for c in r.data if c.isdigit())
        if len(digits) != cls.SKILL_LEVEL_DIGITS:
            return None
        return int(digits[:2]), int(digits[2:])

    @classmethod
    def detail_level(cls, results: list[OcrMatchResult]) -> int | None:
        """技能详情页的「等级N」"""
        for r in results:
            m = cls.LEVEL_FULL_RE.fullmatch(cls.norm(r.data))
            if m is not None:
                return int(m.group(1))
        return None

    # ---------- 强化弹窗 ----------
    @classmethod
    def parse_dialog(cls, content: list[OcrMatchResult], denny: list[OcrMatchResult]) -> DialogReading:
        """
        弹窗内容区（等级 N→N+1、材料「有/需」、红字「代理人等级不足N」）和丁尼区（与「确定」同一行）。
        红字出现时上面几行整体上移约 52 像素，所以内容区按一整块读，用文字格式区分
        """
        reading = DialogReading()
        levels: list[tuple[int, int]] = []
        mats: list[tuple[int, int, int]] = []
        for r in content:
            s = cls.norm(r.data)
            m = cls.LEVEL_FULL_RE.fullmatch(s)
            if m is not None:
                levels.append((r.rect.x1, int(m.group(1))))
                continue
            m = cls.NEED_LEVEL_RE.search(s)
            if m is not None:
                reading.need_level = int(m.group(1))
                continue
            p = cls.pair(s)
            if p is not None:
                mats.append((r.rect.x1, p[0], p[1]))
        levels.sort()
        if levels:
            reading.from_lv = levels[0][1]
        if len(levels) >= 2:
            reading.to_lv = levels[1][1]
        reading.mats = [(h, n) for _, h, n in sorted(mats)]
        for r in sorted(denny, key=lambda r: r.rect.x1):
            p = cls.pair(r.data)
            if p is not None:
                reading.den_have, reading.den_need = p
                break
        return reading

    @staticmethod
    def name_materials(mats: list[tuple[int, int]], agent_id: str, base_from: int,
                       expected: dict[str, int] | None) -> list[tuple[str, int, int]]:
        """
        弹窗里材料只有图标没有名字：从 assets/agent_upgrade/agents/<id>.json 查这一级的芯片名，
        查不到用步骤预计花费里的材料名；11→12 时需要 1 个的是「仓鼠笼」访问器
        :param base_from: 去掉影画加成的基础等级
        :return: [(材料名, 有, 需)]
        """
        chip_name = (skill_item_name(agent_id, base_from)
                     or next((k for k in (expected or {}) if k not in (DENNY, CAGE)), None)
                     or '技能芯片（名字待查）')
        named: list[tuple[str, int, int]] = []
        for have, need in mats:
            name = CAGE if base_from == 11 and need == 1 and len(mats) > 1 else chip_name
            named.append((name, have, need))
        return named

    @staticmethod
    def cost_text(den_have: int, den_need: int, named: list[tuple[str, int, int]]) -> str:
        return f'丁尼 {den_need}（有 {den_have}），材料 ' + '，'.join(f'{name} {n}（有 {h}）' for name, h, n in named)

    @staticmethod
    def cost_matches(expected: dict[str, int] | None, den_need: int, named: list[tuple[str, int, int]]) -> bool:
        """
        真升级前核对弹窗花费和步骤预计的完全一样：没有预计花费、没有丁尼、丁尼不同、材料种数或数量不同都算不一样
        （OCR 少读一位数字时不能按错的数字判断保留线就点确定）
        """
        if not expected or DENNY not in expected:
            return False
        if int(expected[DENNY]) != den_need:
            return False
        exp_mats = sorted(int(v) for k, v in expected.items() if k != DENNY)
        got_mats = sorted(n for _, _, n in named)
        return exp_mats == got_mats

    @classmethod
    def decide(cls, reading: DialogReading, named: list[tuple[str, int, int]], step: dict,
               apply: bool, reserve: int) -> tuple[str, str]:
        """
        读完强化弹窗后决定这一级怎么做，按顺序：
        代理人等级不足 → 材料或丁尼不足 →（真升级）花费和预计不一样 → 丁尼到保留线 → 演练 → 点确定
        :return: (SkillStepStatus 里的状态, 说明)
        """
        sk = step['skill']
        frm = step.get('display_from', step['from'])
        den_have, den_need = reading.den_have, reading.den_need
        cost_txt = cls.cost_text(den_have, den_need, named)
        short = den_have < den_need or any(h < n for _, h, n in named)
        expected = step.get('cost') or {}
        if reading.need_level:
            also = '；材料也不够' if short else ''
            return SkillStepStatus.AGENT_LV_LOW, f'{sk} {frm}→{frm + 1} 要代理人 {reading.need_level} 级（{cost_txt}）{also}'
        if short:
            return SkillStepStatus.SHORT, f'材料或丁尼不足：{cost_txt}'
        if apply and not cls.cost_matches(expected, den_need, named):
            exp_txt = '，'.join(f'{k} {v}' for k, v in expected.items()) or '无'
            return SkillStepStatus.COST_MISMATCH, f'强化弹窗的花费和预计不一样（{cost_txt}；预计 {exp_txt}），没有点确定'
        den_cost = max(den_need, int(expected.get(DENNY, 0) or 0))
        if reserve and den_have - den_cost < reserve:
            return SkillStepStatus.RESERVE, f'丁尼 {den_have} 花 {den_cost} 后低于保留 {reserve}，停止升级'
        if not apply:
            return SkillStepStatus.REHEARSED, f'演练：会花 {cost_txt}'
        return SkillStepStatus.CONFIRM, cost_txt

    # ---------- 材料转化 ----------
    # 强化弹窗芯片不够时，游戏把「确定」换成「转化获取」（白字，同一位置）；点了出「材料转化」弹窗：
    # 「仍需6个特化引燃芯片，是否消耗其他材料进行转化？」，左边低一档芯片数量，右边转化出来的数量，「消耗：丁尼 有 / 花」
    CONVERT_RE: ClassVar[re.Pattern] = re.compile(r'仍需(\d+)个(.+?芯片)[，,]?是否消耗其他材料进行转化')
    TIER_PREFIX: ClassVar[dict[str, str]] = {'基础': 'C', '进阶': 'B', '特化': 'A'}
    TIER_ORDER: ClassVar[str] = 'CBA'
    # 图标底部色条的颜色（OpenCV HSV，H 0-180；截图是 RGB），实测 2026-11-25
    TIER_HSV: ClassVar[dict[str, tuple[int, int, int]]] = {'C': (81, 65, 168), 'B': (100, 255, 255), 'A': (147, 255, 255)}
    TIER_TOL: ClassVar[tuple[int, int, int]] = (6, 40, 40)
    # 只认色条：某档颜色在图标区域下半部分连续 8 行以上、每行都盖住至少 60% 的宽度才算这一档（实测色条 12 行、约 82% 宽）；
    # 图标本身的画（如特化电击芯片的蓝色）每行不到一半宽，不会算成色条
    TIER_BAR_MIN_COVER: ClassVar[float] = 0.6
    TIER_BAR_MIN_ROWS: ClassVar[int] = 8
    COUNT_MAX_DIGITS: ClassVar[int] = 5
    COUNT_EXTRA_CHARS: ClassVar[str] = 'xX×'

    @classmethod
    def tier_name(cls, element: str, tier: str) -> str:
        prefix = next(p for p, t in cls.TIER_PREFIX.items() if t == tier)
        return f'{prefix}{element}芯片'

    @classmethod
    def chip_parts(cls, name: str | None) -> tuple[str, str] | None:
        """「特化引燃芯片」→ ('引燃', 'A')；不是「<基础|进阶|特化><属性>芯片」的（如仓鼠笼）返回 None"""
        if not name or not name.endswith('芯片'):
            return None
        for prefix, tier in cls.TIER_PREFIX.items():
            if name.startswith(prefix):
                element = name[len(prefix):-2]
                return (element, tier) if element else None
        return None

    @classmethod
    def count(cls, results: list[OcrMatchResult]) -> int | None:
        """图标下的数量：从左到右拼数字；混进别的字（可能读到了别的东西）、没有数字或超过 5 位都返回 None"""
        digits = ''
        for r in sorted(results, key=lambda r: r.rect.x1):
            s = cls.norm(r.data)
            if any(not (c.isdigit() or c in cls.COUNT_EXTRA_CHARS) for c in s):
                return None
            digits += ''.join(c for c in s if c.isdigit())
        if not digits or len(digits) > cls.COUNT_MAX_DIGITS:
            return None
        return int(digits)

    @staticmethod
    def reading_order(results: list[OcrMatchResult]) -> list[OcrMatchResult]:
        """
        按行、行内从左到右排好：竖直中心落在某行第一个结果的上下范围内就算同一行（不用固定的 20 像素分段，
        一行拆成两段、上沿正好跨过分段线时不会把右半段排到前面）
        """
        lines: list[tuple[int, int, list[OcrMatchResult]]] = []
        for r in sorted(results, key=lambda r: (r.rect.y1 + r.rect.y2) / 2):
            cy = (r.rect.y1 + r.rect.y2) / 2
            line = next((ln for ln in lines if ln[0] <= cy <= ln[1]), None)
            if line is None:
                lines.append((r.rect.y1, r.rect.y2, [r]))
            else:
                line[2].append(r)
        return [r for _, _, toks in lines for r in sorted(toks, key=lambda r: r.rect.x1)]

    @classmethod
    def parse_convert(cls, sentence: list[OcrMatchResult], src: list[OcrMatchResult], dst: list[OcrMatchResult],
                      denny: list[OcrMatchResult]) -> ConvertReading:
        """材料转化弹窗的说明句、两个数量、丁尼（档位由操作按图标颜色另外填）"""
        reading = ConvertReading()
        text = ''.join(cls.norm(r.data) for r in cls.reading_order(sentence))
        m = cls.CONVERT_RE.search(text)
        if m is not None:
            reading.missing = int(m.group(1))
            reading.chip = m.group(2)
        reading.src_count = cls.count(src)
        reading.dst_count = cls.count(dst)
        for r in sorted(denny, key=lambda r: r.rect.x1):
            p = cls.pair(r.data)
            if p is not None:
                reading.den_have, reading.den_cost = p
                break
        return reading

    @classmethod
    def parse_obtained(cls, results: list[OcrMatchResult]) -> tuple[str | None, int | None]:
        """
        确认转化后的「获得」弹窗：图标下的数量和名字（实测 2026-11-25：上面一行「8」，下面一行「特化引燃芯片」）。
        返回 (名字, 数量)，读不出的是 None
        """
        name: str | None = None
        num: int | None = None
        for r in cls.reading_order(results):
            s = cls.norm(r.data)
            if cls.CJK.search(s):
                if name is None:
                    name = s
            elif num is None:
                num = cls.count([r])
        return name, num

    @classmethod
    def tier_from_icon(cls, crop: MatLike | None) -> str | None:
        """
        图标框的下半部分里找色条（crop 是 RGB 截图的一块）：某档颜色连续 TIER_BAR_MIN_ROWS 行、每行都盖住
        TIER_BAR_MIN_COVER 以上的宽度才算这一档；正好一档有色条才返回它，没有或不止一档都返回 None
        （认不出就取消转化，绝不当作基础）。不按总像素数：特化电击芯片图标下半部分的蓝色画和进阶的色条同色（2026-11-25 实测）
        """
        import cv2
        import numpy as np

        if crop is None or crop.size == 0 or crop.ndim != 3:
            return None
        width = crop.shape[1]
        low = np.ascontiguousarray(crop[crop.shape[0] // 2:, :])
        hsv = cv2.cvtColor(low, cv2.COLOR_RGB2HSV)
        th, ts, tv = cls.TIER_TOL
        found: list[str] = []
        for tier, (h, s, v) in cls.TIER_HSV.items():
            lo = np.array([max(0, h - th), max(0, s - ts), max(0, v - tv)], dtype=np.uint8)
            hi = np.array([min(179, h + th), min(255, s + ts), min(255, v + tv)], dtype=np.uint8)
            rows = (cv2.inRange(hsv, lo, hi) > 0).sum(axis=1) >= cls.TIER_BAR_MIN_COVER * width
            run = longest = 0
            for full in rows:
                run = run + 1 if full else 0
                longest = max(longest, run)
            if longest >= cls.TIER_BAR_MIN_ROWS:
                found.append(tier)
        return found[0] if len(found) == 1 else None

    @staticmethod
    def short_materials(named: list[tuple[str, int, int]]) -> list[tuple[int, str, int, int]]:
        """不够的材料：(在弹窗里的序号, 名字, 有, 需)"""
        return [(i, name, h, n) for i, (name, h, n) in enumerate(named) if h < n]

    @classmethod
    def convert_precheck(cls, reading: DialogReading, named: list[tuple[str, int, int]], step: dict,
                         chip_expected: str | None, apply: bool, reserve: int) -> tuple[str, str]:
        """
        decide() 返回材料不足、又开着自动转化时：这次值不值得点「转化获取」看转化弹窗。
        :return: (CONVERT, 说明) 或 (SHORT, 不转化的原因)；SHORT 时和原来一样跳过并记进刷取队列
        """
        S = SkillStepStatus
        den_have, den_need = reading.den_have, reading.den_need
        if reading.need_level:
            return S.SHORT, f'代理人等级不足 {reading.need_level}'
        if den_have is None or den_need is None:
            return S.SHORT, '读不到丁尼'
        if den_have < den_need:
            return S.SHORT, '丁尼不够（转化补不了丁尼）'
        if not chip_expected:
            return S.SHORT, '查不到这一级要的芯片名'
        short = cls.short_materials(named)
        if len(short) != 1:
            return S.SHORT, f'不够的材料有 {len(short)} 种'
        _, name, have, need = short[0]
        if name == CAGE:
            return S.SHORT, f'缺的是{CAGE}，不能转化'
        if name != chip_expected:
            return S.SHORT, f'不够的是 {name}，这一级要的是 {chip_expected}'
        parts = cls.chip_parts(name)
        if parts is None:
            return S.SHORT, f'{name} 不是芯片'
        tier = parts[1]
        if tier == 'C':
            return S.SHORT, '基础芯片没有更低一档可以转化'
        if apply and tier != 'A':
            return S.SHORT, '进阶芯片转化没验证过'
        expected = step.get('cost') or {}
        if apply and not cls.cost_matches(expected, den_need, named):
            return S.SHORT, '强化弹窗的花费和预计不一样'
        missing = need - have
        est = CONVERT_DENNY_PER_CHIP * missing
        if den_have - est < den_need:
            return S.SHORT, f'丁尼 {den_have} 不够先转化（约 {est}）再升级（{den_need}）'
        den_cost = max(den_need, int(expected.get(DENNY, 0) or 0))
        if reserve and den_have - den_cost < reserve:
            return S.SHORT, f'丁尼 {den_have} 花 {den_cost} 后低于保留 {reserve}'
        if reserve and den_have - den_cost - est < reserve:
            return S.SHORT, f'丁尼 {den_have} 转化（约 {est}）再花 {den_cost} 后低于保留 {reserve}'
        return S.CONVERT, f'差 {missing} 个{name}，先看「转化获取」'

    @classmethod
    def check_convert(cls, conv: ConvertReading, reading: DialogReading, named: list[tuple[str, int, int]],
                      chip_expected: str | None, step: dict, apply: bool, reserve: int) -> tuple[str, str, dict]:
        """
        核对转化弹窗：芯片名、差几个、只转化差的那几个、低一档 3 换 1、丁尼和强化弹窗一样、花费合理、转化加升级后丁尼不低于保留线。
        真升级只做实测过的 进阶→特化，丁尼必须正好每个 300。
        :return: (CONVERT 或 CONVERT_SKIPPED, 说明, 转化计划 {from, used, to, made, dennies, den_before}（没通过时为空）)
        """
        bad = SkillStepStatus.CONVERT_SKIPPED
        if not conv.complete():
            return bad, f'转化弹窗没读全（{conv.as_dict()}）', {}
        if not chip_expected or conv.chip != chip_expected:
            return bad, f'转化弹窗要的是 {conv.chip}，这一级要 {chip_expected}', {}
        short = cls.short_materials(named)
        if len(short) != 1:
            return bad, f'不够的材料有 {len(short)} 种', {}
        _, _, have, need = short[0]
        if conv.missing != need - have:
            return bad, f'转化弹窗说还差 {conv.missing} 个，强化弹窗是 {have}/{need}', {}
        if conv.dst_count != conv.missing:
            return bad, f'转化出来 {conv.dst_count} 个，差的是 {conv.missing} 个', {}
        parts = cls.chip_parts(chip_expected)
        if parts is None:
            return bad, f'{chip_expected} 不是芯片', {}
        element, tier = parts
        if conv.dst_tier != tier:
            return bad, f'右边图标档位 {conv.dst_tier}，{chip_expected} 是 {tier}', {}
        if cls.TIER_ORDER.index(conv.src_tier) != cls.TIER_ORDER.index(tier) - 1:
            return bad, f'左边图标档位 {conv.src_tier}，不是比 {tier} 低一档', {}
        if apply and (tier, conv.src_tier) != ('A', 'B'):
            return bad, f'{conv.src_tier}→{tier} 的转化没验证过', {}
        if conv.src_count != CONVERT_RATIO * conv.dst_count:
            return bad, f'用 {conv.src_count} 个换 {conv.dst_count} 个，不是 {CONVERT_RATIO} 换 1', {}
        if conv.den_have != reading.den_have:
            return bad, f'转化弹窗的丁尼 {conv.den_have} 和强化弹窗 {reading.den_have} 不一样', {}
        if conv.den_cost <= 0 or conv.den_cost > CONVERT_DENNY_MAX_PER_CHIP * conv.dst_count:
            return bad, f'转化花丁尼 {conv.den_cost}，不合理', {}
        if apply and conv.den_cost != CONVERT_DENNY_PER_CHIP * conv.dst_count:
            return bad, f'转化花丁尼 {conv.den_cost}，不是每个 {CONVERT_DENNY_PER_CHIP}', {}
        if conv.den_have - conv.den_cost < reading.den_need:
            return bad, f'丁尼 {conv.den_have} 转化花 {conv.den_cost} 后不够升级（{reading.den_need}）', {}
        den_up = max(reading.den_need, int((step.get('cost') or {}).get(DENNY, 0) or 0))
        if reserve and conv.den_have - conv.den_cost - den_up < reserve:
            return bad, f'丁尼 {conv.den_have} 转化花 {conv.den_cost} 再升级花 {den_up} 后低于保留 {reserve}', {}
        plan = {'from': cls.tier_name(element, conv.src_tier), 'used': conv.src_count, 'to': chip_expected,
                'made': conv.dst_count, 'dennies': conv.den_cost, 'den_before': conv.den_have}
        note = '' if conv.den_cost == CONVERT_DENNY_PER_CHIP * conv.dst_count else f'（丁尼不是每个 {CONVERT_DENNY_PER_CHIP}）'
        return (SkillStepStatus.CONVERT,
                f"用 {plan['from']} {plan['used']} 转化 {plan['to']} {plan['made']}（丁尼 {plan['dennies']}）{note}", plan)

    @staticmethod
    def check_after_convert(before: DialogReading, after: DialogReading, conv: ConvertReading,
                            idx: int) -> tuple[str, str]:
        """
        确认转化后回到强化弹窗：
        'pending' 数字还没变（画面没刷新）；'ok' 同一级、花费不变、这种芯片正好多了转化出来的数、丁尼正好少了转化花的、什么都不缺；
        其它都是 'bad'（可能是只刷新了一半，调用方先重试）
        """
        if after.mats == before.mats and after.den_pair == before.den_pair:
            return 'pending', '转化后强化弹窗的数量还没变'
        problems: list[str] = []
        if (after.from_lv, after.to_lv) != (before.from_lv, before.to_lv):
            problems.append(f'等级 {after.from_lv}→{after.to_lv}')
        if after.need_level:
            problems.append(f'代理人等级不足 {after.need_level}')
        if after.den_need != before.den_need:
            problems.append(f'丁尼花费 {after.den_need}（原来 {before.den_need}）')
        if len(after.mats) != len(before.mats) or [n for _, n in after.mats] != [n for _, n in before.mats]:
            problems.append(f'材料 {after.mats}（原来 {before.mats}）')
        else:
            for j, ((h0, _), (h1, _)) in enumerate(zip(before.mats, after.mats)):
                want = h0 + conv.dst_count if j == idx else h0
                if h1 != want:
                    problems.append(f'第 {j + 1} 种材料有 {h1}，应该是 {want}')
        if before.den_have is None or after.den_have != before.den_have - conv.den_cost:
            problems.append(f'丁尼 {after.den_have}，应该是 {before.den_have} − {conv.den_cost}')
        if (after.den_have is None or after.den_need is None or after.den_have < after.den_need
                or any(h < n for h, n in after.mats)):
            problems.append('还是不够')
        if problems:
            return 'bad', '；'.join(problems)
        return 'ok', f'转化后 材料 {after.mats}，丁尼 {after.den_have}/{after.den_need}'
