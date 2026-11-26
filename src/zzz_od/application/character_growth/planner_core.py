"""代理人培养规划的计算核心（纯 Python，不依赖 Qt）。

GUI（角色培养界面）和 tools/planner/push_plan.py（每晚 23:05 的每日推送）共用这一份逻辑，
算法和 docs/index.html 网页一致：
  - 读 config/planner/state/ 下 hoyolab_sync.py 每晚写的账号数据，以及用户改的目标 / 背包 / ★ 优先培养
  - 算每种材料还缺多少（compute_needs），排刷取清单（farm_rows），按每天电量排期（day_plan / schedule）
  - 保存时只写指定的文件，先写临时文件再 os.replace，文件里其他字段原样保留

成本表（SKILL_LV、CORE_ST、PROMO、W_BANDS、W_PROMO、EXP_A、EXP_W）原来在 tools/planner/allocate.py，
现在放在这里，allocate.py 从这里导入。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import tempfile
from typing import Any

# ---------------------------------------------------------------- 成本表（来自 allocate.py）

SKILLS = ["普攻", "闪避", "支援", "特殊技", "连携技"]
CORE_NAMES = ["无", "A", "B", "C", "D", "E", "F"]
# 技能等级 -> (芯片档位, 数量, 丁尼)
SKILL_LV = {2: ("C", 2, 2000), 3: ("C", 3, 3000), 4: ("B", 2, 6000), 5: ("B", 3, 9000), 6: ("B", 4, 12000),
            7: ("B", 6, 18000), 8: ("A", 5, 45000), 9: ("A", 8, 67500), 10: ("A", 10, 90000),
            11: ("A", 12, 112500), 12: ("A", 15, 135000)}
# 核心技等级 -> (高维数据, 恶名狩猎材料, 丁尼, 代理人等级门槛)
CORE_ST = {1: (0, 0, 5000, 15), 2: (2, 0, 12000, 25), 3: (4, 0, 28000, 35), 4: (9, 2, 60000, 45),
           5: (15, 3, 100000, 55), 6: (30, 4, 200000, 60)}
LV_BANDS = [(1, 10, 6000), (10, 20, 24000), (20, 30, 60000), (30, 40, 135000), (40, 50, 225000), (50, 60, 450000)]
# 突破等级 -> (认证章档位, 数量, 丁尼)
PROMO = {10: ("C", 4, 24000), 20: ("B", 12, 56000), 30: ("B", 20, 120000), 40: ("A", 10, 200000), 50: ("A", 20, 400000)}
W_BANDS = {"S": [(1, 10, 4000), (10, 20, 16000), (20, 30, 40000), (30, 40, 90000), (40, 50, 150000), (50, 60, 300000)],
           "A": [(1, 10, 3200), (10, 20, 12800), (20, 30, 32000), (30, 40, 72000), (40, 50, 120000), (50, 60, 240000)],
           "B": [(1, 10, 2400), (10, 20, 9600), (20, 30, 24000), (30, 40, 54000), (40, 50, 90000), (50, 60, 180000)]}
W_PROMO = {"S": {10: ("C", 4, 12000), 20: ("B", 12, 28000), 30: ("B", 20, 60000), 40: ("A", 10, 100000), 50: ("A", 20, 200000)},
           "A": {10: ("C", 3, 9600), 20: ("B", 10, 22400), 30: ("B", 16, 48000), 40: ("A", 8, 80000), 50: ("A", 16, 160000)},
           "B": {10: ("C", 2, 7200), 20: ("B", 7, 16800), 30: ("B", 12, 36000), 40: ("A", 6, 60000), 50: ("A", 12, 120000)}}
# 注意：这里没有 锋御 -> 执锋者（网页和刷取队列里有）。push_plan 的 gaps / ledger 用这一份，保持推送结果和原来一致；
# GUI 的材料格子用下面的 A_SEAL_GUI（和网页一样）。
A_SEAL = {"强攻": "先行者", "击破": "破阵者", "异常": "掌控者", "支援": "统御者", "防护": "捍卫者", "命破": "裁决者"}
A_SEAL_GUI = {**A_SEAL, "锋御": "执锋者"}
EXP_A = {"见习调查员记录": 100, "正式调查员记录": 600, "资深调查员记录": 3000}
EXP_W = {"音擎蓄电池": 100, "变频音擎电源": 600, "音擎能源模块": 3000}
CAGE = "「仓鼠笼」访问器"
DEFAULT_TARGET = {"lv": 60, "sk": [10, 8, 8, 10, 10], "core": 6, "wl": 60}


def seal(sp: str, t: str) -> str: return {"C": f"初阶{sp}认证章", "B": f"高阶{sp}认证章", "A": f"{A_SEAL.get(sp, sp)}认证章"}[t]
def chip(c: str, t: str) -> str: return {"C": f"基础{c}芯片", "B": f"进阶{c}芯片", "A": f"特化{c}芯片"}[t]
def comp(sp: str, t: str) -> str: return {"C": f"{sp}组件", "B": f"增强型{sp}组件", "A": f"特化型{sp}组件"}[t]
def seal_gui(sp: str, t: str) -> str: return {"C": f"初阶{sp}认证章", "B": f"高阶{sp}认证章", "A": f"{A_SEAL_GUI.get(sp, sp)}认证章"}[t]


def exp_between(bands, a, b):
    return sum(e * (min(b, t) - max(a, f)) / (t - f) for f, t, e in bands if min(b, t) > max(a, f))


def load_doc(path: str) -> dict:
    """读 state 里的 JSON；兼容外面包一层 {"data": {...}} 的旧导出格式。"""
    d = json.load(open(path, encoding="utf-8-sig"))
    return d.get("data", d)


def read_json(p: str, default: Any) -> Any:
    try: return json.load(open(p, encoding="utf-8-sig"))
    except FileNotFoundError: return default


# ---------------------------------------------------------------- 排期用的常量（来自 push_plan.py）

# 每张卡 / 每次的估算掉落
EST = {"expA": 12500, "expW": 15800, "den": 21000, "A_per_card": 1.0, "B_per_card": 1.5, "hdd": 5, "nh": 1}
CHIP_STAGE = {"物理": "刚性测试", "引燃": "升温测试", "冰结": "冰结测试", "电击": "电流测试", "风蚀": "风切测试", "以太": "共鸣测试", "流明": "琢光测试"}
ENGINE_STAGE = {"锋御": "锋御耦合试验", "命破": "命破共鸣试验", "强攻": "强攻极片试验", "击破": "击破振膜试验",
                "异常": "异常磁体试验", "支援": "支援分频试验", "防护": "防护音圈试验"}
EC_NAME = {"阿瓦鲁斯": "秽息蚀者·阿瓦鲁斯", "秽蚀·多佩冈亚·狛野真斗": "秽蚀·狛野真斗"}
PRIO = {"core": 1, "seal": 2, "expA": 3, "chip": 4, "expW": 5, "comp": 6, "den": 7}
SEAL_A = {"先行者": "强攻", "破阵者": "击破", "掌控者": "异常", "统御者": "支援", "捍卫者": "防护", "裁决者": "命破", "执锋者": "锋御"}
SET_AREA = {"怪兽与怪客": ["摇摆爵士", "混沌重金属"], "恶拳与凶枪": ["激素朋克", "獠牙重金属"], "猎人与忠犬": ["震星迪斯科", "雷暴重金属"],
            "高塔与巨炮": ["啄木鸟电音", "灵魂摇滚"], "疯子与追随者": ["河豚电音", "炎狱重金属"], "锐牙与钝斧": ["自由蓝调", "极地重金属"],
            "猎手与凶兽": ["混沌爵士", "原始朋克"], "怪兽二重奏": ["折枝剑歌", "静听嘉音"], "游侠与守卫": ["如影相随", "法厄同之歌"],
            "口舌与刀兵": ["云岿如我", "山大王"], "铁律与狂徒": ["拂晓生花", "月光骑士颂"], "诡步与重壁": ["沧浪行歌", "流光咏叹"],
            "伏龙与战车": ["雪兔梦游仙境", "囚徒手记"], "机师与叛甲": ["呼啸沙龙", "拂晓行纪"], "迅脊与裂爪": ["谶羽之誓", "荆棘玫瑰"]}


def ceq(o): return o["C"] + 3 * o["B"] + 9 * o["A"]


def area_of(s): return next((k for k, v in SET_AREA.items() if s in v), None)


# ---------------------------------------------------------------- 路径

def repo_root() -> str:
    """仓库根目录：本文件在 <repo>/src/zzz_od/application/character_growth/ 下。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))


