"""邦布升级的画面操作。流程 2026-11-21 在游戏里一步步实测（1920×1080，简体中文）：

六分街 › 改装店 → 交互 F → 邦布界面（底部 10 个位置的邦布列表，可左右拖动）
→ 选中带红点的邦布 → 面板「等级」行的箭头 → 「邦布升级」窗口 → 自动添加 → 升级
→ 有多余经验时弹「材料返还」→ 确认 → 到等级上限时弹「系统优化」（突破：材料 + 丁尼）→ 优化。

一条龙的截图是 RGB。
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np

from one_dragon.base.geometry.point import Point
from one_dragon.utils.log_utils import log
from zzz_od.application.agent_upgrade.agent_upgrade_game import StopRun, UpgradeGame

if TYPE_CHECKING:
    from cv2.typing import MatLike

# ---- 坐标（1920×1080）----
SLOT_X = [160, 335, 510, 685, 860, 1035, 1210, 1385, 1560, 1735]  # 底部邦布列表 10 个位置的中心
SLOT_Y = 930
DOT_BOX = (-50, 843, -26, 861)   # 红点：相对位置中心的 x 范围 + 绝对 y 范围（红点中心约 x-38, y 851）
LEVEL_ARROW = (1740, 260)        # 面板「等级」行右边的箭头
STAR_DOT_BOX = (1695, 300, 1730, 335)  # 面板「星级」行箭头上的红点
AUTO_ADD = (828, 823)            # 邦布升级窗口：自动添加 / 全部清除
UPGRADE_BTN = (1174, 823)        # 邦布升级窗口：升级
LVL_CLOSE = (1375, 252)          # 邦布升级窗口右上角 ×
RETURN_OK = (977, 728)           # 材料返还：确认
OPTIMIZE_BTN = (1151, 850)       # 系统优化：优化
OPT_CLOSE = (1460, 228)          # 系统优化窗口右上角 ×


def _red(hsv: np.ndarray) -> np.ndarray:
    """亮红色。背后红色工作台偏暗，不会算进去（5 张截图校准：红点约 190 像素，背景 0）"""
    return ((hsv[..., 0] < 6) | (hsv[..., 0] > 174)) & (hsv[..., 1] > 200) & (hsv[..., 2] > 225)


class BangbooUpgrader:

    def __init__(self, game: UpgradeGame, apply: bool) -> None:
        self.game: UpgradeGame = game
        self.ctx = game.ctx
        self.apply: bool = apply

    # ---------- 基础操作 ----------
    def click(self, xy: tuple[int, int], what: str, wait: float = 1.5) -> None:
        self.game.check_stop()
        log.info(f'点击 @{xy[0]},{xy[1]}（{what}）')
        self.ctx.controller.click(Point(*xy))
        time.sleep(wait)

    def drag(self, x1: int, x2: int) -> None:
        self.ctx.controller.drag_to(start=Point(x1, 940), end=Point(x2, 940), duration=1)
        time.sleep(1.5)

    def text(self) -> str:
        return ' '.join(t.text for t in self.game.ocr())

    def in_bangboo_screen(self) -> bool:
        t = self.text()
        return '属性详情' in t or 'BANGBOO' in t

    # ---------- 列表：红点和指纹 ----------
    @staticmethod
    def has_dot(im: MatLike, x: int) -> bool:
        hsv = cv2.cvtColor(im[DOT_BOX[1]:DOT_BOX[3], max(0, x + DOT_BOX[0]):x + DOT_BOX[2]], cv2.COLOR_RGB2HSV)
        return int(_red(hsv).sum()) > 100

    @staticmethod
    def fp(im: MatLike, x: int) -> dict:
        """邦布外形指纹（内框 + 外框灰度）。翻页后位置会差几个像素，比对时允许位移"""
        g = cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)
        return {'inner': g[870:990, x - 50:x + 20].copy(), 'wide': g[860:1000, max(0, x - 90):x + 60].copy()}

    @staticmethod
    def same(a: dict, b: dict) -> bool:
        # 校准：同一个邦布 0.9999，不同邦布约 0.37
        s1 = cv2.matchTemplate(a['wide'], b['inner'], cv2.TM_CCOEFF_NORMED).max()
        s2 = cv2.matchTemplate(b['wide'], a['inner'], cv2.TM_CCOEFF_NORMED).max()
        return max(float(s1), float(s2)) > 0.9

    def scan(self, on_page=None) -> list[tuple[dict, bool]]:
        """从头翻到尾，返回按列表顺序去重的 [(指纹, 有红点)]；on_page(画面) 返回 True 时提前结束"""
        for _ in range(4):
            self.drag(300, 1700)
        seen: list[tuple[dict, bool]] = []
        prev = None
        for page in range(10):
            self.game.check_stop()
            im = self.game.screenshot()
            if on_page is not None and on_page(im):
                return seen
            for x in SLOT_X:
                f = self.fp(im, x)
                if not any(self.same(f, s[0]) for s in seen):
                    seen.append((f, self.has_dot(im, x)))
            strip = im[860:990].astype(np.int16)
            if prev is not None and float(np.abs(strip - prev).mean()) < 3:
                break  # 拖不动了，到底了
            prev = strip
            self.drag(1600, 200)
        return seen

    def find_targets(self, count: int) -> list[dict]:
        """红点只出现在已拥有、能升级的邦布上；列表里最后 count 个带红点的就是最新的"""
        seen = self.scan()
        dots = [f for f, d in seen if d]
        log.info(f'邦布列表共 {len(seen)} 个，带红点 {len(dots)} 个，处理最后 {min(count, len(dots))} 个')
        return dots[-count:] if count > 0 else []

    def select(self, target: dict) -> None:
        hit: dict = {}

        def look(im: MatLike) -> bool:
            for x in SLOT_X:
                if self.same(self.fp(im, x), target):
                    hit['x'] = x
                    return True
            return False

        self.scan(look)
        if 'x' not in hit:
            raise StopRun('翻遍邦布列表没找到要升级的邦布')
        self.click((hit['x'], SLOT_Y), '选中邦布')

    # ---------- 面板读数 ----------
    def read_panel(self) -> dict:
        texts = self.game.ocr()
        name = next((t.text for t in texts if 1450 < t.cx < 1800 and 120 < t.cy < 190), '?')
        lv = next((t.text for t in texts if 1450 < t.cx < 1600 and 230 < t.cy < 290 and '等级' in t.text), '')
        m = re.search(r'(\d+)', lv)
        return {'name': name, 'level': int(m.group(1)) if m else None}

    def has_star_dot(self) -> bool:
        im = self.game.screenshot()
        x1, y1, x2, y2 = STAR_DOT_BOX
        return int(_red(cv2.cvtColor(im[y1:y2, x1:x2], cv2.COLOR_RGB2HSV)).sum()) > 30

    # ---------- 升级一个邦布 ----------
    def upgrade_one(self, target_level: int, optimize: bool, budget: dict) -> dict:
        info = self.read_panel()
        rec: dict = {'name': info['name'], 'before': info['level'], 'steps': [], 'stopped': None}
        log.info(f'邦布 {info["name"]} 当前等级 {info["level"]}')
        for _ in range(20):
            self.game.check_stop()
            cur = self.read_panel()['level']
            if cur is not None and cur >= target_level:
                rec['stopped'] = f'已到目标 {target_level} 级'
                break
            self.click(LEVEL_ARROW, '打开升级')
            t = self.text()
            if '系统优化' in t:
                if not self._optimize(optimize, budget, rec):
                    break
                continue
            if '邦布升级' not in t:
                path = self.game.shot('no_upgrade_dialog')
                rec['stopped'] = f'没打开升级窗口（截图 {path.name}）'
                break
            self.click(AUTO_ADD, '自动添加')
            if '全部清除' not in self.text():
                rec['stopped'] = '自动添加没放进材料（材料不够）'
                self.click(LVL_CLOSE, '关闭升级窗口')
                break
            if not self.apply:
                rec['stopped'] = '演练：不点升级'
                self.click(AUTO_ADD, '全部清除')
                self.click(LVL_CLOSE, '关闭升级窗口')
                break
            self.click(UPGRADE_BTN, '升级', wait=2.0)
            rec['steps'].append({'what': 'level_up', 'ok': True})
            t = self.text()
            if '材料返还' in t:
                self.click(RETURN_OK, '确认材料返还')
                t = self.text()
            if '系统优化' in t and not self._optimize(optimize, budget, rec):
                break
        rec['after'] = self.read_panel()['level']
        if self.has_star_dot():
            rec['note'] = '星级那一行有红点（可升星），这次没做'
        return rec

    def _optimize(self, optimize: bool, budget: dict, rec: dict) -> bool:
        """系统优化（突破）。返回 False 表示这个邦布到此为止"""
        texts = self.game.ocr()
        cost_t = next((t.text for t in texts if '/' in t.text and 700 < t.cx < 1000 and 820 < t.cy < 880), '')
        m = re.search(r'(\d+)\s*/\s*(\d+)', cost_t)
        have, cost = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        log.info(f'系统优化：丁尼 {have} / 需要 {cost}')
        why = None
        if not optimize:
            why = '设置了不做系统优化'
        elif not self.apply:
            why = '演练'
        elif cost is None:
            why = '读不到花费'
        elif budget['left'] < cost:
            why = f'超出本次丁尼上限（剩 {budget["left"]}）'
        elif have is not None and have < cost:
            why = '丁尼不够'
        if why:
            rec['stopped'] = f'到等级上限，没做系统优化：{why}'
            self.click(OPT_CLOSE, '关闭系统优化')
            return False
        self.click(OPTIMIZE_BTN, '优化', wait=2.5)
        t = self.text()
        if '确认' in t:
            self.click(RETURN_OK, '关闭优化结果')
        budget['left'] -= cost
        budget['spent'] += cost
        rec['steps'].append({'what': 'optimize', 'dennies': cost, 'dennies_before': have, 'ok': True})
        return True
