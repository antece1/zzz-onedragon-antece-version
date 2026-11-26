"""角色培养界面用到的小组件：统计卡、代理人卡、材料格子、驱动盘卡、排期卡、刷取表。

只负责显示和把用户的改动用信号发出去，计算全部在 planner_core 里。
颜色和网页 docs/index.html 一致，每个颜色都给了亮色 / 暗色两套。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FlowLayout,
    PillPushButton,
    SimpleCardWidget,
    SpinBox,
    StrongBodyLabel,
    TableWidget,
    TitleLabel,
    CustomStyleSheet,
    setCustomStyleSheet,
    setStyleSheet,
)

from zzz_od.application.character_growth import planner_core as pc

# ---------------------------------------------------------------- 颜色（亮色, 暗色）

GOOD = ("#1f8a4c", "#4cc47f")
WARN = ("#b86e00", "#e7a23b")
BAD = ("#c23b3b", "#ef6b6b")
MUTED = ("#5d6475", "#9aa1b2")
ACCENT = "#e8b400"  # 网页的警示黄，★ 优先培养用
TIER = {"C": "#2f9c8a", "B": "#3570d4", "A": "#8a4fd6", "S": "#d98a00"}
GAP_COLOR = {"ok": GOOD, "craft": WARN, "short": BAD}


def fmt(n) -> str:
    """整数 + 千分位，和网页的 fmt 一样。"""
    try:
        return f"{round(n):,}"
    except (TypeError, ValueError):
        return str(n)


def themed_qss(widget: QWidget, light: str, dark: str) -> None:
    """给普通 Qt 控件设亮色 / 暗色两套样式，并登记到 qfluentwidgets 的样式管理里，切换主题时自动换。
    （setCustomStyleSheet 只对已经登记过的 fluent 控件生效）"""
    setCustomStyleSheet(widget, light, dark)
    setStyleSheet(widget, CustomStyleSheet(widget))


def color_label(label, pair: tuple[str, str]):
    label.setTextColor(QColor(pair[0]), QColor(pair[1]))
    return label


def muted(text: str = "", parent=None, wrap: bool = False) -> CaptionLabel:
    lb = CaptionLabel(text, parent)
    color_label(lb, MUTED)
    lb.setWordWrap(wrap)
    return lb


class BoxLabel(CaptionLabel):
    """带边框的小标签。
    qfluentwidgets 的标签切换主题时会调用 setTextColor，用只有文字颜色的样式覆盖自定义样式；
    这里重写 setTextColor，每次都把边框样式（含文字颜色）一起设回去。"""

    # 不重写 __init__：FluentLabelBase 的 __init__ 是 singledispatchmethod，(text, parent) 版本会再调 self.__init__(parent)

    def set_box_qss(self, light_qss: str, dark_qss: str) -> "BoxLabel":
        self._box_qss = (light_qss, dark_qss)
        self.setTextColor()
        return self

    def setTextColor(self, light=QColor(0, 0, 0), dark=QColor(255, 255, 255)):
        self._lightColor = QColor(light)
        self._darkColor = QColor(dark)
        qss = getattr(self, "_box_qss", None)
        if qss is None:  # 父类构造时先调一次，那时还没有边框样式
            CaptionLabel.setTextColor(self, light, dark)
            return
        setCustomStyleSheet(self, qss[0], qss[1])


def pill(text: str, pair: tuple[str, str], parent=None) -> CaptionLabel:
    """带边框的小标签（网页的 .pill）。"""
    lq, dq = (f"CaptionLabel{{color:{c};border:1px solid {c};border-radius:9px;padding:0px 8px;background:transparent;}}"
              for c in pair)
    return BoxLabel(text, parent).set_box_qss(lq, dq)


def chip(text: str, parent=None) -> CaptionLabel:
    """灰色方框标签（网页的 .chip）。"""
    return BoxLabel(text, parent).set_box_qss(
        "CaptionLabel{color:#5d6475;border:1px solid #d5d9e2;padding:0px 6px;background:transparent;}",
        "CaptionLabel{color:#9aa1b2;border:1px solid #3a3f4e;padding:0px 6px;background:transparent;}")


def accent_bar(parent=None) -> QFrame:
    bar = QFrame(parent)
    bar.setFixedHeight(3)
    bar.setStyleSheet(f"QFrame{{background-color:{ACCENT};border:none;}}")
    return bar


def tier_icon(char: str, tier: str, parent=None, size: int = 40) -> QLabel:
    lb = QLabel(char, parent)
    lb.setFixedSize(size, size)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lb.setStyleSheet(f"QLabel{{background-color:{TIER.get(tier, TIER['S'])};color:#ffffff;border-radius:4px;"
                     f"font-weight:bold;font-size:{size // 2 - 2}px;}}")
    return lb


def h_row(*widgets, spacing: int = 6, stretch_end: bool = True) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    if stretch_end:
        lay.addStretch(1)
    return lay


class FitFlowBox(QWidget):
    """FlowLayout 的容器：宽度变了就把标了 fit 的卡片等宽拉伸，一行正好排满（每张不窄于 min_width），右边不留大块空白。"""

    def __init__(self, parent=None, min_width: int = 0):
        QWidget.__init__(self, parent)
        self.min_width = min_width

    def resizeEvent(self, e):
        QWidget.resizeEvent(self, e)
        self.fit()

    def fit(self) -> None:
        lay = self.layout()
        if not self.min_width or lay is None:
            return
        m = lay.contentsMargins()
        avail = self.width() - m.left() - m.right() - 1  # FlowLayout 换行判断用 rect.right()（宽度 − 1）
        sp = lay.horizontalSpacing()
        n = max(1, (avail + sp) // (self.min_width + sp))
        w = max(self.min_width, (avail - (n - 1) * sp) // n)
        for i in range(lay.count()):
            wd = lay.itemAt(i).widget()
            if wd is not None and wd.property("fit") and wd.maximumWidth() != w:
                wd.setFixedWidth(w)


def fit_card(w: QWidget, width: int) -> QWidget:
    """卡片的最小宽度；放进 FitFlowBox 后按一行排几张自动拉宽。"""
    w.setFixedWidth(width)
    w.setProperty("fit", True)
    return w


def flow_container(parent=None, h_spacing: int = 10, v_spacing: int = 10, fit_width: int = 0) -> tuple[QWidget, FlowLayout]:
    """fit_width > 0 时，标了 fit 的卡片（fit_card）按容器宽度等宽拉伸。"""
    w = FitFlowBox(parent, fit_width)
    lay = FlowLayout(w, needAni=False)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setHorizontalSpacing(h_spacing)
    lay.setVerticalSpacing(v_spacing)
    return w, lay


class NoWheelSpinBox(SpinBox):
    """没获得焦点时不吃滚轮，避免滚动页面时误改数量。"""

    def __init__(self, parent=None):
        SpinBox.__init__(self, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            SpinBox.wheelEvent(self, e)
        else:
            e.ignore()


def number_combo(lo: int, hi: int, value: int, width: int = 72, labels: list[str] | None = None) -> ComboBox:
    cb = ComboBox()
    cb.addItems(labels if labels is not None else [str(i) for i in range(lo, hi + 1)])
    cb.setCurrentIndex(max(0, min(cb.count() - 1, value - lo)))
    cb.setFixedWidth(width)
    cb.setMaxVisibleItems(12)
    return cb


# ---------------------------------------------------------------- 顶部统计

class StatCard(SimpleCardWidget):

    def __init__(self, caption: str, key: bool = False, parent=None):
        SimpleCardWidget.__init__(self, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(0)
        if key:
            lay.addWidget(accent_bar(self))
        else:
            lay.addSpacing(3)
        inner = QVBoxLayout()
        inner.setContentsMargins(14, 6, 14, 0)
        inner.setSpacing(0)
        self.value_label = TitleLabel("0", self)
        self.caption_label = muted(caption, self)
        inner.addWidget(self.value_label)
        inner.addWidget(self.caption_label)
        lay.addLayout(inner)

    def set_value(self, text: str, caption: str | None = None) -> None:
        self.value_label.setText(text)
        if caption is not None:
            self.caption_label.setText(caption)


# ---------------------------------------------------------------- 代理人卡

class AgentCard(SimpleCardWidget):
    """一个代理人：★ / 计入 / 当前（只读）→ 目标（可改）。"""

    star_clicked = Signal(str)
    on_toggled = Signal(str, bool)
    target_changed = Signal(str, str, int)  # agent_id, field(lv/core/wl/sk0..sk4), value

    WIDTH = 352

    def __init__(self, aid: str, meta: dict, cur: dict, tgt: dict, counted: bool, starred: bool, parent=None):
        SimpleCardWidget.__init__(self, parent)
        self.aid = aid
        fit_card(self, self.WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.bar = accent_bar(self)  # ★ 的黄条：一直建着，切换 ★ 时只改显示，不用重建卡片
        outer.addWidget(self.bar)
        body = QVBoxLayout()
        body.setSpacing(8)
        self.body_layout = body
        outer.addLayout(body)

        # 标题行：★、名字、稀有度 / 职业 / 属性 / 影画、计入
        self.star_btn = PillPushButton("★", self)
        self.star_btn.setCheckable(True)
        self.star_btn.setChecked(starred)
        self.star_btn.setFixedWidth(44)
        self.star_btn.setToolTip("设为优先培养：它的材料排在每日电量最前面，并开始刷它的驱动盘")
        self.star_btn.clicked.connect(lambda: self.star_clicked.emit(self.aid))
        self.name_label = StrongBodyLabel(meta.get("name", aid), self)
        f = self.name_label.font(); f.setPixelSize(17); self.name_label.setFont(f)
        head = QHBoxLayout(); head.setContentsMargins(0, 0, 0, 0); head.setSpacing(6)
        head.addWidget(self.star_btn); head.addWidget(self.name_label)
        for t in (f"{meta['rarity']}级" if meta.get("rarity") else None, meta.get("sp"), meta.get("attr"),
                  f"影画 {meta['m']}" if meta.get("m") else None):
            if t: head.addWidget(chip(t, self))
        head.addStretch(1)
        self.on_box = CheckBox("计入", self)
        self.on_box.setChecked(counted)
        self.on_box.toggled.connect(lambda v: self.on_toggled.emit(self.aid, bool(v)))
        head.addWidget(self.on_box)
        body.addLayout(head)

        # 当前 → 目标
        grid = QGridLayout(); grid.setContentsMargins(0, 0, 0, 0); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(6)
        grid.setColumnMinimumWidth(0, 44)
        arrow = lambda: muted("→", self)

        def row(r: int, title: str, cur_text: str, editor: QWidget, extra: QWidget | None = None):
            grid.addWidget(muted(title, self), r, 0)
            cl = BodyLabel(cur_text, self)
            cl.setMinimumWidth(64)
            cl.setToolTip("当前值：每晚 23:00 从 HoYoLAB 同步，这里只读")
            line = QHBoxLayout(); line.setContentsMargins(0, 0, 0, 0); line.setSpacing(6)
            line.addWidget(cl); line.addWidget(arrow()); line.addWidget(editor)
            if extra is not None: line.addWidget(extra)
            line.addStretch(1)
            grid.addLayout(line, r, 1)

        self.lv_cb = number_combo(1, 60, tgt["lv"])
        self.lv_cb.currentIndexChanged.connect(lambda i: self.target_changed.emit(self.aid, "lv", i + 1))
        row(0, "等级", f"Lv {cur.get('lv', 1)}", self.lv_cb)

        self.core_cb = number_combo(0, 6, tgt["core"], labels=list(pc.CORE_NAMES))
        self.core_cb.currentIndexChanged.connect(lambda i: self.target_changed.emit(self.aid, "core", i))
        row(1, "核心技", pc.CORE_NAMES[max(0, min(6, cur.get("core", 0)))], self.core_cb)

        wr = cur.get("wr") or "-"
        self.wl_cb = number_combo(1, 60, tgt["wl"])
        self.wl_cb.currentIndexChanged.connect(lambda i: self.target_changed.emit(self.aid, "wl", i + 1))
        if wr == "-":
            self.wl_cb.setEnabled(False)
            row(2, "音擎", "未装备", self.wl_cb)
        else:
            wname = muted(cur.get("wname") or "", self)
            wname.setMaximumWidth(120)
            row(2, "音擎", f"{wr}级 Lv {cur.get('wl', 1)}", self.wl_cb, wname)
        body.addLayout(grid)

        # 技能（去掉影画加成后的基础等级）
        skills = QGridLayout(); skills.setContentsMargins(0, 0, 0, 0); skills.setHorizontalSpacing(4); skills.setVerticalSpacing(2)
        cur_sk = list(cur.get("sk") or [1] * 5)
        self.sk_cbs = []
        for i, name in enumerate(pc.SKILLS):
            n = muted(name, self); n.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c = BodyLabel(f"{cur_sk[i]} →", self); c.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c.setToolTip("当前基础等级（只读）")
            cb = number_combo(1, 12, tgt["sk"][i], width=62)
            cb.currentIndexChanged.connect(lambda idx, k=i: self.target_changed.emit(self.aid, f"sk{k}", idx + 1))
            self.sk_cbs.append(cb)
            skills.addWidget(n, 0, i, Qt.AlignmentFlag.AlignHCenter)
            skills.addWidget(c, 1, i, Qt.AlignmentFlag.AlignHCenter)
            skills.addWidget(cb, 2, i, Qt.AlignmentFlag.AlignHCenter)
        sk_title = QHBoxLayout(); sk_title.setContentsMargins(0, 0, 0, 0)
        sk_title.addWidget(muted("技能", self)); sk_title.addStretch(1)
        body.addLayout(sk_title)
        body.addLayout(skills)

        line = QFrame(self); line.setFixedHeight(1)
        themed_qss(line, "QFrame{background-color:#d5d9e2;border:none;}", "QFrame{background-color:#3a3f4e;border:none;}")
        body.addWidget(line)
        self.mini_label = muted("", self)
        body.addWidget(self.mini_label)
        self.set_starred(starred)
        self.set_counted(counted)

    def set_starred(self, starred: bool) -> None:
        self.bar.setVisible(starred)
        self.body_layout.setContentsMargins(12, 10 if starred else 13, 12, 12)
        if self.star_btn.isChecked() != starred:
            self.star_btn.setChecked(starred)

    def set_counted(self, counted: bool) -> None:
        if self.on_box.isChecked() != counted:
            self.on_box.blockSignals(True)
            self.on_box.setChecked(counted)
            self.on_box.blockSignals(False)
        if counted:
            self.name_label.setTextColor(QColor(0, 0, 0), QColor(255, 255, 255))
        else:
            color_label(self.name_label, MUTED)

    def set_cost(self, per: dict | None) -> None:
        if per is None:
            self.mini_label.setText("未计入")
        else:
            self.mini_label.setText(f"这个代理人要花 {fmt(per['den'])} 丁尼" + (f" · 还差 {per['lv_gap']} 级" if per.get("lv_gap") else ""))


# ---------------------------------------------------------------- 材料格子

ICON_CHAR = {"expA": "记", "expW": "擎", "seal": "章", "chip": "芯", "comp": "件", "hdd": "数", "nh": "狩", "丁尼": "丁", pc.CAGE: "笼"}


class MaterialTile(SimpleCardWidget):
    """一个材料：图标、名字、需要多少、背包（可改）、缺口。"""

    own_changed = Signal(str, int)

    WIDTH = 214

    def __init__(self, group_key: str, tile: dict, parent=None):
        SimpleCardWidget.__init__(self, parent)
        self.name = tile["name"]
        self.group_key = group_key
        fit_card(self, self.WIDTH)
        char = ICON_CHAR.get(tile["name"]) or ICON_CHAR.get(tile.get("kind") or group_key, "材")
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        grid.addWidget(tier_icon(char, tile.get("tier", "S"), self), 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        name = StrongBodyLabel(tile["name"], self)
        name.setWordWrap(True)
        f = name.font(); f.setPixelSize(12); name.setFont(f)
        if tile.get("boss"):
            where = "恶名狩猎" if tile.get("kind") == "nh" else "专业挑战室"
            name.setToolTip(f"{where} › {tile['boss']}")
        grid.addWidget(name, 0, 1)
        self.need_label = BodyLabel("", self)
        grid.addWidget(self.need_label, 1, 1)
        own = QHBoxLayout(); own.setContentsMargins(0, 0, 0, 0); own.setSpacing(6)
        own.addWidget(muted("背包", self))
        self.spin = NoWheelSpinBox(self)
        self.spin.setRange(0, 2_000_000_000)
        self.spin.setValue(int(round(float(tile.get("have") or 0))))
        self.spin.setEnabled(bool(tile.get("editable", True)))
        self.spin.setMinimumWidth(130)
        self.spin.valueChanged.connect(lambda v: self.own_changed.emit(self.name, int(v)))
        own.addWidget(self.spin, 1)
        grid.addLayout(own, 2, 0, 1, 2)
        self.gap_label = CaptionLabel("", self)
        self.gap_label.setWordWrap(True)
        f = self.gap_label.font(); f.setBold(True); self.gap_label.setFont(f)
        grid.addWidget(self.gap_label, 3, 0, 1, 2)
        self.update_tile(tile)

    def update_tile(self, tile: dict) -> None:
        need = tile.get("need")
        if self.group_key in ("expA", "expW"):
            self.need_label.setText(f"{fmt(need)} 个（折合）" if tile.get("tier") == "A" else f"每个 {tile.get('value', '')} 经验")
        elif self.name == "丁尼":
            self.need_label.setText(f"需要 {fmt(need)}")
        else:
            self.need_label.setText(f"需要 {fmt(need)} 个")
        gap = tile.get("gap")
        self.gap_label.setVisible(gap is not None)
        if gap:
            self.gap_label.setText(gap["text"])
            color_label(self.gap_label, GAP_COLOR.get(gap["status"], MUTED))


# ---------------------------------------------------------------- 刷取表

FARM_HEADERS = ["去哪刷", "为了什么", "每次电量", "每次大约掉落", "次数/卡数", "电量", "数据"]


def farm_table(rows: list[dict], nh_rows: list[dict], parent=None) -> TableWidget:
    """刷取建议表（网页“刷取建议”）。rows 是 planner_core.farm_rows 的结果，nh_rows 是 compute_needs 的恶名狩猎行。"""
    t = TableWidget(parent)
    t.setBorderVisible(True)
    t.setBorderRadius(8)
    t.setWordWrap(False)
    t.setColumnCount(len(FARM_HEADERS))
    t.setHorizontalHeaderLabels(FARM_HEADERS)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    lines = []
    for r in rows:
        if r.get("disc"):
            lines.append((pc.row_where(r), r["why"], "60", pc.row_drop(r), f"每天 {r['n']}", f"每天 {r['n'] * 60}", "持续", GOOD))
        else:
            est = pc.row_estimated(r)
            why = ("队列 · " if r.get("queue") and not r["why"].startswith("队列") else "") + r["why"]
            lines.append((pc.row_where(r), why, str(r["cost"]), pc.row_drop(r), fmt(r["n"]), fmt(r["n"] * r["cost"]),
                          "估算" if est else "已确认", WARN if est else GOOD))
    for r in nh_rows:
        lines.append((r["where"], r["why"], str(r["cost"]), r["drop"], fmt(r["runs"]),
                      f"{fmt(r['battery'])}（或等 {r['weeks']} 周免费）", "已确认", GOOD))
    if not lines:
        lines.append(("目标都达成了，不需要再刷。", "", "", "", "", "", "", MUTED))
    t.setRowCount(len(lines))
    from qfluentwidgets import isDarkTheme
    for i, line in enumerate(lines):
        for j, text in enumerate(line[:7]):
            it = QTableWidgetItem(text)
            if j in (2, 4, 5):
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if j == 6 and text:
                it.setForeground(QColor(line[7][1] if isDarkTheme() else line[7][0]))
            if j <= 1:
                it.setToolTip(text)
            t.setItem(i, j, it)
    hh = t.horizontalHeader()
    for j, mode in enumerate((QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.Stretch,
                              QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.ResizeToContents,
                              QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.ResizeToContents,
                              QHeaderView.ResizeMode.ResizeToContents)):
        hh.setSectionResizeMode(j, mode)
    t.resizeRowsToContents()
    height = hh.sizeHint().height() + sum(t.rowHeight(i) for i in range(t.rowCount())) + 6
    t.setFixedHeight(height)
    return t


# ---------------------------------------------------------------- 排期

class DayCard(SimpleCardWidget):
    """一天的电量排期。items: schedule() 里的 [{row, n}]"""

    WIDTH = 252

    def __init__(self, title: str, items: list[dict], per_day: int, left: int, parent=None):
        SimpleCardWidget.__init__(self, parent)
        fit_card(self, self.WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(3)
        lay.addWidget(StrongBodyLabel(title, self))
        disc_runs = sum(x["n"] for x in items if x["row"].get("kind") == "disc")
        used = per_day - left
        lay.addWidget(muted(f"用 {fmt(used)} 电量" + (f" · ★ 驱动盘 {disc_runs} 次" if disc_runs else ""), self))
        for x in items:
            r = x["row"]
            text = f"{pc.row_where(r).split(' › ')[-1]} ×{x['n']}{' 张卡' if r['cost'] == 20 else ' 次'}（{r['why']}）"
            lb = CaptionLabel("• " + text, self)
            lb.setWordWrap(True)
            if r.get("star") or r.get("disc"):
                lb.setTextColor(QColor("#8a6a00"), QColor("#f5c518"))
            lay.addWidget(lb)
        if left:
            lay.addWidget(muted(f"当天剩 {left} 电量", self))
        lay.addStretch(1)


class NoteCard(SimpleCardWidget):
    """一段说明文字（排期后的“之后”、空状态等）。"""

    def __init__(self, title: str, text: str, width: int | None = None, parent=None):
        SimpleCardWidget.__init__(self, parent)
        if width:
            fit_card(self, width)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)
        lay.addWidget(StrongBodyLabel(title, self))
        body = BodyLabel(text, self)
        body.setWordWrap(True)
        lay.addWidget(body)


# ---------------------------------------------------------------- 驱动盘

class DiscAgentCard(SimpleCardWidget):
    """一个代理人的 6 个驱动盘 + 4+2 / 没升满 / 非 S / 空位检查，★ 代理人显示推荐套装。"""

    def __init__(self, a: dict, parent=None):
        SimpleCardWidget.__init__(self, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        if a["starred"]:
            outer.addWidget(accent_bar(self))
        lay = QVBoxLayout()
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        outer.addLayout(lay)

        head = QHBoxLayout(); head.setContentsMargins(0, 0, 0, 0); head.setSpacing(8)
        name = StrongBodyLabel(("★ " if a["starred"] else "") + a["name"], self)
        f = name.font(); f.setPixelSize(16); name.setFont(f)
        head.addWidget(name)
        if a["sets"]:
            head.addWidget(chip(" · ".join(f"{k} {v}" for k, v in a["sets"]), self))
        if a["four"] and a["two"]: head.addWidget(pill("4+2 成套", GOOD, self))
        elif a["four"]: head.addWidget(pill("只有 4 件套", WARN, self))
        else: head.addWidget(pill("没凑成 4 件套", BAD, self))
        if a["low"]: head.addWidget(pill(f"{a['low']} 个没升满", BAD, self))
        if a["not_s"]: head.addWidget(pill(f"{a['not_s']} 个不是 S 级", WARN, self))
        if a["empty"]: head.addWidget(pill(f"{a['empty']} 个空位", BAD, self))
        head.addStretch(1)
        lay.addLayout(head)

        rec = a.get("rec")
        if a["starred"]:
            if rec and rec.get("four"):
                parts = [f"推荐：4 件 {rec['four']} + 2 件 {' / '.join(rec.get('two') or [])}"]
                if rec.get("main"): parts.append(f"4/5/6 号位主属性 {rec['main']}")
                if rec.get("subs"): parts.append(f"副属性 {rec['subs']}")
                if rec.get("area"): parts.append(f"刷 区域巡防 › {rec['area']}")
                text = " · ".join(parts)
                if rec.get("alt"): text += f"\n备选：{rec['alt']}"
            else:
                text = "推荐套装还在查。"
            lb = BodyLabel(text, self); lb.setWordWrap(True)
            f = lb.font(); f.setPixelSize(12); lb.setFont(f)
            lay.addWidget(lb)

        slots = QHBoxLayout(); slots.setContentsMargins(0, 0, 0, 0); slots.setSpacing(6)
        for n in range(1, 7):
            slots.addWidget(self._slot(n, a["slots"].get(n)), 1)
        lay.addLayout(slots)

    def _slot(self, n: int, d: dict | None) -> QFrame:
        fr = QFrame(self)
        fr.setObjectName("discSlot")
        lay = QVBoxLayout(fr)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(1)
        if not d:
            themed_qss(fr, "#discSlot{border:1px dashed #c4c9d4;border-radius:4px;background:transparent;}",
                       "#discSlot{border:1px dashed #4a5060;border-radius:4px;background:transparent;}")
            fr.setMinimumHeight(96)
            lb = muted(f"{n} 号位\n空", fr); lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(lb)
            return fr
        top = TIER.get(d.get("rarity"), "#9aa1b2")
        themed_qss(fr, f"#discSlot{{border:1px solid #d5d9e2;border-top:3px solid {top};border-radius:4px;background:#f3f4f7;}}",
                   f"#discSlot{{border:1px solid #3a3f4e;border-top:3px solid {top};border-radius:4px;background:#262a35;}}")
        lay.addWidget(muted(f"{n} 号位 · {d.get('rarity') or '?'}级", fr))
        s = StrongBodyLabel(d.get("set") or "未知套装", fr); s.setWordWrap(True)
        f = s.font(); f.setPixelSize(12); s.setFont(f)
        lay.addWidget(s)
        cap = d.get("level_cap") or 15
        lv = CaptionLabel(f"Lv {d.get('level')}/{cap}", fr)
        if (d.get("level") or 0) < cap:
            color_label(lv, BAD)
            f = lv.font(); f.setBold(True); lv.setFont(f)
        lay.addWidget(lv)
        ms = d.get("main_stat") or {}
        if ms:
            m = CaptionLabel(f"{ms.get('stat', '')} {ms.get('value', '')}", fr); m.setWordWrap(True)
            lay.addWidget(m)
        subs = [f"{x.get('stat', '')} {x.get('value', '')}" + (f" +{x['upgrades']}" if x.get("upgrades") else "")
                for x in d.get("sub_stats") or []]
        if subs:
            sl = muted("\n".join(subs), fr, wrap=True)
            lay.addWidget(sl)
        lay.addStretch(1)
        return fr