def state_dir(root: str | None = None) -> str:
    return os.path.join(root or repo_root(), "config", "planner", "state")


def daily_dir(root: str | None = None) -> str:
    return os.path.join(root or repo_root(), "config", "planner", "daily")


def charge_plan_path(root: str | None = None) -> str:
    """一条龙的体力计划文件（实例由环境变量 ONEDRAGON_INSTANCE 决定，默认 01）。"""
    return os.path.join(root or repo_root(), "config", os.environ.get("ONEDRAGON_INSTANCE", "01"), "one_dragon", "charge_plan.yml")


def queue_path(root: str | None = None) -> str:
    """升级应用写的刷取队列。"""
    return os.path.join(root or repo_root(), "config", "agent_upgrade", "queue.json")


def farm_queue_path(root: str | None = None) -> str:
    """旧格式的刷取队列，通常不存在。"""
    return os.path.join(daily_dir(root), "farm_queue.json")


def skill_upgrade_dir(root: str | None = None) -> str:
    """角色技能提升自己的数据目录：config/skill_upgrade/（结果、背包校正）"""
    return os.path.join(root or repo_root(), "config", "skill_upgrade")


def bag_adjust_path(root: str | None = None) -> str:
    """角色技能提升对背包的校正（真的花掉的、强化弹窗里看到的真实数量）。"""
    return os.path.join(skill_upgrade_dir(root), "bag_adjust.json")


# 可保存的文件：kind -> state 下的相对路径
DOC_FILES = {"current": ("plan", "current.json"), "targets": ("plan", "targets.json"),
             "inventory": ("plan", "inventory.json"), "focus": ("plan", "focus.json")}


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _clamp(v, lo: int, hi: int) -> int:
    try: v = int(float(v))
    except (TypeError, ValueError): v = lo
    return max(lo, min(hi, v))


