from __future__ import annotations

import json
import time
from pathlib import Path

from one_dragon.utils import os_utils
from zzz_od.application.character_growth.planner_core import CAGE, SKILLS
from zzz_od.application.character_growth.skill_steps import (
    CONVERT_DENNY_MAX_PER_CHIP,
    CONVERT_DENNY_PER_CHIP,
    CONVERT_RATIO,
    DENNY,
)

__all__ = ['CAGE', 'CONVERT_DENNY_MAX_PER_CHIP', 'CONVERT_DENNY_PER_CHIP', 'CONVERT_RATIO', 'DENNY', 'SKILLS',
           'CARD_TEMPLATE_SUB_DIR', 'add_to_queue', 'clean_name', 'describe', 'queue_path',
           'remove_from_queue', 'remove_from_queue_prefix', 'skill_item_name']

# 代理人列表卡片的头像模板：assets/template/agent_card/<代理人 id>/raw.png（模板 id 就是代理人 id）
CARD_TEMPLATE_SUB_DIR: str = 'agent_card'


def assets_dir() -> Path:
    """随代码提交的资源：各代理人升级消耗表 assets/agent_upgrade/agents/<id>.json"""
    return Path(os_utils.get_path_under_work_dir('assets', 'agent_upgrade'))


def queue_path() -> Path:
    """
    刷取队列：升级时材料不够，把这一步要的材料记下来，给以后的每日刷取用。
    放在 config/agent_upgrade/queue.json（已 gitignore；和代理人升级共用同一个文件、同一个格式）
    """
    return Path(os_utils.get_path_under_work_dir('config', 'agent_upgrade')) / 'queue.json'


def skill_item_name(agent_id: str, frm: int) -> str | None:
    """从 assets/agent_upgrade/agents/<id>.json 查某级技能要的芯片名（强化弹窗里只有图标没有名字）；frm 是基础等级"""
    path = assets_dir() / 'agents' / f'{agent_id}.json'
    if not path.exists():
        return None
    ref = json.loads(path.read_text(encoding='utf-8'))
    row = next((r for r in ref.get('skill_per_level', []) if r['from'] == frm), None)
    return row['item'] if row else None


def _load_queue(path: Path) -> dict:
    """读刷取队列；文件不存在、带 BOM、被改成列表或缺 items 时都按 {"items": [...]} 处理"""
    if not path.exists():
        return {'items': []}
    q = json.loads(path.read_text(encoding='utf-8-sig') or '{}')
    if isinstance(q, list):
        q = {'items': q}
    if not isinstance(q, dict):
        q = {}
    items = q.get('items')
    q['items'] = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return q


def add_to_queue(what: str, needs: list[tuple[str, int]]) -> bool:
    """
    把这一步要的每种材料（强化弹窗上的总消耗，不是差额）追加进刷取队列
    :param what: 这一步的描述，同一个描述已在队列里就不重复加
    :param needs: (材料名, 需要数量)
    :return: 是否新加入
    """
    path = queue_path()
    q = _load_queue(path)
    if any(i.get('for') == what for i in q['items']):
        return False
    now = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    now = now[:-2] + ':' + now[-2:] if len(now) > 5 else now
    for item, need in needs:
        q['items'].append({'item': item, 'need': need, 'for': what, 'added_at': now})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding='utf-8')
    return True


def remove_from_queue(what: str) -> bool:
    """这一步在强化弹窗里材料已经够了：把它之前记进刷取队列的条目去掉（不然每日推送还会接着刷）。返回是否改了文件"""
    path = queue_path()
    if not path.exists():
        return False
    q = _load_queue(path)
    keep = [i for i in q['items'] if i.get('for') != what]
    if len(keep) == len(q['items']):
        return False
    q['items'] = keep
    path.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding='utf-8')
    return True


def remove_from_queue_prefix(prefix: str) -> bool:
    """去掉 for 以 prefix 开头的条目（例如「诺姆 等级 」：升过一部分后，旧的「52→55」不能和新的「53→55」一起刷）。返回是否改了文件"""
    path = queue_path()
    if not path.exists():
        return False
    q = _load_queue(path)
    keep = [i for i in q['items'] if not str(i.get('for') or '').startswith(prefix)]
    if len(keep) == len(q['items']):
        return False
    q['items'] = keep
    path.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding='utf-8')
    return True


def clean_name(text: str | None) -> str:
    """只留下名字里的汉字、字母和数字：去掉 OCR 带出来的 ☆★+、空格和「·」之类的符号"""
    return ''.join(c for c in (text or '') if c.isalnum())


def describe(step: dict) -> str:
    """一步的中文描述，如「简 技能 闪避 4→5」（刷取队列和结果 JSON 用它对应同一步）"""
    name = step.get('name_cn') or step['agent']
    if step['what'] == 'skill':
        frm = step.get('display_from', step['from'])
        to = step.get('display_to', step['to'])
        return f"{name} 技能 {step['skill']} {frm}→{to}"
    label = {'level': '等级', 'core': '核心技', 'engine': '音擎'}.get(step['what'], step['what'])
    return f"{name} {label} {step['from']}→{step['to']}"
