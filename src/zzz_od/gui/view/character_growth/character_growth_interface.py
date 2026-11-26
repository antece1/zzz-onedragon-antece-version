"""角色培养：代理人培养规划（原来的网页 docs/index.html 移植成程序界面）。

数据都在 config/planner/state/ 下：每晚 23:00 hoyolab_sync.py 写入代理人等级、驱动盘和电量，
这里改的目标 / 计入 / ★ 优先培养 / 背包数量通过 planner_core.PlannerState.save() 写回，
23:05 的每日推送（tools/planner/push_plan.py）就会按新数据排体力计划。
计算全部在 zzz_od.application.character_growth.planner_core 里，这个文件只管显示。
"""
from __future__ import annotations

import datetime as dt
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    FluentIcon,
    InfoBarIcon,
    PushButton,
    SegmentedWidget,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    qconfig,
)

from one_dragon_qt.widgets.fast_scroll_area import FastScrollArea
from one_dragon_qt.widgets.vertical_scroll_interface import VerticalScrollInterface
from zzz_od.application.character_growth import planner_core as pc
from zzz_od.gui.view.character_growth.growth_widgets import (
    BAD,
    GOOD,
    MUTED,
    AgentCard,
    DayCard,
    DiscAgentCard,
    MaterialTile,
    NoteCard,
    NoWheelSpinBox,
    StatCard,
    accent_bar,
    color_label,
    farm_table,
    flow_container,
    fmt,
    muted,
)

SECTIONS = [("agents", "代理人"), ("materials", "材料"), ("farm", "刷取建议"),
            ("schedule", "每日排期"), ("discs", "驱动盘"), ("notes", "说明")]

SAVE_DELAY_MS = 800   # 改动后多久写文件（和网页一样 0.8 秒）
CALC_DELAY_MS = 120   # 连续修改时合并重算


def push_preview(state: pc.PlannerState, today: dt.date | None = None) -> dict:
    """今晚 23:05 push_plan.py 会推送什么：和它 --dry-run 打印的文字逐行一致。
    排序、电量、兜底本都来自 planner_core（farm_plan / today_budget / day_plan / filler_row），这里只拼文字。"""
    cfg = state.daily_cfg
    if not cfg.get("enabled", True):
        return {"enabled": False, "lines": ["daily push disabled in config.json"], "budget": 0, "picked": []}
    today = today or dt.date.today()
    plan = pc.farm_plan(state, today)
    rows = plan["rows"]
    lines = ["  提示：" + n for n in plan["notes"]]
    budget, budget_src = pc.today_budget(state, today)
    picked, left = pc.day_plan(rows, budget)
    clear_all = cfg.get("clear_all", True)
    lines.append(f"今日排期（{budget_src} {budget}，排完剩 {left}）：")
    for r in picked:
        lines.append(f"  {r['cat']} > {r['typ']}{' > ' + r['mis'] if r['mis'] else ''}  x{r['n']}{'次' if r['cost'] != 20 else '张卡'}  （{r['why']}）")
    if clear_all:
        f = pc.filler_row(rows)
        lines.append(f"  {f['cat']} > {f['typ']} > {f['mis']}  1 张卡一次，最多 12 次，刷到电量不够为止  （{f['why']}）")
    if not picked and not clear_all:
        lines.append("  没有缺口，不改体力计划。")
    return {"enabled": True, "lines": lines, "budget": budget, "budget_src": budget_src, "picked": picked, "left": left}


class _Section(QWidget):
    """一个分区：上面是说明和控件（不重建），下面是内容（数据变了就整块重建）。"""

    def __init__(self, title: str, parent=None):
        QWidget.__init__(self, parent)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 4, 0, 0)
        self.v.setSpacing(8)
        self.title_label = SubtitleLabel(title, self)
        self.sub_label = muted("", self, wrap=True)
        self.v.addWidget(self.title_label)
        self.v.addWidget(self.sub_label)
        self.controls = QHBoxLayout()
        self.controls.setContentsMargins(0, 0, 0, 0)
        self.controls.setSpacing(8)
        self.v.addLayout(self.controls)
        self.body: QWidget | None = None
        self.built = False

    def new_body(self) -> QVBoxLayout:
        if self.body is not None:
            self.v.removeWidget(self.body)
            self.body.hide()
            self.body.deleteLater()
        self.body = QWidget(self)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.v.addWidget(self.body)
        self.built = True
        return lay