def _write_json_atomic(path: str, obj: Any) -> None:
    """先写同目录的临时文件，再 os.replace 覆盖，避免写一半被读到。格式和 hoyolab_sync.py 一样（indent=1）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, indent=1))
        os.replace(tmp, path)
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise


def _own_hash(own: dict) -> str:
    return hashlib.sha1(json.dumps(own, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def inventory_stamp_of(path: str, doc: dict) -> tuple[float | None, dict]:
    """背包"上次读过"的时间戳和界面单项修改记录 (stamp, edits)。
    界面只改几项时会在 inventory.json 里记 read_stamp / edit_hash / edits：own 和 edit_hash 对得上就沿用 read_stamp，
    这样 ledger 和技能提升的校正不会因为改了一项就整体作废；文件被别的工具整体重写过（对不上）就用修改时间，和原来一样。"""
    try: mtime = os.path.getmtime(path)
    except OSError: return None, {}
    if doc.get("read_stamp") is not None and doc.get("edit_hash") == _own_hash(doc.get("own", {})):
        edits = doc.get("edits")
        return float(doc["read_stamp"]), dict(edits) if isinstance(edits, dict) else {}
    return mtime, {}


# ---------------------------------------------------------------- 状态

class PlannerState:
    """规划用到的全部数据。改动用 set_* 方法，save(kinds) 只写改过的那几个文件。"""

    def __init__(self, root: str | None = None) -> None:
        self.root: str = root or repo_root()
        self.metas: dict[str, dict] = {}       # 代理人元数据，按 agents.json 的顺序
        self.cur: dict[str, dict] = {}         # 当前等级 {id: {lv, sk[5], core, wr, wl, wname}}
        self.tgt: dict[str, dict] = {}         # 目标 {id: {lv, sk[5], core, wl}}
        self.on: dict[str, bool] = {}          # 是否计入
        self.own: dict[str, float] = {}        # 背包（上次读背包的数量）
        self.starred: list[str] = []           # ★ 优先培养
        self.disc_runs: int = 2                # ★ 代理人每天刷几次驱动盘
        self.discs: dict = {}                  # discs.json
        self.discs_error: str | None = None    # discs.json 读不了时的原因（驱动盘不影响排期，读不了就当没有）
        self.status: dict = {}                 # status.json（电量 / 同步时间）
        self.daily_cfg: dict = {"per_day": 240, "enabled": True}
        self.ledger: dict = {}                 # daily/ledger.json 原样
        self.queue: dict = {"items": []}       # agent_upgrade/queue.json
        self.farm_queue: dict = {"items": []}  # daily/farm_queue.json（旧格式）
        self.charge_yml: dict | None = None    # 一条龙体力计划（读回昨天刷了多少）
        self.source: str | None = None         # current.json 的 source（hoyolab / edited ...）
        self.snap_at: str | None = None
        self.inventory_stamp: float | None = None  # 上次读背包的时间戳（inventory_stamp_of），ledger 用它判断背包有没有重新读过
        self.inventory_edits: dict[str, dict] = {}  # 读背包之后界面改过的物品 -> {done: 当时 ledger 条数, adjust: 当时的校正}
        self._dirty: dict[str, set] = {k: set() for k in DOC_FILES}

    # ------------------------------------------------ 读取
    @classmethod
    def load(cls, root: str | None = None, charge_plan: bool = True, discs: bool = True) -> "PlannerState":
        """discs=False 时不读 discs.json（每日推送用不到，原来的 push_plan 也不读它）。"""
        s = cls(root)
        sd = state_dir(s.root)
        p = lambda *a: os.path.join(sd, *a)
        cur_doc = load_doc(p("plan", "current.json"))
        s.cur = cur_doc.get("cur", {})
        s.source, s.snap_at = cur_doc.get("source"), cur_doc.get("snapAt")
        tg = load_doc(p("plan", "targets.json"))
        s.tgt, s.on = tg.get("tgt", {}), tg.get("on", {})
        inv = load_doc(p("plan", "inventory.json"))
        s.own = dict(inv.get("own", {}))
        s.inventory_stamp, s.inventory_edits = inventory_stamp_of(p("plan", "inventory.json"), inv)
        s.metas = {m["id"]: m for m in load_doc(p("meta", "agents.json")).get("list", [])}
        focus = load_doc(p("plan", "focus.json")) if os.path.exists(p("plan", "focus.json")) else {}
        s.starred = list(focus.get("starred", []))
        s.disc_runs = int(focus.get("disc_runs", 2) or 0)
        if discs:
            try:
                s.discs = read_json(p("discs.json"), {})
            except (ValueError, OSError) as e:  # 同步写到一半被打断之类：只影响驱动盘面板
                s.discs, s.discs_error = {}, f"{type(e).__name__}: {e}"
        s.status = read_json(p("status.json"), {})
        s.daily_cfg = read_json(os.path.join(daily_dir(s.root), "config.json"), {"per_day": 240, "enabled": True})
        s.ledger = read_json(os.path.join(daily_dir(s.root), "ledger.json"), {})
        s.queue = read_json(queue_path(s.root), {"items": []})
        s.farm_queue = read_json(farm_queue_path(s.root), {"items": []})
        if charge_plan:
            s.charge_yml = load_charge_plan(s.root)
        return s

    # ------------------------------------------------ 修改
    def _ensure(self, agent_id: str) -> None:
        c = self.cur.setdefault(agent_id, {"lv": 1, "sk": [1, 1, 1, 1, 1], "core": 0, "wr": "-", "wl": 1})
        if agent_id not in self.tgt:
            self.tgt[agent_id] = {"lv": DEFAULT_TARGET["lv"], "sk": [max(a, b) for a, b in zip(c["sk"], DEFAULT_TARGET["sk"])],
                                  "core": DEFAULT_TARGET["core"], "wl": DEFAULT_TARGET["wl"]}
            self._dirty["targets"].add(agent_id)
        self.on.setdefault(agent_id, False)

    @staticmethod
    def _apply(o: dict, field: str, value) -> None:
        if field in ("lv", "wl"): o[field] = _clamp(value, 1, 60)
        elif field == "core": o["core"] = _clamp(value, 0, 6)
        elif field == "wr":
            v = str(value).upper() if value is not None else "-"
            o["wr"] = v if v in ("S", "A", "B") else "-"
        elif re.fullmatch(r"sk[0-4]", field):
            sk = list(o.get("sk") or [1, 1, 1, 1, 1]); sk[int(field[2])] = _clamp(value, 1, 12); o["sk"] = sk
        else:
            raise ValueError(f"未知字段：{field}")

    def set_target(self, agent_id: str, field: str, value) -> None:
        """field: lv / core / wl / sk0..sk4"""
        if field == "wr": raise ValueError("目标里没有音擎稀有度")
        self._ensure(agent_id)
        self._apply(self.tgt[agent_id], field, value)
        self._dirty["targets"].add(agent_id)

    def set_current(self, agent_id: str, field: str, value) -> None:
        """field: lv / core / wl / wr / sk0..sk4。手动改过后 source 记为 edited（网页同样处理）。"""
        self._ensure(agent_id)
        self._apply(self.cur[agent_id], field, value)
        self._dirty["current"].add(agent_id)
        if self.source in ("snapshot", "hoyolab"):
            self.source = "edited"
            self._dirty["current"].add("__source__")

    def set_on(self, agent_id: str, on: bool) -> None:
        self._ensure(agent_id)
        self.on[agent_id] = bool(on)
        self._dirty["targets"].add(agent_id)

    def toggle_star(self, agent_id: str) -> bool:
        """切换 ★ 优先培养；设为 ★ 时同时计入。返回切换后是否为 ★。"""
        if agent_id in self.starred:
            self.starred = [x for x in self.starred if x != agent_id]
            starred = False
        else:
            self.starred = self.starred + [agent_id]
            self.set_on(agent_id, True)
            starred = True
        self._dirty["focus"].add("starred")
        return starred

    def set_disc_runs(self, n: int) -> None:
        self.disc_runs = _clamp(n, 0, 10)
        self._dirty["focus"].add("disc_runs")

    def set_own(self, item: str, count) -> None:
        try: v = max(0.0, float(count))
        except (TypeError, ValueError): v = 0.0
        self.own[item] = int(v) if v.is_integer() else v
        self._dirty["inventory"].add(item)

    def dirty_kinds(self) -> list[str]:
        return [k for k, v in self._dirty.items() if v]

    # ------------------------------------------------ 保存
    def save(self, kinds: list[str]) -> list[str]:
        """只写 kinds 里点名、且确实改过的文件（targets / focus / inventory / current）。
        写之前重新读一遍文件，只把改过的代理人 / 物品合并进去，其他字段（包括夜里同步刚写的）原样保留。
        返回实际写入的文件路径。"""
        written = []
        for kind in kinds:
            if kind not in DOC_FILES: raise ValueError(f"不能保存：{kind}")
            dirty = self._dirty[kind]
            if not dirty: continue
            path = os.path.join(state_dir(self.root), *DOC_FILES[kind])
            raw = read_json(path, {})
            doc = raw["data"] if isinstance(raw.get("data"), dict) else raw  # 兼容 {"data": {...}}
            if kind == "targets":
                tgt, on = doc.setdefault("tgt", {}), doc.setdefault("on", {})
                for aid in dirty:
                    if aid in self.tgt: tgt[aid] = self.tgt[aid]
                    if aid in self.on: on[aid] = self.on[aid]
            elif kind == "current":
                cur = doc.setdefault("cur", {})
                for aid in dirty:
                    if aid == "__source__": doc["source"] = self.source
                    elif aid in self.cur: cur[aid] = self.cur[aid]
            elif kind == "inventory":
                # 只改了几项，不算重新读背包：保留磁盘上这份的读背包时间戳，改过的物品记下当时的 ledger 条数和校正，
                # 之后只有这几项不再加之前估算的掉落 / 校正（填的就是实际数量），其他物品照常
                stamp, edits = inventory_stamp_of(path, doc)
                same = stamp is not None and stamp == self.inventory_stamp
                done_n = len(resolve_ledger(self)["done"]) if same else 0
                adjust = load_bag_adjust(self, raw=True) if same else {}
                own = doc.setdefault("own", {})
                for item in dirty:
                    own[item] = self.own.get(item, 0)
                    edits[item] = {"done": done_n, "adjust": adjust.get(item, 0.0), "at": _now_iso()}
                if stamp is not None:
                    doc["read_stamp"], doc["edits"], doc["edit_hash"] = stamp, edits, _own_hash(own)
            elif kind == "focus":
                if "starred" in dirty: doc["starred"] = list(self.starred)
                if "disc_runs" in dirty: doc["disc_runs"] = self.disc_runs
            doc["updated_at"] = _now_iso()
            _write_json_atomic(path, raw)
            if kind == "inventory":
                self.inventory_stamp, self.inventory_edits = inventory_stamp_of(path, doc)
            dirty.clear()
            written.append(path)
        return written


def load_charge_plan(root: str | None = None) -> dict | None:
    """读一条龙体力计划；没有 PyYAML 或文件不存在时返回 None。"""
    try:
        import yaml
        with open(charge_plan_path(root), encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (ImportError, OSError):
        return None


# ---------------------------------------------------------------- ledger（上次读背包之后刷到的东西）

def collect_done(yml, tags):
    """Read back how many cards/runs of our own plan items OneDragon finished."""
    done = []
    for p in (yml or {}).get("plan_list") or []:
        tag = tags.get(p.get("plan_id"))
        if tag and p.get("run_times"):
            done.append({"kind": tag["kind"], "key": tag.get("key"), "cards": p["run_times"] * tag.get("cards_per_run", 1),
                         "stage": f'{p["category_name"]}/{p["mission_type_name"]}/{p.get("mission_name")}'})
    return done


def resolve_ledger(state: PlannerState, today: dt.date | None = None) -> dict:
    """push_plan 的 ledger 规则：背包重新读过（inventory_stamp 变了；界面只改几项不算）就从空的开始；
    上次推送不是今天，就把体力计划里昨天刷完的次数记进来。返回新的 dict，不改 state.ledger。"""
    today = today or dt.date.today()
    ledger = dict(state.ledger)
    if "done" in ledger: ledger["done"] = list(ledger["done"])
    if ledger.get("state_stamp") != state.inventory_stamp:  # new export from main: start a fresh ledger
        ledger = {"state_stamp": state.inventory_stamp, "done": []}
    if ledger.get("pushed_on") and ledger.get("pushed_on") != today.isoformat():
        ledger["done"] = ledger.get("done", []) + collect_done(state.charge_yml, ledger.get("tags", {}))  # yesterday's results, only for items we wrote
    return ledger


def _ledger_gains(e: dict) -> list[tuple[str, float]]:
    """ledger 里一条记录估算掉落的 [(物品, 数量)]"""
    k, n, key = e["kind"], e["cards"], e.get("key")
    if k == "expA": return [("资深调查员记录", n * EST["expA"] / 3000)]
    if k == "expW": return [("音擎能源模块", n * EST["expW"] / 3000)]
    if k == "den": return [("丁尼", n * EST["den"])]
    if k in ("seal", "chip", "comp"):
        f = {"seal": seal, "chip": chip, "comp": comp}[k]
        return [(f(key, "A"), n * EST["A_per_card"]), (f(key, "B"), n * EST["B_per_card"])]
    if k == "core": return [(key, n * EST["hdd"])]
    if k == "nh": return [(key, n * EST["nh"])]
    return []


def apply_ledger(own, ledger, edits: dict | None = None):
    """Add the estimated drops of runs finished since the state export.
    edits：界面改过的物品 -> {"done": 改的时候 ledger 已有几条}，这几条的掉落已经算在填的数量里，不再加。"""
    edits = edits or {}
    for i, e in enumerate(ledger.get("done", [])):
        for item, v in _ledger_gains(e):
            if item in edits and i < int((edits[item] or {}).get("done", 0) or 0): continue
            own[item] = own.get(item, 0) + v


def pending_done(state: PlannerState, ledger: dict, today: dt.date | None = None) -> list[dict]:
    """今晚已经推送、还没记进 ledger 的刷本次数（明天推送时才记进去）；只有角色技能提升用，好用上当晚刷到的材料。"""
    today = today or dt.date.today()
    if ledger.get("pushed_on") != today.isoformat():
        return []  # 上次推送不是今天：体力计划里的次数已经由 resolve_ledger 记进 done 了
    return collect_done(state.charge_yml, ledger.get("tags", {}))


def load_bag_adjust(state: PlannerState, raw: bool = False) -> dict[str, float]:
    """角色技能提升在上次读背包之后对背包的校正 {物品: 差值}（花掉的、强化弹窗里看到的真实数量和估算的差）。
    背包重新读过（inventory_stamp 变了）就作废；raw=False 时扣掉界面改那一项时已有的校正（填的就是实际数量）。"""
    try:
        doc = read_json(bag_adjust_path(state.root), {})
    except (ValueError, OSError):
        return {}
    if not isinstance(doc, dict) or doc.get("inventory_stamp") != state.inventory_stamp:
        return {}
    out = {}
    for k, v in (doc.get("adjust") or {}).items():
        try: out[k] = float(v)
        except (TypeError, ValueError): continue
    if not raw:
        for k in list(out):
            base = (state.inventory_edits.get(k) or {}).get("adjust")
            if base is not None: out[k] -= float(base)
    return out


def effective_own(state: PlannerState, today: dt.date | None = None, pending: bool = False) -> dict:
    """背包 + ledger 里估算的掉落 + 角色技能提升的校正（排期用这个）。
    pending=True 时再加上今晚已推送、还没记进 ledger 的刷本（角色技能提升用）。"""
    own = dict(state.own)
    ledger = resolve_ledger(state, today)
    apply_ledger(own, ledger, state.inventory_edits)
    if pending:
        apply_ledger(own, {"done": pending_done(state, ledger, today)})
    for k, v in load_bag_adjust(state).items():
        own[k] = max(0, own.get(k, 0) + v)
    return own


def adjusted_own(state: PlannerState) -> dict:
    """背包 + 角色技能提升的校正（不加估算掉落）：界面「材料」里显示和编辑的数量。"""
    own = dict(state.own)
    for k, v in load_bag_adjust(state).items():
        own[k] = max(0, own.get(k, 0) + v)
    return own


def observe_bag(state: PlannerState, seen: dict[str, float], today: dt.date | None = None, why: str = "") -> dict[str, float]:
    """记下游戏里看到的真实数量（强化弹窗的「有」，升级后的剩余）：调整校正，使 effective_own(pending=True) 正好等于它。
    以后刷到的（ledger）照常往上加；背包重新读过后作废。返回写进文件的校正。"""
    base = dict(state.own)
    ledger = resolve_ledger(state, today)
    apply_ledger(base, ledger, state.inventory_edits)
    apply_ledger(base, {"done": pending_done(state, ledger, today)})
    adjust = load_bag_adjust(state, raw=True)
    try:
        doc = read_json(bag_adjust_path(state.root), {})
    except (ValueError, OSError):
        doc = {}
    seen_doc = dict(doc.get("seen") or {}) if isinstance(doc, dict) and doc.get("inventory_stamp") == state.inventory_stamp else {}
    now = _now_iso()
    for item, count in seen.items():
        b = float((state.inventory_edits.get(item) or {}).get("adjust", 0) or 0)
        adjust[item] = float(count) - float(base.get(item, 0)) + b
        seen_doc[item] = {"count": count, "at": now, "why": why}
    _write_json_atomic(bag_adjust_path(state.root), {
        "inventory_stamp": state.inventory_stamp, "adjust": adjust, "seen": seen_doc, "updated_at": now,
        "_说明": "角色技能提升在上次读背包之后对背包的校正：adjust = 游戏里看到的数量 − 当时的估算（背包 + 刷本估算）；"
                 "每日推送、角色培养和技能提升都会加上它，背包重新读过后作废"})
    return adjust


# ---------------------------------------------------------------- 刷取清单（push_plan 的算法，行为不变）

def gaps(cur_all, tgt_all, on, metas, own, only=None):
    need = {"expA": 0, "expW": 0, "den": 0, "seal": {}, "chip": {}, "comp": {}, "hdd": {}}
    def add(d, k, t, n): d.setdefault(k, {"C": 0, "B": 0, "A": 0})[t] += n
    for aid, m in metas.items():
        if not on.get(aid) or aid not in cur_all or aid not in tgt_all: continue
        if only is not None and aid not in only: continue
        c, g = cur_all[aid], tgt_all[aid]
        lv0, lv1 = c["lv"], max(c["lv"], g["lv"])
        need["expA"] += exp_between(LV_BANDS, lv0, lv1)
        for at, (t, n, d) in PROMO.items():
            if lv0 <= at < lv1: add(need["seal"], m["sp"], t, n); need["den"] += d
        for i in range(5):
            for lv in range(c["sk"][i] + 1, max(c["sk"][i], g["sk"][i]) + 1):
                t, n, d = SKILL_LV[lv]; add(need["chip"], m["chip"], t, n); need["den"] += d
        for s in range(c["core"] + 1, max(c["core"], g["core"]) + 1):
            h, _, d, _ = CORE_ST[s]; need["hdd"][m["hdd"]] = need["hdd"].get(m["hdd"], 0) + h; need["den"] += d
        wr = c.get("wr", "-")
        if wr and wr != "-":
            wl0, wl1 = c["wl"], max(c["wl"], g["wl"])
            need["expW"] += exp_between(W_BANDS[wr], wl0, wl1)
            for at, (t, n, d) in W_PROMO[wr].items():
                if wl0 <= at < wl1: add(need["comp"], m["sp"], t, n); need["den"] += d
    rows = []
    have = lambda tbl: sum(own.get(n, 0) * v for n, v in tbl.items())
    s = need["expA"] - have(EXP_A)
    if s > 0: rows.append(dict(kind="expA", cat="实战模拟室", typ="基础材料", mis="调查专项", cost=20, n=math.ceil(s / EST["expA"]), why="代理人经验"))
    s = need["expW"] - have(EXP_W)
    if s > 0: rows.append(dict(kind="expW", cat="实战模拟室", typ="基础材料", mis="音擎专项", cost=20, n=math.ceil(s / EST["expW"]), why="音擎经验"))
    for kind, f, typ, stage in (("seal", seal, "代理人晋升", lambda k: k + "演练"),
                                ("chip", chip, "代理人技能", lambda k: CHIP_STAGE.get(k)),
                                ("comp", comp, "音擎改装", lambda k: ENGINE_STAGE.get(k))):
        for k, nd in need[kind].items():
            h = {t: own.get(f(k, t), 0) for t in "CBA"}
            short = ceq(nd) - ceq(h)
            if short > 0 and stage(k):
                rows.append(dict(kind=kind, key=k, cat="实战模拟室", typ=typ, mis=stage(k), cost=20,
                                 n=math.ceil(short / (9 * EST["A_per_card"] + 3 * EST["B_per_card"])), why=f"{typ}：{k}"))
    boss = {m["hdd"]: EC_NAME.get(m.get("ecBoss"), m.get("ecBoss")) for m in metas.values() if m.get("hdd")}
    for name, n in need["hdd"].items():
        s = n - own.get(name, 0)
        if s > 0 and boss.get(name):
            rows.append(dict(kind="core", key=name, cat="专业挑战室", typ=boss[name], mis=None, cost=40, n=math.ceil(s / EST["hdd"]), why="核心技：" + name))
    s = need["den"] - own.get("丁尼", 0)
    if s > 0: rows.append(dict(kind="den", cat="实战模拟室", typ="基础材料", mis="赏金专项", cost=20, n=math.ceil(s / EST["den"]), why="丁尼"))
    rows.sort(key=lambda r: PRIO[r["kind"]])  # stable: ★ rows are put in front by the caller afterwards
    return rows


def queue_rows(queue, own, metas):
    """Turn queued materials (written by upgrade.py when a step lacks materials) into farming rows.
    Returns (rows, notes, remaining_items)."""
    rows, notes, keep, ceq_need = [], [], [], {}
    boss = {m["hdd"]: EC_NAME.get(m.get("ecBoss"), m.get("ecBoss")) for m in metas.values() if m.get("hdd")}
    # 恶名狩猎材料 -> Boss：体力计划可以花 60 电量打恶名狩猎（charge_plan 的「恶名狩猎」分类），每周 3 次免费之外也能刷
    nh_boss = {m["nh"]: m["nhBoss"] for m in metas.values() if m.get("nh") and m.get("nhBoss")}
    tierv = {"C": 1, "B": 3, "A": 9}
    for it in queue.get("items", []):
        name, need = it.get("item", ""), float(it.get("need") or 0)
        if own.get(name, 0) >= need: continue  # covered now: drop from the queue
        keep.append(it)
        m = re.fullmatch(r"(基础|进阶|特化)(.+)芯片", name)
        if m and CHIP_STAGE.get(m.group(2)):
            t = {"基础": "C", "进阶": "B", "特化": "A"}[m.group(1)]
            k = ("实战模拟室", "代理人技能", CHIP_STAGE[m.group(2)], "chip", m.group(2))
            ceq_need[k] = ceq_need.get(k, 0) + (need - own.get(name, 0)) * tierv[t]; continue
        m = re.fullmatch(r"(初阶|高阶)(.+)认证章", name) or re.fullmatch(r"(先行者|破阵者|掌控者|统御者|捍卫者|裁决者|执锋者)认证章", name)
        if m:
            sp = m.group(2) if m.lastindex == 2 else SEAL_A[m.group(1)]
            t = {"初阶": "C", "高阶": "B"}.get(m.group(1), "A")
            k = ("实战模拟室", "代理人晋升", sp + "演练", "seal", sp)
            ceq_need[k] = ceq_need.get(k, 0) + (need - own.get(name, 0)) * tierv[t]; continue
        m = re.fullmatch(r"(增强型|特化型)?(.+)组件", name)
        if m and ENGINE_STAGE.get(m.group(2)):
            t = {"增强型": "B", "特化型": "A"}.get(m.group(1) or "", "C")
            k = ("实战模拟室", "音擎改装", ENGINE_STAGE[m.group(2)], "comp", m.group(2))
            ceq_need[k] = ceq_need.get(k, 0) + (need - own.get(name, 0)) * tierv[t]; continue
        short = need - own.get(name, 0)
        if name in boss:
            rows.append(dict(kind="core", key=name, cat="专业挑战室", typ=boss[name], mis=None, cost=40, n=math.ceil(short / EST["hdd"]), why=f"队列：{it.get('for', '')} 缺 {name}"))
        elif name in nh_boss:
            rows.append(dict(kind="nh", key=name, cat="恶名狩猎", typ=nh_boss[name], mis=None, cost=60, n=math.ceil(short / EST["nh"]), why=f"队列：{it.get('for', '')} 缺 {name}"))
        elif name == "丁尼":
            rows.append(dict(kind="den", cat="实战模拟室", typ="基础材料", mis="赏金专项", cost=20, n=math.ceil(short / EST["den"]), why=f"队列：{it.get('for', '')} 缺丁尼"))
        elif name in EXP_A:
            rows.append(dict(kind="expA", cat="实战模拟室", typ="基础材料", mis="调查专项", cost=20, n=math.ceil(short * EXP_A[name] / EST["expA"]), why=f"队列：{it.get('for', '')} 缺经验"))
        elif name in EXP_W:
            rows.append(dict(kind="expW", cat="实战模拟室", typ="基础材料", mis="音擎专项", cost=20, n=math.ceil(short * EXP_W[name] / EST["expW"]), why=f"队列：{it.get('for', '')} 缺音擎经验"))
        else:
            notes.append(f"{name} 还缺 {short:g}（{it.get('for', '')}），不能用电量刷：恶名狩猎每周免费 / 丽都城募 / 活动")
    per_card = 9 * EST["A_per_card"] + 3 * EST["B_per_card"]
    for (cat, typ, mis, kind, key), c in ceq_need.items():
        rows.append(dict(kind=kind, key=key, cat=cat, typ=typ, mis=mis, cost=20, n=math.ceil(c / per_card), why=f"队列：{typ} {key}"))
    return rows, notes, keep


def farm_plan(state: PlannerState, today: dt.date | None = None, flags: bool = False) -> dict:
    """push_plan 的完整排序结果（flags=True 时每行另加 queue / star / disc 标记）：
    {"rows": 最终顺序的刷取行, "notes": 队列里不能用电量刷的提示,
     "queue_keep": queue.json 里还没够的条目, "own": 排期用的背包（含 ledger 估算）, "stars": 生效的 ★ 代理人}
    行的顺序：刷取队列 > ★ 代理人自己的缺口 > ★ 驱动盘（区域巡防）> 其他缺口（各自按 核心技 > 突破 > 经验 > 技能 > 音擎 > 丁尼）。"""
    cur, tgt, on, metas = state.cur, state.tgt, state.on, state.metas
    own = effective_own(state, today)
    rows = gaps(cur, tgt, on, metas, own)
    # ★ 优先培养：它自己的缺口排最前，再加每天固定次数的驱动盘（区域巡防）
    stars = [s for s in state.starred if on.get(s) and s in metas]
    if stars:
        names = "、".join(metas[s]["name"] for s in stars)
        srows = gaps(cur, tgt, on, metas, own, only=set(stars))
        key = lambda r: (r["cat"], r["typ"], r["mis"])
        sn = {key(r): r["n"] for r in srows}
        rows = [dict(r, n=r["n"] - sn.get(key(r), 0)) for r in rows]
        rows = [r for r in rows if r["n"] > 0]
        disc = []
        for s in stars:
            ds = metas[s].get("discSets") or {}
            area = area_of(ds.get("four", ""))
            runs = int(state.disc_runs or 0)
            if area and runs > 0:
                disc.append(dict(kind="disc", key=ds["four"], cat="区域巡防", typ=area, mis=None, cost=60, n=runs,
                                 why=f"★{metas[s]['name']}：驱动盘 {ds['four']}"))
        rows = [dict(r, why=f"★{names}：{r['why']}") for r in srows] + disc + rows
    # 刷取队列：已确认的升级卡在材料上，排在最前面
    queue = state.queue
    # upgrade.py 写的队列（fork 的格式：material / need / for_step），只读不改
    fq = state.farm_queue
    extra = [{"item": x.get("material"), "need": x.get("need"), "for": f'{x.get("name_cn", "")} {x.get("for_step", "")}'.strip()}
             for x in fq.get("items", []) if x.get("material")]
    qrows, qnotes, qkeep = queue_rows({"items": queue.get("items", []) + extra}, own, metas)
    qkeep = [x for x in qkeep if x not in extra]  # only queue.json is rewritten here
    if qrows:
        key = lambda r: (r["cat"], r["typ"], r["mis"])
        qn = {}
        for r in qrows: qn[key(r)] = qn.get(key(r), 0) + r["n"]
        rows = [dict(r, n=r["n"] - qn.get(key(r), 0)) if r["kind"] != "disc" else r for r in rows]
        rows = qrows + [r for r in rows if r["n"] > 0]
    # 限时双倍活动（config.json 的 boosts）：活动期间每天先刷几张卡，排在刷取队列前面
    brows = boost_rows(rows, state.daily_cfg.get("boosts") or [])
    if brows:
        key = lambda r: (r["cat"], r["typ"], r["mis"])
        used = {}
        for r in brows: used[key(r)] = used.get(key(r), 0) + r["n"]
        rest = []
        for r in rows:
            k = key(r)
            if r["kind"] != "disc" and used.get(k, 0) > 0:
                take = min(used[k], r["n"]); used[k] -= take
                if r["n"] - take > 0: rest.append(dict(r, n=r["n"] - take))
            else:
                rest.append(r)
        rows = brows + rest
    if flags:  # 给 GUI 用的标记；push_plan 不加（ledger 的 last_push 原样保存行内容）
        qset = {id(r) for r in qrows}
        rows = [dict(r, queue=id(r) in qset, disc=r["kind"] == "disc",
                     star=r["kind"] == "disc" or (id(r) not in qset and r["why"].startswith("★"))) for r in rows]
    return {"rows": rows, "notes": qnotes, "queue_keep": qkeep, "own": own, "stars": stars}


def active_boosts(boosts: list[dict], now: dt.datetime | None = None) -> list[dict]:
    """还在活动期内的双倍活动（until 是带时区的 ISO 时间；格式不对的忽略）"""
    now = now or dt.datetime.now().astimezone()
    out = []
    for b in boosts:
        try:
            until = dt.datetime.fromisoformat(str(b["until"]))
        except (KeyError, ValueError):
            continue
        if until.tzinfo is None:
            until = until.astimezone()
        if now < until and int(b.get("cards_per_day") or 0) > 0:
            out.append(b)
    return out


def boost_rows(rows: list[dict], boosts: list[dict], now: dt.datetime | None = None) -> list[dict]:
    """
    双倍活动每天要先刷的卡（只支持实战模拟室，20 电量一张）。
    先用清单里同一 cat/typ 的缺口行（按原来的顺序，例如 调查专项 再 赏金专项），不够 cards_per_day 时用 fallback_mis 补满。
    """
    out = []
    for b in active_boosts(boosts, now):
        cat, typ, left = b.get("cat", "实战模拟室"), b.get("typ"), int(b["cards_per_day"])
        why = f"{b.get('name', '双倍活动')}（每天前 {left} 张双倍，到 {str(b['until'])[:16]}）"
        for r in rows:
            if left <= 0: break
            if r["cat"] == cat and r["typ"] == typ and r["cost"] == 20 and r["kind"] != "disc":
                n = min(left, r["n"]); left -= n
                out.append(dict(r, n=n, why=f"{why}：{r['why']}"))
        if left > 0 and b.get("fallback_mis"):
            fb = b["fallback_mis"]
            kind = {"调查专项": "expA", "音擎专项": "expW", "赏金专项": "den"}.get(fb, "den")
            out.append(dict(kind=kind, key=None, cat=cat, typ=typ, mis=fb, cost=20, n=left, why=why))
    return out


def farm_rows(state: PlannerState, today: dt.date | None = None) -> list[dict]:
    """最终优先顺序的刷取行。每行：kind,key,cat,typ,mis,cost,n,why + queue/star/disc 标记。"""
    return farm_plan(state, today, flags=True)["rows"]


def filler_row(rows: list[dict]) -> dict:
    """清体力兜底本：清单里第一个 20 电量的本，没有就刷丁尼。"""
    base = next((r for r in rows if r["cost"] == 20), None) or dict(kind="den", cat="实战模拟室", typ="基础材料", mis="赏金专项", cost=20, n=1, why="丁尼")
    return dict(base, why="清体力兜底：" + base["why"])


# ---------------------------------------------------------------- 排期

def day_one(rows, budget):
    picked = []
    for r in rows:
        if budget < r["cost"]: continue
        n = min(r["n"], budget // r["cost"])
        if n: picked.append({**r, "n": n}); budget -= n * r["cost"]
    return picked, budget


def day_plan(rows: list[dict], budget: int) -> tuple[list[dict], int]:
    """按顺序把一天的电量装满（= push_plan.day_one）。返回 (picked 行，n 为当天次数/卡数, 剩余电量)。"""
    return day_one(rows, budget)


def schedule(rows: list[dict], per_day: int, days: int = 14) -> list[dict]:
    """多天排期预览：每天按 rows 的顺序装满 per_day 电量；驱动盘行每天都刷，不会刷完。
    返回 [{day, items:[{row, n}], left: 当天剩余电量, remain: 这天之后还差的电量}]，所有材料行刷完就停。"""
    q = [{"row": r, "left": r["n"]} for r in rows]
    out = []
    for day in range(1, days + 1):
        if not any(x["left"] > 0 and x["row"]["kind"] != "disc" for x in q): break
        budget, items = int(per_day), []
        for x in q:
            r = x["row"]; disc = r["kind"] == "disc"
            left = r["n"] if disc else x["left"]
            if left <= 0 or budget < r["cost"]: continue
            n = min(left, budget // r["cost"])
            if not disc: x["left"] -= n
            budget -= n * r["cost"]
            items.append({"row": r, "n": n})
        if not items: break
        out.append({"day": day, "items": items, "left": budget,
                    "remain": sum(x["left"] * x["row"]["cost"] for x in q if x["row"]["kind"] != "disc")})
    return out


def today_budget(state: PlannerState, today: dt.date | None = None) -> tuple[int, str]:
    """每日清体力：按 23:00 同步到的实际电量排；同步不是今天的就用 config 里的 per_day。"""
    today = today or dt.date.today()
    budget, budget_src = int(state.daily_cfg.get("per_day", 240)), "config 每天电量"
    st = state.status or {}
    bat = (st.get("battery") or {}).get("current")
    if isinstance(bat, int) and str(st.get("synced_at", ""))[:10] == today.isoformat():
        budget, budget_src = bat, f"HoYoLAB {str(st['synced_at'])[11:16]} 读到的电量"
    return budget, budget_src


# ---------------------------------------------------------------- 给 GUI 的显示数据

def row_where(r: dict) -> str:
    """关卡路径，例如 实战模拟室 › 基础材料 › 调查专项"""
    return " › ".join(x for x in (r.get("cat"), r.get("typ"), r.get("mis")) if x)


def row_drop(r: dict) -> str:
    """每张卡 / 每次的大致掉落（网页“掉落”一列）。"""
    k = r.get("kind")
    if k == "expA": return f"约 {EST['expA']:,} 经验/卡"
    if k == "expW": return f"约 {EST['expW']:,} 经验/卡"
    if k == "den": return f"约 {EST['den']:,} 丁尼/卡"
    if k in ("seal", "chip", "comp"): return "约 1 个 A + 1.5 个 B"
    if k == "core": return f"{EST['hdd']} 个"
    if k == "nh": return f"约 {EST['nh']} 个/次"
    if k == "disc": return " / ".join(SET_AREA.get(r.get("typ"), []))
    return ""


def row_estimated(r: dict) -> bool:
    """实战模拟室（20 电量）的掉落是估算值；专业挑战室 / 区域巡防是确定的。"""
    return r.get("cost") == 20


def _fmt(n) -> str:
    return f"{round(n):,}"


def _gap(short, unit_text: str, craft: bool = False) -> dict:
    if short <= 0:
        return {"status": "craft" if craft else "ok", "text": "够用（需要合成）" if craft else "够用", "short": 0}
    return {"status": "short", "text": f"还缺 {unit_text}", "short": short}


def compute_needs(state: PlannerState, own: dict | None = None, today: dt.date | None = None) -> dict:
    """材料需求（和网页的材料格子一样分组）。own 默认用 adjusted_own：背包数量加上角色技能提升的校正（GUI 里可编辑的那个）。
    返回:
      {"need": 原始需求汇总,
       "per_agent": {id: {"den": 丁尼, "lv_gap": 等级差} 或 None（没计入）},
       "groups": [{"key", "title", "tiles": [{"name", "tier", "need", "have", "editable", "gap": None|{status,text,short}}]}],
       "nh_rows": 恶名狩猎行（不进电量排期）,
       "totals": {"battery", "days", "per_day", "den_need", "den_short", "nh_short", "cage_need"}}"""
    own = adjusted_own(state) if own is None else own
    o = lambda n: float(own.get(n, 0) or 0)
    need = {"expA": 0, "expW": 0, "den": 0, "seal": {}, "chip": {}, "comp": {}, "hdd": {}, "nh": {}, "cage": 0}
    def add(d, k, t, n): d.setdefault(k, {"C": 0, "B": 0, "A": 0})[t] += n
    per = {}
    for aid, m in state.metas.items():
        c, g = state.cur.get(aid), state.tgt.get(aid)
        if not state.on.get(aid) or not c or not g: per[aid] = None; continue
        den = 0
        lv0, lv1 = c["lv"], max(c["lv"], g["lv"])
        need["expA"] += exp_between(LV_BANDS, lv0, lv1)
        for at, (t, n, d) in PROMO.items():
            if lv0 <= at < lv1: add(need["seal"], m.get("sp"), t, n); den += d
        for i in range(5):
            for lv in range(c["sk"][i] + 1, max(c["sk"][i], g["sk"][i]) + 1):
                t, n, d = SKILL_LV[lv]; add(need["chip"], m.get("chip"), t, n); den += d
                if lv == 12: need["cage"] += 1
        for s in range(c["core"] + 1, max(c["core"], g["core"]) + 1):
            h, nb, d, _ = CORE_ST[s]; den += d
            if m.get("hdd"): need["hdd"][m["hdd"]] = need["hdd"].get(m["hdd"], 0) + h
            if m.get("nh"): need["nh"][m["nh"]] = need["nh"].get(m["nh"], 0) + nb
        wr = c.get("wr", "-")
        if wr and wr != "-":
            wl0, wl1 = c["wl"], max(c["wl"], g["wl"])
            need["expW"] += exp_between(W_BANDS.get(wr, W_BANDS["S"]), wl0, wl1)
            for at, (t, n, d) in W_PROMO.get(wr, W_PROMO["S"]).items():
                if wl0 <= at < wl1: add(need["comp"], m.get("sp"), t, n); den += d
        need["den"] += den
        per[aid] = {"den": den, "lv_gap": lv1 - lv0}

    groups = []
    for key, title, table, total in (("expA", "代理人经验", EXP_A, need["expA"]), ("expW", "音擎经验", EXP_W, need["expW"])):
        have = sum(o(n) * v for n, v in table.items())
        short = max(0, total - have)
        tiles = []
        for tier, (n, v) in zip("CBA", table.items()):
            tiles.append({"name": n, "tier": tier, "value": v, "need": math.ceil(total / v) if tier == "A" else None,
                          "have": own.get(n, 0), "editable": True,
                          "gap": _gap(short, f"{_fmt(short)} 经验（约 {_fmt(math.ceil(short / v))} 个）") if tier == "A" else None})
        tiles.reverse()  # 网页按 A、B、C 排
        groups.append({"key": key, "title": f"{title} · 需要 {_fmt(total)} 经验", "need": total, "have": have, "short": short, "tiles": tiles})
    for key, title, namer in (("seal", "代理人突破 · 认证章", seal), ("chip", "技能升级 · 技能芯片", chip), ("comp", "音擎突破 · 组件", comp)):
        tiles = []
        for k, nd in need[key].items():
            if ceq(nd) <= 0: continue
            have = {t: o(namer(k, t)) for t in "CBA"}
            direct = all(have[t] >= nd[t] for t in "CBA")
            short_c = max(0, ceq(nd) - ceq(have))
            for t in "CBA":
                if not nd[t] and not have[t]: continue
                tiles.append({"name": namer(k, t), "tier": t, "group_key": k, "need": nd[t], "have": own.get(namer(k, t), 0), "editable": True,
                              "gap": _gap(short_c, f"折合 {short_c / 9:.1f} 个 A 级", not direct) if t == "A" else None})
        if tiles: groups.append({"key": key, "title": title, "tiles": tiles})
    tiles, nh_rows, nh_short = [], [], 0
    by_hdd = {m["hdd"]: m for m in state.metas.values() if m.get("hdd")}
    by_nh = {m["nh"]: m for m in state.metas.values() if m.get("nh")}
    for n, c in need["hdd"].items():
        if not c: continue
        s = max(0, c - o(n))
        tiles.append({"name": n, "tier": "A", "kind": "hdd", "need": c, "have": own.get(n, 0), "editable": True, "gap": _gap(s, f"{_fmt(s)} 个"),
                      "boss": (by_hdd.get(n) or {}).get("ecBoss")})
    for n, c in need["nh"].items():
        if not c: continue
        s = max(0, c - o(n)); nh_short += s
        boss = (by_nh.get(n) or {}).get("nhBoss") or "（Boss 待查）"
        tiles.append({"name": n, "tier": "S", "kind": "nh", "need": c, "have": own.get(n, 0), "editable": True, "gap": _gap(s, f"{_fmt(s)} 个"), "boss": boss})
        if s > 0:
            nh_rows.append({"where": f"恶名狩猎 › {boss}", "why": f"核心技：{n}", "cost": 60, "drop": "1 个（每周前 3 次免费）",
                            "runs": math.ceil(s), "battery": math.ceil(s) * 60, "weeks": math.ceil(s / 3), "nh": True})
    if tiles: groups.append({"key": "core", "title": "核心技 · 高维数据与恶名狩猎材料", "tiles": tiles})
    den_short = max(0, need["den"] - o("丁尼"))
    tiles = [{"name": "丁尼", "tier": "S", "need": need["den"], "have": own.get("丁尼", 0), "editable": True, "gap": _gap(den_short, _fmt(den_short))}]
    if need["cage"]:
        s = max(0, need["cage"] - o(CAGE))
        tiles.append({"name": CAGE, "tier": "S", "need": need["cage"], "have": own.get(CAGE, 0), "editable": True,
                      "gap": _gap(s, f"{_fmt(s)} 个（丽都城募/活动）")})
    groups.append({"key": "misc", "title": "通用", "tiles": tiles})

    per_day = int(state.daily_cfg.get("per_day", 240) or 240)
    battery = sum(r["n"] * r["cost"] for r in farm_rows(state, today) if r["kind"] != "disc")
    totals = {"battery": battery, "days": battery / per_day, "per_day": per_day, "den_need": need["den"],
              "den_short": den_short, "nh_short": nh_short, "cage_need": need["cage"]}
    return {"need": need, "per_agent": per, "groups": groups, "nh_rows": nh_rows, "totals": totals}


def agent_order(state: PlannerState, show_all: bool = False) -> list[str]:
    """代理人卡片顺序：★ 在前，再是计入的，再按当前等级从高到低；show_all=False 时只列计入或 ★ 的。"""
    ids = [a for a in state.metas if a in state.cur and (show_all or state.on.get(a) or a in state.starred)]
    return sorted(ids, key=lambda a: (a not in state.starred, not state.on.get(a), -state.cur[a].get("lv", 1)))


def disc_report(state: PlannerState, show_all: bool = False) -> dict:
    """驱动盘检查（网页“驱动盘”面板）：
    {"agents": [{id, name, starred, sets:[(套装, 件数)], four, two, low, not_s, empty, ok, slots:{1..6: 盘或 None}, rec}],
     "no_disc": [没装驱动盘的代理人名], "issues": 需要处理的代理人数}"""
    discs = (state.discs or {}).get("agents") or {}
    shown = [a for a in state.metas if show_all or state.on.get(a)]
    have = [a for a in shown if discs.get(a)]
    have.sort(key=lambda a: a not in state.starred)
    out, issues = [], 0
    for aid in have:
        lst = discs[aid]
        by_slot = {d.get("slot"): d for d in lst}
        cnt = {}
        for d in lst:
            if d and d.get("set"): cnt[d["set"]] = cnt.get(d["set"], 0) + 1
        sets = sorted(cnt.items(), key=lambda x: -x[1])
        four = any(n >= 4 for _, n in sets)
        two = len([1 for _, n in sets if n >= 2]) >= 2
        low = len([d for d in lst if (d.get("level") or 0) < (d.get("level_cap") or 15)])
        not_s = len([d for d in lst if d.get("rarity") != "S"])
        empty = 6 - len([s for s in by_slot if s in range(1, 7)])
        ok = four and two and not low and not not_s and not empty
        if not ok: issues += 1
        m = state.metas[aid]
        starred = aid in state.starred
        rec = None
        if starred:
            ds = m.get("discSets") or {}
            rec = {"four": ds.get("four"), "two": list(ds.get("two") or []), "main": ds.get("main"), "subs": ds.get("subs"),
                   "alt": ds.get("alt"), "area": area_of(ds.get("four", "")) if ds.get("four") else None} if ds.get("four") else {}
        out.append({"id": aid, "name": m.get("name", aid), "starred": starred, "sets": sets, "four": four, "two": two,
                    "low": low, "not_s": not_s, "empty": empty, "ok": ok,
                    "slots": {n: by_slot.get(n) for n in range(1, 7)}, "rec": rec})
    no_disc = [state.metas[a].get("name", a) for a in shown if not discs.get(a)]
    return {"agents": out, "no_disc": no_disc, "issues": issues, "taken_at": (state.discs or {}).get("taken_at")}
