"""每日推送：把"每日电量排期"的第 1 天写进 OneDragon 体力计划（charge_plan.yml）。

antece 版：由 C:\\ZZZ-Auto\\ZZZ-Auto.ps1 在启动一条龙之前调用（计划任务每天 23:05，23:00 先跑 hoyolab_sync.py）。
用本仓库的 venv 运行（需要 PyYAML）:
  .venv\\Scripts\\python.exe tools\\planner\\push_plan.py [--dry-run]

数据来源:
  config\\planner\\state\\           hoyolab_sync.py 每晚更新的账号数据（背包是上次读背包的数量）
  config\\planner\\daily\\ledger.json  上次读背包之后，每天实际刷了多少（从体力计划的 run_times 读回）
  config\\agent_upgrade\\queue.json   代理人升级时材料不够记下的刷取队列
规则:
  - 排期算法和网页一致：核心技 > 突破 > 代理人经验 > 技能 > 音擎 > 丁尼，按每天可用电量装满为止。
  - 恶名狩猎：每周 3 次免费由"恶名狩猎"应用自己打；刷取队列里的恶名狩猎材料（如 刻命残蜕）会排进体力计划，
    每次 60 电量（体力计划的「恶名狩猎」分类，如 恶名狩猎 > 基塔布鲁）。
  - 限时双倍活动（config\\planner\\daily\\config.json 的 boosts）：活动期内每晚先刷几张卡，排在刷取队列前面。
  - 写入前备份原文件到 daily\\backups\\。只替换 plan_list，其余设置保留；loop 设为 false，避免刷完清单后从头重复。
"""
import argparse, datetime as dt, json, os, shutil, sys, uuid

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(TOOLS))           # 仓库根目录
HERE = os.path.join(ROOT, "config", "planner", "daily")  # ledger / config / backups / preview
os.makedirs(HERE, exist_ok=True)
# 计算逻辑和 GUI 共用：src/zzz_od/application/character_growth/planner_core.py（-I 运行时 PYTHONPATH 不生效，自己加路径）
sys.path.insert(0, os.path.join(ROOT, "src"))
from zzz_od.application.character_growth.planner_core import (  # noqa: E402
    PlannerState, charge_plan_path, day_one, farm_plan, filler_row, queue_path, resolve_ledger, today_budget)

CHARGE = charge_plan_path(ROOT)
LEDGER = os.path.join(HERE, "ledger.json")
QUEUE = queue_path(ROOT)  # 升级应用写的刷取队列


def to_plan_items(picked, team_idx, tags):
    """OneDragon builds ChargePlanItem(**item), so items carry only its own fields; our tags go to the ledger by plan_id."""
    items = []
    def item(r, plan_times, card):
        pid = str(uuid.uuid4())
        tags[pid] = {"kind": r["kind"], "key": r.get("key"), "cards_per_run": 1 if r["cost"] != 20 else int(card)}
        return {"tab_name": "训练", "category_name": r["cat"], "mission_type_name": r["typ"], "mission_name": r["mis"],
                "level": "默认等级", "auto_battle_config": "全配队通用", "run_times": 0, "plan_times": plan_times,
                "card_num": card, "predefined_team_idx": team_idx, "notorious_hunt_buff_num": 1, "plan_id": pid}
    for r in picked:
        if r["cost"] != 20:  # 专业挑战室 / 区域巡防 / 恶名狩猎：一次一场
            items.append(item(r, r["n"], "默认数量"))
        else:  # 实战模拟室：一次最多 5 张卡
            full, rest = divmod(r["n"], 5)
            if full: items.append(item(r, full, "5"))
            if rest: items.append(item(r, 1, str(rest)))
    return items


def main():
    import yaml
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    state = PlannerState.load(ROOT, charge_plan=False)
    cfg = state.daily_cfg
    if not cfg.get("enabled", True): print("daily push disabled in config.json"); return
    yml = yaml.safe_load(open(CHARGE, encoding="utf-8")) or {}
    state.charge_yml = yml
    today = dt.date.today()
    # ledger 只看背包：记的是"上次读背包之后刷到的东西"。每晚 HoYoLAB 同步只改等级，不该清掉这些记录。
    ledger = resolve_ledger(state, today)
    # 排序：双倍活动 > 刷取队列 > ★ 代理人缺口 > ★ 驱动盘 > 其他（规则见 planner_core.farm_plan）
    plan = farm_plan(state, today)
    rows, qkeep = plan["rows"], plan["queue_keep"]
    queue = state.queue
    for nline in plan["notes"]: print("  提示：" + nline)
    # 每日清体力：按 23:00 同步到的实际电量排；同步不是今天的就用 config 里的 per_day
    budget, budget_src = today_budget(state, today)
    picked, left = day_one(rows, budget)
    old = yml.get("plan_list") or []
    team = next((p.get("predefined_team_idx") for p in old if p.get("predefined_team_idx") is not None), 3)
    tags = {}
    items = to_plan_items(picked, team, tags)
    # 兜底：最后放一个 20 电量的本，次数给够，把剩下的（以及跑本期间回复的）电量花完；电量不够时一条龙自己停
    clear_all = cfg.get("clear_all", True)
    if clear_all:
        filler = filler_row(rows)
        fid = str(uuid.uuid4())
        tags[fid] = {"kind": filler["kind"], "key": filler.get("key"), "cards_per_run": 1}
        items.append({"tab_name": "训练", "category_name": filler["cat"], "mission_type_name": filler["typ"], "mission_name": filler["mis"],
                      "level": "默认等级", "auto_battle_config": "全配队通用", "run_times": 0, "plan_times": 12,
                      "card_num": "1", "predefined_team_idx": team, "notorious_hunt_buff_num": 1, "plan_id": fid})
    print(f"今日排期（{budget_src} {budget}，排完剩 {left}）：")
    for r in picked: print(f"  {r['cat']} > {r['typ']}{' > ' + r['mis'] if r['mis'] else ''}  x{r['n']}{'次' if r['cost'] != 20 else '张卡'}  （{r['why']}）")
    if clear_all: print(f"  {filler['cat']} > {filler['typ']} > {filler['mis']}  1 张卡一次，最多 12 次，刷到电量不够为止  （{filler['why']}）")
    if not items: print("  没有缺口，不改体力计划。"); return
    new = dict(yml); new["plan_list"] = items; new["loop"] = False
    if clear_all: new["skip_plan"] = True  # 电量不够打 40/60 的本时跳过它，继续刷后面 20 电量的本
    if a.dry_run:
        out = os.path.join(HERE, "preview_charge_plan.yml")
        yaml.safe_dump(new, open(out, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
        print("演练：没有改 OneDragon 配置，预览写到", out); return
    os.makedirs(os.path.join(HERE, "backups"), exist_ok=True)
    shutil.copy2(CHARGE, os.path.join(HERE, "backups", f"charge_plan_{dt.datetime.now():%Y%m%d_%H%M%S}.yml"))
    yaml.safe_dump(new, open(CHARGE, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
    ledger["pushed_on"] = dt.date.today().isoformat(); ledger["last_push"] = [dict(r) for r in picked]; ledger["tags"] = tags
    json.dump(ledger, open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if len(qkeep) != len(queue.get("items", [])):  # drop queue entries the bag now covers
        json.dump({"items": qkeep}, open(QUEUE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("已写入", CHARGE)


if __name__ == "__main__":
    main()