class CharacterGrowthInterface(VerticalScrollInterface):

    def __init__(self, ctx=None, parent=None):
        self.ctx = ctx  # 不依赖 ctx 的任何服务；ctx=None 也能用
        VerticalScrollInterface.__init__(
            self,
            content_widget=None,
            object_name='character_growth_interface',
            nav_text_cn='资源调度员',
            nav_icon=FluentIcon.EDUCATION,
            parent=parent,
        )
        self.state: pc.PlannerState | None = None
        self._needs: dict | None = None
        self._plan: dict | None = None
        self._sig = None
        self._loaded_on: dt.date | None = None
        self._show_all: bool = False
        self._current: str = "agents"
        self._stale: set[str] = set()
        self._pending: set[str] = set()
        self._agent_cards: dict[str, AgentCard] = {}
        self._agent_flow = None  # 代理人卡片所在的 FlowLayout（★ 改顺序时直接挪卡片）
        self._tiles: dict[str, MaterialTile] = {}
        self._group_titles: dict[str, StrongBodyLabel] = {}
        self._ui_ready = False

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._flush_saves)
        self._calc_timer = QTimer(self)
        self._calc_timer.setSingleShot(True)
        self._calc_timer.setInterval(CALC_DELAY_MS)
        self._calc_timer.timeout.connect(self._recompute)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._flush_saves)  # 关程序前把还没写的改动写掉
        qconfig.themeChanged.connect(self._on_theme_changed)

    # ------------------------------------------------------------ 布局（第一次显示时才建）
    def get_fixed_top_widget(self) -> QWidget:
        top = QWidget(self)
        lay = QVBoxLayout(top)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(8)

        head = QHBoxLayout(); head.setContentsMargins(0, 0, 0, 0); head.setSpacing(10)
        self.title_label = SubtitleLabel("角色培养", top)
        self.sync_label = muted("", top)
        self.source_label = muted("", top)
        self.save_label = CaptionLabel("", top)
        self.reload_btn = PushButton(FluentIcon.SYNC, "重新读取", top)
        self.reload_btn.setToolTip("重新读 config/planner/state/ 下的文件")
        self.reload_btn.clicked.connect(lambda: self._refresh(force=True))
        head.addWidget(self.title_label)
        head.addWidget(self.sync_label)
        head.addWidget(self.source_label)
        head.addStretch(1)
        head.addWidget(self.save_label)
        head.addWidget(self.reload_btn)
        lay.addLayout(head)

        self.stats_row = QWidget(top)
        stats = QHBoxLayout(self.stats_row); stats.setContentsMargins(0, 0, 0, 0); stats.setSpacing(10)
        self.stat_bat = StatCard("还需电量（不含恶名狩猎）", key=True)
        self.stat_days = StatCard("按每天 240 电量约需天数")
        self.stat_den = StatCard("丁尼缺口")
        self.stat_nh = StatCard("恶名狩猎材料缺口（每周免费 3 次）")
        for c in (self.stat_bat, self.stat_days, self.stat_den, self.stat_nh):
            stats.addWidget(c, 1)
        lay.addWidget(self.stats_row)

        self.segment = SegmentedWidget(top)
        for key, text in SECTIONS:
            self.segment.addItem(routeKey=key, text=text, onClick=lambda _=False, k=key: self._switch(k))
        self.segment.setCurrentItem(self._current)
        seg_row = QHBoxLayout(); seg_row.setContentsMargins(0, 0, 0, 0)
        seg_row.addWidget(self.segment); seg_row.addStretch(1)
        lay.addLayout(seg_row)
        self.stats_row.hide(); self.segment.hide()
        return top

    def get_content_widget(self) -> QWidget:
        box = QWidget()
        self._content_layout = QVBoxLayout(box)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(12)

        self.loading_label = muted("正在读取培养数据…", box)
        self._content_layout.addWidget(self.loading_label)
        self.empty_card = SimpleCardWidget(box)
        el = QVBoxLayout(self.empty_card); el.setContentsMargins(20, 16, 20, 16); el.setSpacing(6)
        self.empty_title = StrongBodyLabel("", self.empty_card)
        self.empty_text = BodyLabel("", self.empty_card); self.empty_text.setWordWrap(True)
        el.addWidget(self.empty_title); el.addWidget(self.empty_text)
        self.empty_card.hide()
        self._content_layout.addWidget(self.empty_card)

        self.sections: dict[str, _Section] = {}
        for key, text in SECTIONS:
            s = _Section(text, box)
            s.hide()
            self.sections[key] = s
            self._content_layout.addWidget(s)
        self._init_section_controls()
        self._content_layout.addStretch(1)
        self._ui_ready = True
        return box

    def _init_section_controls(self) -> None:
        s = self.sections["agents"]
        s.sub_label.setText("点 ★ 设为优先培养：它的材料排在每日电量最前面，并开始刷它的驱动盘。"
                            "左边是当前（每晚 23:00 从 HoYoLAB 同步，只读），右边是目标；技能是去掉影画加成后的等级。")
        self.show_all_box = CheckBox("显示全部代理人", s)
        self.show_all_box.toggled.connect(self._on_show_all)
        self.agent_count_label = muted("", s)
        s.controls.addWidget(self.show_all_box); s.controls.addWidget(self.agent_count_label); s.controls.addStretch(1)

        s = self.sections["materials"]
        s.sub_label.setText("填上背包里的数量，缺口会实时更新。同类材料 3 个低级合成 1 个高级。"
                            "改过的那一项按你填的数算，之前估算的“已刷掉落”和技能提升的校正只对这一项清零，其他材料不受影响。")

        s = self.sections["farm"]
        s.sub_label.setText("只算缺口部分，顺序就是每日推送的顺序：刷取队列 › ★ 代理人 › ★ 驱动盘 › 其他"
                            "（各自按 核心技 › 突破 › 代理人经验 › 技能 › 音擎 › 丁尼）。")

        s = self.sections["schedule"]
        s.sub_label.setText("按优先级把缺口排进每天的电量。★ 代理人的驱动盘每天都刷，不会刷完。")
        s.controls.addWidget(muted("★ 优先角色每天刷驱动盘", s))
        self.disc_runs_spin = NoWheelSpinBox(s)
        self.disc_runs_spin.setRange(0, 10)
        self.disc_runs_spin.valueChanged.connect(self._on_disc_runs)
        s.controls.addWidget(self.disc_runs_spin)
        s.controls.addWidget(muted("次（区域巡防，60 电量一次）", s))
        s.controls.addSpacing(16)
        s.controls.addWidget(muted("预览用每天电量", s))
        self.per_day_spin = NoWheelSpinBox(s)
        self.per_day_spin.setRange(20, 2640)
        self.per_day_spin.setSingleStep(20)
        self.per_day_spin.setToolTip("只影响这里的 14 天预览，不保存；每日推送按 23:00 读到的实际电量排")
        self.per_day_spin.valueChanged.connect(self._on_per_day_preview)
        s.controls.addWidget(self.per_day_spin)
        s.controls.addStretch(1)

        s = self.sections["discs"]
        s.sub_label.setText("只显示计入的代理人。红字是没升满级的盘，副属性后面的 +N 是强化命中次数。")

    # ------------------------------------------------------------ 显示 / 隐藏
    def on_interface_shown(self) -> None:
        VerticalScrollInterface.on_interface_shown(self)  # 第一次显示时建布局（只建空壳）
        QTimer.singleShot(0, self._refresh)  # 先让页面出来，再读数据

    def on_interface_hidden(self) -> None:
        VerticalScrollInterface.on_interface_hidden(self)
        self._flush_saves()

    def _signature(self):
        """会影响显示的文件的修改时间；没变就不用重建界面。"""
        root = pc.repo_root()
        files = []
        for base, _dirs, names in os.walk(pc.state_dir(root)):
            files += [os.path.join(base, n) for n in names if n.endswith(".json")]
        files += [os.path.join(pc.daily_dir(root), "config.json"), os.path.join(pc.daily_dir(root), "ledger.json"),
                  pc.queue_path(root), pc.farm_queue_path(root), pc.charge_plan_path(root)]
        sig = []
        for p in sorted(files):
            try: sig.append((p, os.path.getmtime(p)))
            except OSError: sig.append((p, None))
        return tuple(sig)

    def _refresh(self, force: bool = False) -> None:
        if not self._ui_ready:
            return
        if not self._flush_saves():
            return  # 没保存成功时不重读，免得丢掉这次的改动
        sig = self._signature()
        today = dt.date.today()
        if not force and self.state is not None and sig == self._sig and today == self._loaded_on:
            return
        if not os.path.isdir(pc.state_dir()):
            self._show_empty("还没有培养数据",
                             "没有找到 config/planner/state/ 文件夹。每天 23:00 的 HoYoLAB 同步（tools/planner/hoyolab_sync.py）"
                             "会写入代理人等级、技能、音擎、驱动盘和电量；同步过一次后再打开这个页面就能看到培养规划。")
            return
        try:
            state = pc.PlannerState.load()
        except FileNotFoundError as e:
            self._show_empty("培养数据还不完整",
                             f"缺少文件：{os.path.relpath(e.filename, pc.repo_root()) if e.filename else e}。"
                             "每天 23:00 的 HoYoLAB 同步会补全 config/planner/state/ 下的数据，之后点「重新读取」。")
            return
        except Exception as e:  # 文件格式坏了之类
            self._show_empty("读取培养数据失败", f"{type(e).__name__}: {e}")
            return
        self.state, self._sig, self._loaded_on = state, sig, today
        self._show_main()
        self._recompute(rebuild_all=True)

    def _show_empty(self, title: str, text: str) -> None:
        self.state = None
        self._sig = None
        self.loading_label.hide()
        self.stats_row.hide(); self.segment.hide()
        for s in self.sections.values(): s.hide()
        self.empty_title.setText(title)
        self.empty_text.setText(text)
        self.empty_card.show()
        self.sync_label.setText(""); self.source_label.setText("")

    def _show_main(self) -> None:
        self.loading_label.hide()
        self.empty_card.hide()
        self.stats_row.show(); self.segment.show()
        st = self.state
        self.disc_runs_spin.blockSignals(True); self.disc_runs_spin.setValue(int(st.disc_runs or 0)); self.disc_runs_spin.blockSignals(False)
        self.per_day_spin.blockSignals(True); self.per_day_spin.setValue(int(st.daily_cfg.get("per_day", 240) or 240)); self.per_day_spin.blockSignals(False)
        for k, s in self.sections.items(): s.setVisible(k == self._current)

    # ------------------------------------------------------------ 分区切换
    def _switch(self, key: str) -> None:
        if key == self._current:
            return
        self._current = key
        self.segment.setCurrentItem(key)
        if self.state is None:
            return
        for k, s in self.sections.items(): s.setVisible(k == key)
        if key in self._stale or not self.sections[key].built:
            self._rebuild(key)
        area = self.findChild(FastScrollArea)
        if area is not None:
            area.verticalScrollBar().setValue(0)

    def _rebuild(self, key: str) -> None:
        if self.state is None or self._needs is None:
            return
        self._stale.discard(key)
        getattr(self, f"_build_{key}")()

    # ------------------------------------------------------------ 计算
    def _recompute(self, rebuild_all: bool = False) -> None:
        st = self.state
        if st is None:
            return
        self._needs = pc.compute_needs(st)
        self._plan = pc.farm_plan(st, flags=True)
        self._update_header()
        if rebuild_all:
            self._stale = {k for k, _ in SECTIONS}
            self._rebuild(self._current)
            return
        self._stale |= {k for k, _ in SECTIONS if k not in ("notes", self._current)}
        if self._current == "agents":
            self._update_agent_costs()
        elif self._current == "materials":
            self._update_tiles()
        elif self._current != "notes":
            self._rebuild(self._current)

    def _update_header(self) -> None:
        st, t = self.state, self._needs["totals"]
        self.stat_bat.set_value(fmt(t["battery"]))
        self.stat_days.set_value(f"{t['days']:.1f}", f"按每天 {t['per_day']} 电量约需天数")
        self.stat_den.set_value(fmt(t["den_short"]))
        self.stat_nh.set_value(fmt(t["nh_short"]))
        status = st.status or {}
        when = str(status.get("synced_at") or "").replace("T", " ")[:16]
        bat = status.get("battery") or {}
        text = f"账号同步 {when}" if when else "还没有同步记录"
        if bat.get("current") is not None:
            text += f" · 电量 {bat.get('current')}/{bat.get('max')}"
        self.sync_label.setText(text)
        if st.source == "edited":
            self.source_label.setText(f"当前等级：快照 {st.snap_at or ''}，之后手动改过")
        elif st.source:
            self.source_label.setText(f"当前等级来自 {'HoYoLAB' if st.source == 'hoyolab' else st.source}")
        else:
            self.source_label.setText("")

    # ------------------------------------------------------------ 代理人
    def _build_agents(self) -> None:
        st, s = self.state, self.sections["agents"]
        lay = s.new_body()
        n_on = len([a for a in st.metas if st.on.get(a)])
        self.agent_count_label.setText(f"共 {len(st.metas)} 个代理人，计入 {n_on} 个" + ("" if self._show_all else "（没计入的已隐藏）"))
        ids = pc.agent_order(st, self._show_all)
        self._agent_cards = {}
        self._agent_flow = None
        if not ids:
            lay.addWidget(muted("还没有计入任何代理人。勾选“显示全部代理人”，再勾选要培养的人。", s.body, wrap=True))
            return
        box, flow = flow_container(s.body, fit_width=AgentCard.WIDTH)
        self._agent_flow = flow
        for aid in ids:
            card = AgentCard(aid, st.metas[aid], st.cur[aid], self._target_view(aid), bool(st.on.get(aid)), aid in st.starred, box)
            card.set_cost((self._needs["per_agent"] or {}).get(aid))
            card.star_clicked.connect(self._on_star)
            card.on_toggled.connect(self._on_counted)
            card.target_changed.connect(self._on_target)
            flow.addWidget(card)
            self._agent_cards[aid] = card
        lay.addWidget(box)

    def _target_view(self, aid: str) -> dict:
        """显示用的目标；还没有目标时显示 planner_core 的默认目标（真正改了才写进 targets.json）。"""
        st = self.state
        if aid in st.tgt:
            return st.tgt[aid]
        c = st.cur.get(aid) or {"sk": [1] * 5}
        d = pc.DEFAULT_TARGET
        return {"lv": d["lv"], "sk": [max(a, b) for a, b in zip(c["sk"], d["sk"])], "core": d["core"], "wl": d["wl"]}

    def _update_agent_costs(self) -> None:
        st = self.state
        per = self._needs["per_agent"]
        for aid, card in self._agent_cards.items():
            card.set_cost(per.get(aid))
        n_on = len([a for a in st.metas if st.on.get(a)])
        self.agent_count_label.setText(f"共 {len(st.metas)} 个代理人，计入 {n_on} 个" + ("" if self._show_all else "（没计入的已隐藏）"))

    def _on_show_all(self, checked: bool) -> None:
        self._show_all = bool(checked)
        self._rebuild("agents")

    def _on_star(self, aid: str) -> None:
        self.state.toggle_star(aid)
        self._queue_save("focus", "targets")
        QTimer.singleShot(0, self._reorder_agents)  # ★ 会改变排序，等按钮的信号处理完再挪卡片
        self._calc_timer.start()

    def _reorder_agents(self) -> None:
        """★ 改了以后只更新卡片的 ★ / 计入 显示并按新顺序挪动，不重建（显示全部时重建要半秒多）。
        要显示的代理人变了才整块重建。"""
        st, flow = self.state, self._agent_flow
        if st is None:
            return
        ids = pc.agent_order(st, self._show_all)
        if flow is None or set(ids) != set(self._agent_cards):
            self._rebuild("agents")
            return
        for a, card in self._agent_cards.items():
            card.set_starred(a in st.starred)
            card.set_counted(bool(st.on.get(a)))
        flow.removeAllWidgets()  # 只从布局里拿出来，卡片不删
        for a in ids:
            flow.addWidget(self._agent_cards[a])
        flow.invalidate()
        if flow.parentWidget() is not None:
            flow.parentWidget().updateGeometry()

    def _on_counted(self, aid: str, on: bool) -> None:
        self.state.set_on(aid, on)
        card = self._agent_cards.get(aid)
        if card is not None:
            card.set_counted(on)
        kinds = ["targets"]
        if not on and aid in self.state.starred:
            # 每日推送只认计入的 ★（farm_plan），不计入还留着 ★ 会让界面显示“优先培养”而推送不管它：一起取消 ★
            self.state.toggle_star(aid)
            kinds.append("focus")
            if card is not None:
                card.set_starred(False)  # 和取消计入别的代理人一样，卡片先留在原处，下次重建再排序
        self._queue_save(*kinds)
        self._calc_timer.start()

    def _on_target(self, aid: str, field: str, value: int) -> None:
        self.state.set_target(aid, field, value)
        self._queue_save("targets")
        self._calc_timer.start()

    # ------------------------------------------------------------ 材料
    def _build_materials(self) -> None:
        s = self.sections["materials"]
        lay = s.new_body()
        self._tiles, self._group_titles = {}, {}
        for g in self._needs["groups"]:
            title = StrongBodyLabel(g["title"], s.body)
            color_label(title, MUTED)
            self._group_titles[g["key"]] = title
            lay.addWidget(title)
            box, flow = flow_container(s.body, 8, 8, fit_width=MaterialTile.WIDTH)
            for t in g["tiles"]:
                tile = MaterialTile(g["key"], t, box)
                tile.own_changed.connect(self._on_own)
                flow.addWidget(tile)
                self._tiles[t["name"]] = tile
            lay.addWidget(box)
            lay.addSpacing(6)

    def _update_tiles(self) -> None:
        for g in self._needs["groups"]:
            lb = self._group_titles.get(g["key"])
            if lb is not None:
                lb.setText(g["title"])
            for t in g["tiles"]:
                tile = self._tiles.get(t["name"])
                if tile is not None:
                    tile.update_tile(t)

    def _on_own(self, item: str, value: int) -> None:
        self.state.set_own(item, value)
        self._queue_save("inventory")
        self._calc_timer.start()

    # ------------------------------------------------------------ 刷取建议
    def _build_farm(self) -> None:
        s = self.sections["farm"]
        lay = s.new_body()
        rows = self._plan["rows"]
        lay.addWidget(farm_table(rows, self._needs["nh_rows"], s.body))
        total = self._needs["totals"]["battery"]
        foot = StrongBodyLabel(f"合计（不含恶名狩猎、驱动盘）：{fmt(total)} 电量", s.body)
        lay.addWidget(foot)
        for n in self._plan["notes"]:
            lay.addWidget(muted("提示：" + n, s.body, wrap=True))
        if any(r.get("queue") for r in rows):
            lay.addWidget(muted("“队列”是「代理人升级」「角色技能提升」卡在材料上时记下的（config/agent_upgrade/queue.json），排在最前面。", s.body, wrap=True))

    # ------------------------------------------------------------ 每日排期
    def _build_schedule(self) -> None:
        st, s = self.state, self.sections["schedule"]
        lay = s.new_body()

        # 今晚 23:05 会推送什么（和 push_plan.py --dry-run 打印的一样）
        pv = push_preview(st)
        card = SimpleCardWidget(s.body)
        cl = QVBoxLayout(card); cl.setContentsMargins(0, 0, 0, 12); cl.setSpacing(0)
        cl.addWidget(accent_bar(card))
        inner = QVBoxLayout(); inner.setContentsMargins(16, 10, 16, 0); inner.setSpacing(6)
        head = QHBoxLayout(); head.setContentsMargins(0, 0, 0, 0); head.setSpacing(8)
        title = StrongBodyLabel("今晚 23:05 会推送", card)
        f = title.font(); f.setPixelSize(16); title.setFont(f)
        head.addWidget(title)
        tag = CaptionLabel("预览" if pv["enabled"] else "已停用", card)
        color_label(tag, GOOD if pv["enabled"] else BAD)
        head.addWidget(tag); head.addStretch(1)
        inner.addLayout(head)
        if pv["enabled"]:
            inner.addWidget(muted("每天 23:05 tools/planner/push_plan.py 把下面这一天写进一条龙的体力计划（charge_plan.yml）。"
                                  "这是按现在的数据算的；23:00 同步后电量和等级会更新，到时以当晚的数据为准。", card, wrap=True))
        else:
            inner.addWidget(muted("config/planner/daily/config.json 里 enabled=false，今晚不会改体力计划。", card, wrap=True))
        if st.charge_yml is None and pv["enabled"]:
            warn = CaptionLabel(f"读不到一条龙体力计划 {os.path.relpath(pc.charge_plan_path(), pc.repo_root())}（或没装 PyYAML），推送需要这个文件。", card)
            warn.setWordWrap(True); color_label(warn, BAD)
            inner.addWidget(warn)
        text = BodyLabel("\n".join(pv["lines"]), card)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mono = text.font(); mono.setFamilies(["Consolas", "Microsoft YaHei UI"]); mono.setPixelSize(13); text.setFont(mono)
        inner.addWidget(text)
        cl.addLayout(inner)
        lay.addWidget(card)

        # 14 天排期
        per_day = int(self.per_day_spin.value())
        days = pc.schedule(self._plan["rows"], per_day, days=14)
        lay.addWidget(StrongBodyLabel(f"14 天排期（每天 {per_day} 电量）", s.body))
        box, flow = flow_container(s.body, 8, 8, fit_width=DayCard.WIDTH)
        if not days:
            flow.addWidget(NoteCard("没有要刷的", "目标都够了，电量可以留着。", DayCard.WIDTH, box))
        for d in days:
            flow.addWidget(DayCard(f"第 {d['day']} 天", d["items"], per_day, d["left"], box))
        if days and days[-1]["remain"] > 0:
            flow.addWidget(NoteCard("之后", f"14 天后仍差 {fmt(days[-1]['remain'])} 电量，继续按同样顺序刷。", DayCard.WIDTH, box))
        lay.addWidget(box)

    def _on_per_day_preview(self, _value: int) -> None:
        if self._current == "schedule":
            self._rebuild("schedule")
        else:
            self._stale.add("schedule")

    def _on_disc_runs(self, value: int) -> None:
        if self.state is None:
            return
        self.state.set_disc_runs(value)
        self._queue_save("focus")
        self._calc_timer.start()

    # ------------------------------------------------------------ 驱动盘
    def _build_discs(self) -> None:
        s = self.sections["discs"]
        lay = s.new_body()
        rep = pc.disc_report(self.state, show_all=False)
        taken = str(rep.get("taken_at") or "").replace("T", " ")[:16]
        s.sub_label.setText(f"只显示计入的代理人；{rep['issues']} 个代理人的驱动盘需要处理。红字是没升满级的盘，副属性后面的 +N 是强化命中次数。"
                            + (f" 数据时间 {taken}" if taken else ""))
        if not rep["agents"] and not rep["no_disc"]:
            lay.addWidget(muted("还没有驱动盘数据，或者没有计入的代理人。", s.body))
        for a in rep["agents"]:
            lay.addWidget(DiscAgentCard(a, s.body))
        if rep["no_disc"]:
            lay.addWidget(muted("没装驱动盘：" + "、".join(rep["no_disc"]), s.body, wrap=True))

    # ------------------------------------------------------------ 说明
    def _build_notes(self) -> None:
        s = self.sections["notes"]
        lay = s.new_body()
        e = pc.EST
        notes = [
            "升级消耗（等级、突破、技能、核心技、音擎）已用多个来源核对：B 站 wiki、Game8、Icy Veins、Prydwen。",
            f"实战模拟室每张卡 20 电量，一次最多 5 张。调查专项约 {fmt(e['expA'])} 经验/卡、音擎专项约 {fmt(e['expW'])} 经验/卡、"
            f"赏金专项约 {fmt(e['den'])} 丁尼/卡，都是估算值（表里标“估算”）。",
            f"代理人晋升按每卡 {e['A_per_card']:g} 个 A 级 + {e['B_per_card']:g} 个 B 级估算（只有 Game8 一个来源）。技能芯片和音擎组件的每卡掉落没查到，暂时按同样的量估算。",
            f"专业挑战室 40 电量一次，满级掉 {e['hdd']} 个高维数据（已确认）。恶名狩猎每周 3 次免费奖励，所有 Boss 共用，之后深度追猎 60 电量一次，"
            "掉 1 个 Boss 材料；恶名狩猎不排进每日推送，由「恶名狩猎」应用自己打。",
            f"{pc.CAGE}（技能 11→12 级用）不能用电量刷，来自丽都城募和活动。",
            "音擎组件按代理人自己的职业计算；如果装的是别的职业的音擎，组件种类会不同。",
            "等级经验在每个等级段内按线性估算，误差在一个等级段以内。",
            "代理人等级、技能、核心技、音擎、驱动盘和电量每晚 23:00 从 HoYoLAB 同步，这里只读。背包材料和丁尼 HoYoLAB 不提供，"
            "保持上次读背包的数量，可以在「材料」里改。",
            "「角色技能提升」在强化弹窗里看到的真实数量和升级花掉的材料，会记成背包校正（config/skill_upgrade/bag_adjust.json），"
            "材料格子、刷取建议和每日推送都会算上；重新读背包后清零。",
            "这里改的目标、计入、★ 优先培养、驱动盘次数和背包数量约 0.8 秒后写回 config/planner/state/plan/，只写改过的那个文件；"
            "今晚 23:05 的每日推送会按新数据排体力计划。",
            "计算和每日推送共用 src/zzz_od/application/character_growth/planner_core.py，界面上的数字和推送结果一致。",
        ]
        for n in notes:
            lb = BodyLabel("• " + n, s.body)
            lb.setWordWrap(True)
            lay.addWidget(lb)

    # ------------------------------------------------------------ 保存
    def _queue_save(self, *kinds: str) -> None:
        self._pending.update(kinds)
        self.save_label.setText("待保存…")
        color_label(self.save_label, MUTED)
        self._save_timer.start()

    def _flush_saves(self) -> bool:
        """把排队的改动写进文件。返回 False 表示写失败（改动还留在内存里，下次再试）。"""
        self._save_timer.stop()
        if not self._pending or self.state is None:
            return True
        kinds = [k for k in ("targets", "focus", "inventory", "current") if k in self._pending]
        before = self._signature()
        external = self._sig is not None and before != self._sig  # 读进来之后别的程序改过文件（23:00 同步、技能提升……）
        try:
            written = self.state.save(kinds)
        except Exception as e:
            self.save_label.setText("保存失败")
            color_label(self.save_label, BAD)
            self.save_label.setToolTip(f"{type(e).__name__}: {e}")
            try:
                self.show_info_bar("保存失败", f"{type(e).__name__}: {e}", icon=InfoBarIcon.ERROR, duration=5000)
            except Exception:
                pass
            return False
        self._pending.clear()
        if external:
            # 不能把别人的改动当成自己写的吞掉：保持旧的指纹，并重新读一遍（save 只合并了自己改的字段，其他内容以文件为准）
            QTimer.singleShot(0, lambda: self._refresh(force=True))
        else:
            self._sig = self._signature()  # 只有自己写的文件变了，不算外部变化
        if written:
            self.save_label.setText(f"已保存 {dt.datetime.now():%H:%M:%S} · 今晚 23:05 推送会用")
            self.save_label.setToolTip("\n".join(os.path.relpath(p, pc.repo_root()) for p in written))
            color_label(self.save_label, GOOD)
        else:
            self.save_label.setText("")
        return True

    def _on_theme_changed(self, *_args) -> None:
        # 刷取表的“估算 / 已确认”颜色是建表时按主题取的，换主题后重建
        if self.state is not None and self._needs is not None:
            if self._current == "farm":
                self._rebuild("farm")
            else:
                self._stale.add("farm")
