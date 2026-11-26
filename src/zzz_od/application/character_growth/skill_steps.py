"""角色技能提升：按培养目标和攻略优先级算出这一次要升哪些技能（纯 Python，不依赖 Qt 和游戏）。

一条龙里的「角色技能提升」应用每次运行前调用 build_skill_steps，得到和升级清单 plan.json 同格式的技能步骤，
交给 agent_upgrade_runner.Upgrader 在游戏里一级一级地升。规则：
  - 只看计入（on）的代理人：★ 优先培养的在前（focus.json 的顺序），其余按攻略优先级 high > mid > low > 未收录，同级按 agents.json 顺序
  - 同一代理人按攻略的 skill_order 先后升；一步只升 1 级；永远不超过 targets.json 里的培养目标
  - 开启「先到攻略建议等级」时分两轮，轮次在代理人外面：★ 代理人先全部升到 min(培养目标, 攻略建议)，
    再按培养目标继续；然后其余代理人同样两轮。这样同一组里每个代理人都先到攻略建议，才有人继续往培养目标升
  - 11→12 需要代理人 60 级并且有「仓鼠笼」访问器；芯片按档位精确计算，默认不考虑合成；
    开着「材料不够时自动转化」（convert）时特化芯片不够可以用进阶芯片 3 换 1 补上（只排 进阶→特化）
  - 背包是估算（skill_bag）：估算不够的材料（芯片 / 仓鼠笼 / 丁尼保留线）每种还是排一步「到游戏里核对」，
    由强化弹窗里的 有/需 决定升不升，这个技能后面的等级这次不再排；丁尼到线时核对这一步后整体停下
  - 每次最多 max_steps 步
  - current.json 里的技能是去掉影画加成的基础等级；游戏里显示的等级 = 基础 + 影画加成（3 影 +2，5 影再 +2）

生成步骤只读文件。游戏里核对过的升级由 skill_upgrade_app 写回 config/planner/state/plan/current.json（write_verified_skill，
只改这一个技能等级）；强化弹窗里看到的真实数量和花掉的材料记进 config/skill_upgrade/bag_adjust.json
（planner_core.observe_bag），每日推送和角色培养界面也会算上它，背包重新读过后作废。
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

from zzz_od.application.character_growth.planner_core import (
    CAGE,
    EXP_A,
    LV_BANDS,
    PROMO,
    SKILL_LV,
    SKILLS,
    PlannerState,
    _now_iso,
    _write_json_atomic,
    chip,
    effective_own,
    exp_between,
    load_bag_adjust,
    repo_root,
    state_dir,
)
from zzz_od.application.character_growth.planner_core import skill_upgrade_dir as skill_upgrade_dir_str

DENNY: str = '丁尼'
EXP: str = '代理人经验'  # 等级步骤的 cost 里用它表示经验值（不是物品）
# 材料转化（强化弹窗芯片不够时游戏给出「转化获取」）：3 个低一档芯片换 1 个，
# 丁尼按实测「18 个进阶换 6 个特化花 1800」估算每换 1 个 300；1000 只是读数的合理上限
CONVERT_RATIO: int = 3
CONVERT_DENNY_PER_CHIP: int = 300
CONVERT_DENNY_MAX_PER_CHIP: int = 1000
# 技能升到这一级（基础等级）要代理人多少级。和核心技门槛同一档（15/25/35/45/55/60）；
# 10 级要 55 是游戏强化弹窗「代理人等级不足55」确认过的（2026-11-23），其余按账号数据推得：
# 20 级代理人有 3 级技能、40 级有 7、52 级有 9、55 级有 10、60 级有 12。
# 游戏里看到的门槛记在 config/skill_upgrade/level_gate_seen.json，优先于这张表。
SKILL_GATE: dict[int, int] = {2: 15, 3: 15, 4: 25, 5: 25, 6: 35, 7: 35, 8: 45, 9: 45, 10: 55, 11: 55, 12: 60}
DEFAULT_ORDER: list[str] = ['特殊技', '连携技', '普攻', '支援', '闪避']  # 攻略没收录的代理人，和 allocate.py 一样
TIER_RANK: dict[str, int] = {'high': 0, 'mid': 1, 'low': 2}
TIER_TEXT: dict[str, str] = {'high': '高', 'mid': '中', 'low': '低'}


# ---------------------------------------------------------------- 路径和数据

def priority_path(root: str | None = None) -> Path:
    """攻略技能优先级：assets/character_growth/skill_priority.json（随代码提交）"""
    return Path(root or repo_root()) / 'assets' / 'character_growth' / 'skill_priority.json'


def load_priority(root: str | None = None) -> dict[str, dict]:
    """读攻略技能优先级，按代理人 id：{priority_tier, skill_order, skill_targets}；文件不存在时返回空"""
    path = priority_path(root)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8')).get('agents', {})


def skill_upgrade_dir(root: str | None = None) -> Path:
    """角色技能提升自己的数据目录：config/skill_upgrade/（结果、背包校正）"""
    return Path(skill_upgrade_dir_str(root))


def results_dir(root: str | None = None) -> Path:
    return skill_upgrade_dir(root) / 'results'


def level_gate_path(root: str | None = None) -> Path:
    """游戏里看到的技能等级门槛：config/skill_upgrade/level_gate_seen.json"""
    return skill_upgrade_dir(root) / 'level_gate_seen.json'


def load_level_gates(root: str | None = None) -> dict[int, int]:
    """技能等级 -> 要的代理人等级：SKILL_GATE，再用游戏里看到的覆盖"""
    gates = dict(SKILL_GATE)
    path = level_gate_path(root)
    try:
        seen = json.loads(path.read_text(encoding='utf-8')).get('gates', {})
        for to, v in seen.items():
            gates[int(to)] = int(v['need'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return gates


def record_level_gate(to_level: int, need_level: int, why: str, root: str | None = None) -> bool:
    """强化弹窗里看到「代理人等级不足N」：记下「技能升到 to_level 要代理人 N 级」。返回是否改了文件"""
    path = level_gate_path(root)
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        doc = {}
    gates = doc.setdefault('gates', {})
    old = gates.get(str(to_level))
    if old and int(old.get('need', 0)) == int(need_level):
        return False
    gates[str(to_level)] = {'need': int(need_level), 'at': _now_iso(), 'why': why}
    doc['_说明'] = '技能升到某一级（基础等级）要的代理人等级，来自游戏强化弹窗「代理人等级不足N」；生成技能步骤时优先于代码里的 SKILL_GATE'
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(str(path), doc)
    return True


def exp_have(bag: dict[str, float]) -> float:
    """背包里调查员记录折成的经验"""
    return sum(bag.get(n, 0) * v for n, v in EXP_A.items())


def spend_exp(bag: dict[str, float], exp: float) -> None:
    """估算用：从大到小扣调查员记录（游戏里的自动填充也是先用大的）"""
    for name, v in sorted(EXP_A.items(), key=lambda kv: -kv[1]):
        if exp <= 0:
            break
        n = min(bag.get(name, 0), -(-exp // v))
        bag[name] = bag.get(name, 0) - n
        exp -= n * v


def mind_bonus(m: Any) -> int:
    """影画给技能的显示加成：3 影 +2，5 影再 +2"""
    try:
        m = int(m or 0)
    except (TypeError, ValueError):
        m = 0
    return (2 if m >= 3 else 0) + (2 if m >= 5 else 0)


def skill_bag(state: PlannerState, today: dt.date | None = None) -> dict[str, float]:
    """
    生成步骤用的背包：上次读背包的数量 + 之后刷本的估算掉落（含今晚刚刷完、明天推送才记账的）
    + 技能提升记下的校正（花掉的、强化弹窗里看到的真实数量）。和每日推送用的是同一套估算。
    """
    return {k: float(v or 0) for k, v in effective_own(state, today, pending=True).items()}


def bag_source_text(state: PlannerState) -> str:
    """日志里说明背包数据从哪来"""
    when = '没有记录'
    if state.inventory_stamp:
        when = dt.datetime.fromtimestamp(state.inventory_stamp).strftime('%Y-%m-%d %H:%M')
    adjust = load_bag_adjust(state)
    text = f'背包：上次读背包 {when}，加上之后刷本的估算掉落'
    if adjust:
        text += '，以及技能提升在游戏里看到的数量（' + '，'.join(f'{k} {v:+,.0f}' for k, v in adjust.items()) + '）'
    return text + '；估算不够的材料每种先到游戏里看一次强化弹窗'


# ---------------------------------------------------------------- 生成步骤

def agent_order(state: PlannerState, prio: dict[str, dict]) -> list[str]:
    """升级顺序：计入的 ★ 代理人按 focus.json 顺序在前；其余计入的按攻略优先级，同级按 agents.json 顺序"""
    def ok(a: str) -> bool:
        return bool(state.on.get(a)) and a in state.metas and a in state.cur and a in state.tgt
    stars = [a for a in state.starred if ok(a)]
    idx = {a: i for i, a in enumerate(state.metas)}
    rest = [a for a in state.metas if ok(a) and a not in stars]
    rest.sort(key=lambda a: (TIER_RANK.get((prio.get(a) or {}).get('priority_tier', ''), 3), idx[a]))
    return stars + rest


def skill_order_of(prio_entry: dict | None) -> list[str]:
    """代理人的技能先后：攻略的 skill_order，缺的技能按默认顺序补在后面"""
    order = [s for s in (prio_entry or {}).get('skill_order', []) if s in SKILLS]
    return order + [s for s in DEFAULT_ORDER if s not in order]


def build_skill_steps(state: PlannerState, prio: dict[str, dict], reserve: int = 100000, max_steps: int = 10,
                      research_first: bool = True, bag: dict[str, float] | None = None,
                      auto_level: bool = True, gates: dict[int, int] | None = None, convert: bool = False) -> dict:
    """
    算出这一次要升的技能
    技能下一级要的代理人等级（gates，默认 load_level_gates()）比现在高时：auto_level 开着就先排一步
    「等级」（what='level'，只升到够用的等级，不跨突破），关着就记成卡住
    convert（材料不够时自动转化）：特化芯片按估算不够、进阶芯片按 3 换 1 够补上（丁尼每个约 300，保留线照算）时，
    照常排这一步（带 "convert": {from, used, to, made, dennies}），从背包里扣掉转化用的进阶芯片和丁尼；
    只排实测过的 进阶→特化，不排 基础→进阶、不连着转化
    :param state: PlannerState.load() 的结果
    :param prio: load_priority() 的结果
    :param reserve: 丁尼保留线
    :param max_steps: 最多几步（一步 = 一个技能升 1 级）
    :param research_first: 先把各技能升到攻略建议等级，再按培养目标继续
    :param bag: 背包，默认用 skill_bag(state)
    :return: {"steps": [和 plan.json 一样的技能步骤；估算不够、要到游戏里核对的那步带 "check": 原因],
              "blocked": [卡住的技能和原因], "agents": [参与的代理人],
              "bag_after": {用到的材料升完剩多少（估算）}, "stopped": 整体停下的原因或 None}
    """
    bag = dict(bag if bag is not None else skill_bag(state))
    gates = dict(gates if gates is not None else load_level_gates())
    watched = {DENNY, CAGE}
    steps: list[dict] = []
    blocked: list[dict] = []
    stopped: str | None = None
    order = agent_order(state, prio)
    probed: set[str] = set()  # 估算不够、已经排了一步到游戏里核对的材料（每种一次）

    # 每个代理人的数据和进度，两轮共用
    info: dict[str, dict] = {}
    for aid in order:
        meta, cur, tgt = state.metas[aid], state.cur[aid], state.tgt[aid]
        p = prio.get(aid) or {}
        target = {sk: min(12, int((tgt.get('sk') or [1] * 5)[i])) for i, sk in enumerate(SKILLS)}
        caps = {'培养目标': target}
        research = p.get('skill_targets') or {}
        if research_first and research:
            caps['攻略建议'] = {sk: min(target[sk], int(research.get(sk, 12))) for sk in SKILLS}
        info[aid] = {
            'meta': meta, 'name': meta.get('name', aid), 'bonus': mind_bonus(meta.get('m')),
            'lv': [int(x) for x in cur.get('sk', [1] * 5)], 'agent_lv': int(cur.get('lv', 1) or 1),
            'goal_lv': int(tgt.get('lv') or 60),  # 培养目标等级：升级代理人不超过它（和应用、每日推送一致）
            'order': skill_order_of(p), 'caps': caps,
            'dead': set(),  # 已经卡住的技能，后面的等级都依赖它
        }

    def plan_level(aid: str, need_lv: int, sk: str, to: int, pass_name: str) -> tuple[str | None, str | None]:
        """
        技能 sk 升到 to 要代理人 need_lv 级：排一步「等级」把代理人升到 need_lv
        :return: (卡住的原因, 整体停下的原因)；都是 None 表示排好了
        """
        a = info[aid]
        lv0 = a['agent_lv']
        why = f"{sk} {to} 级要代理人 {need_lv} 级（现在 {lv0} 级）"
        if a.get('level_dead'):
            return f"{why}；{a['level_dead']}", None
        if need_lv > a['goal_lv']:
            a['level_dead'] = f"培养目标等级 {a['goal_lv']} 不够"
            return f"{why}；{a['level_dead']}", None
        promos =[b for b in sorted(PROMO) if lv0 <= b < need_lv]
        if promos:  # HoYoLAB 的等级看不出突破了没有，正好在 10/20/.. 级也当作要突破
            a['level_dead'] = f'要先突破（{promos[0]} 级），自动突破还没做'
            return f"{why}；{a['level_dead']}", None
        if len(steps) >= max_steps:
            return None, f'已到每次最多 {max_steps} 步'
        exp = int(round(exp_between(LV_BANDS, lv0, need_lv)))
        for name in EXP_A:
            watched.add(name)
        step = {
            'order': len(steps) + 1, 'what': 'level', 'agent': aid, 'name_cn': a['name'],
            'skill': sk, 'from': lv0, 'to': need_lv, 'for': f'{sk} {to} 级',
            'cost': {EXP: exp}, 'agent_lv': lv0, 'pass': pass_name,
        }
        if exp_have(bag) < exp:
            have = exp_have(bag)
            if EXP in probed:
                a['level_dead'] = f'代理人经验按估算有 {have:,.0f}，要 {exp:,}'
                return f"{why}；{a['level_dead']}", None
            probed.add(EXP)  # 估算不够也到游戏里看一次升级界面，和芯片一样
            step['check'] = f'按估算代理人经验有 {have:,.0f}，要 {exp:,}，到游戏里看升级界面再定'
        spend_exp(bag, exp)
        steps.append(step)
        a['agent_lv'] = need_lv
        return None, None

    def plan_skill(aid: str, sk: str, cap: int, pass_name: str) -> str | None:
        """把一个技能往 cap 升；返回整体停下的原因或 None"""
        a = info[aid]
        i = SKILLS.index(sk)
        lv, bonus = a['lv'], a['bonus']
        while sk not in a['dead'] and lv[i] < cap:
            if len(steps) >= max_steps:
                return f'已到每次最多 {max_steps} 步'
            to = lv[i] + 1
            tier, n, den = SKILL_LV[to]
            item = chip(a['meta'].get('chip', ''), tier)
            watched.add(item)
            reason, short_of = None, None
            need_lv = gates.get(to, 0)
            if a['agent_lv'] < need_lv:
                if auto_level:
                    reason, stop = plan_level(aid, need_lv, sk, to, pass_name)
                    if stop:
                        return stop
                else:
                    reason = f"{to} 级要代理人 {need_lv} 级（现在 {a['agent_lv']} 级）"
                if reason is None and len(steps) >= max_steps:
                    return f'已到每次最多 {max_steps} 步'
            conv: dict | None = None
            if convert and reason is None and tier == 'A' and bag.get(item, 0) < n:
                short = math.ceil(n - bag.get(item, 0))
                src = chip(a['meta'].get('chip', ''), 'B')
                used = CONVERT_RATIO * short
                dc = CONVERT_DENNY_PER_CHIP * short
                if bag.get(src, 0) >= used and bag.get(DENNY, 0) - den - dc >= reserve:
                    conv = {'from': src, 'used': used, 'to': item, 'made': short, 'dennies': dc}
            conv_den = conv['dennies'] if conv else 0
            if reason is not None:
                pass
            elif to == 12 and bag.get(CAGE, 0) < 1:
                reason, short_of = f'缺 {CAGE}', CAGE
            elif bag.get(item, 0) < n and conv is None:
                reason, short_of = f'{item} 有 {bag.get(item, 0):g}，要 {n}', item
            elif bag.get(DENNY, 0) - den - conv_den < reserve:
                reason, short_of = f'丁尼 {bag.get(DENNY, 0):,.0f} 花 {den + conv_den:,} 后低于保留 {reserve:,}', DENNY
            cost = {item: n, DENNY: den}
            if to == 12:
                cost[CAGE] = 1
            step = {
                'order': len(steps) + 1, 'what': 'skill', 'agent': aid, 'name_cn': a['name'],
                'skill': sk, 'from': lv[i], 'to': to,
                'display_from': lv[i] + bonus, 'display_to': to + bonus,
                'cost': cost, 'agent_lv': a['agent_lv'], 'pass': pass_name,
            }
            if reason:
                a['dead'].add(sk)  # 这次不再排这个技能后面的等级
                if short_of is not None and short_of not in probed:
                    # 背包是估算（旧的读背包 + 刷本估算），不够也到游戏里看一次强化弹窗：真的够就升，不够就跳过并记进刷取队列
                    probed.add(short_of)
                    step['check'] = f'按估算{reason}，到游戏里看强化弹窗再定'
                    steps.append(step)
                    if short_of != DENNY:
                        bag[DENNY] = bag.get(DENNY, 0) - den
                    else:
                        return '丁尼按估算到保留线（最后一步到游戏里核对）'
                    return None
                blocked.append({'agent': aid, 'name_cn': a['name'], 'skill': sk, 'from': lv[i], 'to': to,
                                'display_from': lv[i] + bonus, 'display_to': to + bonus, 'reason': reason})
                return '丁尼到保留线' if short_of == DENNY else None
            if conv is not None:  # 先转化：进阶芯片和丁尼扣掉，特化芯片加上转化出来的（cost 仍是强化弹窗上的花费）
                bag[conv['from']] = bag.get(conv['from'], 0) - conv['used']
                bag[item] = bag.get(item, 0) + conv['made']
                bag[DENNY] = bag.get(DENNY, 0) - conv['dennies']
                step['convert'] = conv
                watched.add(conv['from'])
            for k, v in cost.items():
                bag[k] = bag.get(k, 0) - v
            steps.append(step)
            lv[i] = to
        return None

    passes = ['攻略建议', '培养目标'] if research_first else ['培养目标']
    stars = [a for a in order if a in state.starred]
    groups = [g for g in (stars, [a for a in order if a not in state.starred]) if g]
    for group in groups:  # ★ 代理人先两轮都做完，再轮到其余代理人
        for pass_name in passes:  # 轮次在外层：组里每个代理人都先到攻略建议，再往培养目标升
            for aid in group:
                cap = info[aid]['caps'].get(pass_name)
                if cap is None:
                    continue
                for sk in info[aid]['order']:
                    stopped = plan_skill(aid, sk, cap[sk], pass_name)
                    if stopped:
                        break
                if stopped:
                    break
            if stopped:
                break
        if stopped:
            break

    return {
        'steps': steps, 'blocked': blocked, 'agents': order, 'stopped': stopped,
        'bag_after': {k: bag.get(k, 0) for k in sorted(watched)},
    }


def format_steps(result: dict, state: PlannerState, prio: dict[str, dict]) -> list[str]:
    """给日志 / 测试打印用的中文说明"""
    lines: list[str] = []
    names = []
    for a in result['agents']:
        tier = TIER_TEXT.get((prio.get(a) or {}).get('priority_tier', ''), '未收录')
        names.append(f"{'★' if a in state.starred else ''}{state.metas[a].get('name', a)}（{tier}）")
    lines.append('代理人顺序：' + '、'.join(names))
    for s in result['steps']:
        cost = '，'.join(f'{k} {v:,}' for k, v in s['cost'].items())
        if s['what'] == 'level':
            lines.append(f"  {s['order']:>2}. {s['name_cn']} 等级 {s['from']}→{s['to']}（{s['for']}要，{s['pass']}）"
                         f"约 {cost}" + (f"；{s['check']}" if s.get('check') else ''))
            continue
        cv = s.get('convert')
        lines.append(f"  {s['order']:>2}. {s['name_cn']} {s['skill']} {s['display_from']}→{s['display_to']}"
                     f"（基础 {s['from']}→{s['to']}，{s['pass']}）花 {cost}"
                     + (f"；先转化 {cv['from']} {cv['used']}→{cv['to']} {cv['made']}（约丁尼 {cv['dennies']:,}）" if cv else '')
                     + (f"；{s['check']}" if s.get('check') else ''))
    for b in result['blocked']:
        lines.append(f"  卡住：{b['name_cn']} {b['skill']} {b['display_from']}→{b['display_to']}：{b['reason']}")
    if result['stopped']:
        lines.append(f"  停下：{result['stopped']}")
    lines.append('  升完剩（估算）：' + '，'.join(f'{k} {v:,.0f}' for k, v in result['bag_after'].items()))
    return lines


# ---------------------------------------------------------------- 回写 current.json

def write_verified_skill(agent_id: str, skill: str, base_level: int, root: str | None = None) -> bool:
    """
    游戏里核对过升级成功后，把 current.json 里这个技能的基础等级改成 base_level（只升不降）。
    重新读文件后只改这一个数字，其他字段原样保留；source 不改（每晚 HoYoLAB 同步会重新写一遍）。
    :return: 是否写了文件
    """
    path = Path(state_dir(root)) / 'plan' / 'current.json'
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    doc = raw['data'] if isinstance(raw.get('data'), dict) else raw
    cur = doc.get('cur', {}).get(agent_id)
    if cur is None or skill not in SKILLS:
        return False
    sk = list(cur.get('sk') or [1] * 5)
    i = SKILLS.index(skill)
    if int(sk[i]) >= base_level:
        return False
    sk[i] = int(base_level)
    cur['sk'] = sk
    doc['updated_at'] = _now_iso()
    doc['skill_upgrade_at'] = doc['updated_at']
    _write_json_atomic(str(path), raw)
    return True


def write_verified_level(agent_id: str, level: int, root: str | None = None) -> bool:
    """
    游戏里核对过升级代理人成功后，把 current.json 里这个代理人的等级改成 level（只升不降）。
    和 write_verified_skill 一样：重新读文件后只改这一个数字，其他字段原样保留。
    :return: 是否写了文件
    """
    path = Path(state_dir(root)) / 'plan' / 'current.json'
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    doc = raw['data'] if isinstance(raw.get('data'), dict) else raw
    cur = doc.get('cur', {}).get(agent_id)
    if cur is None or not 1 <= int(level) <= 60:
        return False
    if int(cur.get('lv', 1) or 1) >= int(level):
        return False
    cur['lv'] = int(level)
    doc['updated_at'] = _now_iso()
    doc['skill_upgrade_at'] = doc['updated_at']
    _write_json_atomic(str(path), raw)
    return True
