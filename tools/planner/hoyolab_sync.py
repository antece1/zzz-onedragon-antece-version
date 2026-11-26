"""
每晚同步：从 HoYoLAB 战绩接口读账号，更新资源调度员 2.0 的规划数据，并把数据（不含 UID）推到 GitHub Pages。

用法（Windows 计划任务每天 23:00，用 antece版运行目录的 .venv\\Scripts\\pythonw.exe 运行）:
  python tools/planner/hoyolab_sync.py            # 同步并推送到 antece1/zzz-onedragon-antece-version 的 main
  python tools/planner/hoyolab_sync.py --no-push  # 只更新本地，不推 GitHub
  python tools/planner/hoyolab_sync.py --dry-run  # 只读接口、打印结果，不写任何文件

读到的：代理人（等级、影画、5 个技能、核心技、音擎）、已装备的驱动盘、电量。
读不到的：背包材料、丁尼、储蓄电量 —— 背包数量保持上次的值。

文件位置（都已 gitignore，不会提交）：
  config/planner/hoyolab_config.json   登录凭证 ltoken_v2 / ltuid_v2（从浏览器 Cookie 复制）、server、role_id
  config/planner/state/                规划数据（current / targets / inventory / focus / meta / discs / status）
  .log/planner/                        日志和接口原始返回
发布到 docs/data/（网页读取），推送前检查数据里没有 UID。只用 GET 读接口。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]
PLANNER_DIR: Path = ROOT / 'config' / 'planner'
STATE: Path = PLANNER_DIR / 'state'
CONFIG: Path = PLANNER_DIR / 'hoyolab_config.json'
LOG_DIR: Path = ROOT / '.log' / 'planner'
RAW_DIR: Path = LOG_DIR / 'hoyolab_raw'  # 接口原始返回，只留在本机
LOG: Path = LOG_DIR / 'hoyolab_sync.log'
DATA_OUT: Path = ROOT / 'docs' / 'data'
ANTECE_REPO: str = 'https://github.com/antece1/zzz-onedragon-antece-version.git'
BRANCH: str = 'main'

API: str = 'https://sg-public-api.hoyolab.com/event/game_record_zzz/api/zzz/'
SKILL_IDX: dict[int, int] = {0: 0, 2: 1, 6: 2, 1: 3, 3: 4}  # skill_type -> 普攻/闪避/支援/特殊技/连携技
CORE_TYPE: int = 5  # 核心技：接口等级 1-7，7 = F
DISC_CAP: dict[str, int] = {'S': 15, 'A': 12, 'B': 9}
DEFAULT_TARGET_SK: list[int] = [10, 8, 8, 10, 10]
# (state 里的子目录, 文件名) -> 发布到 docs/data/<子目录>_<文件名>
PUBLISH: list[tuple[str, str]] = [('plan', 'current.json'), ('plan', 'targets.json'), ('plan', 'inventory.json'),
                                  ('plan', 'focus.json'), ('meta', 'agents.json'), ('', 'discs.json'), ('', 'status.json')]


def log(msg: str) -> None:
    line = f'{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}'
    if sys.stdout:
        print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass


def rj(p: Path, default: object = None) -> object:
    try:
        return json.loads(p.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default


def wj(p: Path, obj: object) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding='utf-8')


class Hoyolab:
    """HoYoLAB 绝区零战绩接口（只读）"""

    def __init__(self, cfg: dict) -> None:
        self.q: str = f"server={cfg['server']}&role_id={cfg['role_id']}"
        self.headers: dict[str, str] = {
            'Cookie': f"ltoken_v2={cfg['ltoken_v2']}; ltuid_v2={cfg['ltuid_v2']}",
            'x-rpc-lang': 'zh-cn', 'Accept': 'application/json',
            'Origin': 'https://act.hoyolab.com', 'Referer': 'https://act.hoyolab.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36',
        }

    def get(self, path: str) -> dict:
        req = urllib.request.Request(API + path, headers=self.headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode('utf-8'))
        if d.get('retcode') != 0:
            raise RuntimeError(f"{path.split('?')[0]} 返回 {d.get('retcode')}：{d.get('message')}")
        return d['data']

    def fetch_all(self) -> dict:
        basic = self.get('avatar/basic?' + self.q)
        infos = []
        for a in basic.get('avatar_list', []):
            time.sleep(0.4)  # 一次只能查一个 id；放慢一点，别给接口压力
            infos += self.get(f"avatar/info?id_list[]={a['id']}&need_wiki=false&{self.q}").get('avatar_list', [])
        note = self.get('note?' + self.q)
        return {'basic': basic, 'infos': infos, 'note': note}


def num(v: object) -> object:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return v


def convert(raw: dict, id_map: dict[int, str]) -> dict:
    """HoYoLAB 返回 -> 资源调度员的数据格式"""
    agents = {}
    for a in raw['infos']:
        hid = a['id']
        aid = id_map.get(hid, f'agent_{hid}')
        m = num(a.get('rank', 0)) or 0
        bonus = (2 if m >= 3 else 0) + (2 if m >= 5 else 0)  # 影画 3 / 5 各让技能显示等级 +2
        sk, core = [1] * 5, 0
        for s in a.get('skills', []):
            t, lv = s.get('skill_type'), num(s.get('level', 1)) or 1
            if t in SKILL_IDX:
                sk[SKILL_IDX[t]] = max(1, lv - bonus)
            elif t == CORE_TYPE:
                core = max(0, min(6, lv - 1))
        w = a.get('weapon') or {}
        discs = []
        for e in a.get('equip') or []:
            rar = e.get('rarity') or ''
            main = (e.get('main_properties') or [{}])[0]
            discs.append({
                'slot': num(e.get('equipment_type')), 'set': (e.get('equip_suit') or {}).get('name'),
                'rarity': rar, 'level': num(e.get('level')), 'level_cap': DISC_CAP.get(rar),
                'main_stat': {'stat': main.get('property_name'), 'value': main.get('base')},
                'sub_stats': [{'stat': p.get('property_name'), 'value': p.get('base'),
                               'upgrades': max(0, (num(p.get('level', 1)) or 1) - 1)
                               if isinstance(num(p.get('level', 1)), int) else 0}
                              for p in e.get('properties') or []],
            })
        agents[aid] = {
            'hid': hid, 'name': a.get('name_mi18n') or a.get('full_name_mi18n') or aid,
            'rarity': a.get('rarity'), 'm': m,
            'cur': {'lv': num(a.get('level', 1)), 'sk': sk, 'core': core,
                    'wr': w.get('rarity') if w.get('name') else '-', 'wl': num(w.get('level', 1)) or 1,
                    'wname': w.get('name'), 'wrefine': num(w.get('star'))},
            'discs': sorted(discs, key=lambda x: x['slot'] if isinstance(x['slot'], int) else 9),
        }
    energy = (raw['note'].get('energy') or {}).get('progress') or {}
    return {'agents': agents, 'battery': {'current': energy.get('current'), 'max': energy.get('max')}}


def merge_into_state(conv: dict, synced_at: str) -> list[str]:
    """把接口数据合进本地规划数据，返回变化说明"""
    cur_doc = rj(STATE / 'plan' / 'current.json', {'cur': {}})
    tgt_doc = rj(STATE / 'plan' / 'targets.json', {'tgt': {}, 'on': {}})
    meta_doc = rj(STATE / 'meta' / 'agents.json', {'list': []})
    metas = {x['id']: x for x in meta_doc['list']}
    changes = []
    for aid, a in conv['agents'].items():
        old = cur_doc['cur'].get(aid)
        new = {**(old or {}), **a['cur']}
        if old and any(old.get(k) != new.get(k) for k in ('lv', 'sk', 'core', 'wl', 'wr')):
            changes.append(f"{a['name']}：" + '，'.join(f'{k} {old.get(k)}→{new.get(k)}'
                                                     for k in ('lv', 'sk', 'core', 'wl') if old.get(k) != new.get(k)))
        elif not old:
            changes.append(f"新代理人 {a['name']}")
        cur_doc['cur'][aid] = new
        if aid in metas:
            metas[aid].update(rarity=a['rarity'] or metas[aid].get('rarity'), m=a['m'], hid=a['hid'])
        else:
            metas[aid] = {'id': aid, 'name': a['name'], 'rarity': a['rarity'], 'm': a['m'], 'hid': a['hid']}
        if aid not in tgt_doc['tgt']:
            tgt_doc['tgt'][aid] = {'lv': 60, 'sk': [max(s, t) for s, t in zip(a['cur']['sk'], DEFAULT_TARGET_SK, strict=True)],
                                   'core': 6, 'wl': 60}
            tgt_doc.setdefault('on', {})[aid] = False
    cur_doc.update(source='hoyolab', snapAt=synced_at[:16].replace('T', ' '), updated_at=synced_at)
    meta_doc['list'] = list(metas.values())
    meta_doc['updated_at'] = synced_at
    wj(STATE / 'plan' / 'current.json', cur_doc)
    wj(STATE / 'plan' / 'targets.json', tgt_doc)
    wj(STATE / 'meta' / 'agents.json', meta_doc)
    wj(STATE / 'discs.json', {'agents': {k: v['discs'] for k, v in conv['agents'].items() if v['discs']},
                              'taken_at': synced_at})
    wj(STATE / 'status.json', {'battery': conv['battery'], 'synced_at': synced_at, 'source': 'HoYoLAB 战绩接口',
                               'agents': len(conv['agents']), 'changes': changes})
    return changes


def git(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def publish(uid: str, synced_at: str) -> None:
    """
    先快进到 antece1/zzz-onedragon-antece-version 最新的 main，再把规划数据写进 docs/data/、提交并推送。
    先快进再提交，所以 main 被别处更新过也不会推送失败。
    """
    if not (ROOT / '.git').exists():
        raise RuntimeError(f'{ROOT} 不是 git 仓库')
    branch = git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    if branch != BRANCH:
        raise RuntimeError(f'当前在 {branch} 分支，只在 {BRANCH} 分支上发布数据')
    dirty = git('status', '--porcelain', '--untracked-files=no', '--', '.', ':!docs/data').stdout.strip()
    if dirty:
        raise RuntimeError('仓库里有未提交的改动，先处理再同步：\n' + dirty[:300])

    r = git('fetch', ANTECE_REPO, BRANCH, timeout=60)
    if r.returncode:
        raise RuntimeError('拉取 antece1/zzz-onedragon-antece-version 失败：' + r.stderr.strip()[-300:])
    git('checkout', '--', 'docs/data')  # 丢掉上次没提交成功的数据，下面重新写
    r = git('merge', '--ff-only', 'FETCH_HEAD')
    if r.returncode:
        raise RuntimeError('本地 main 有远程没有的提交，无法快进：' + r.stderr.strip()[-300:])

    for sub, f in PUBLISH:
        src = STATE / sub / f if sub else STATE / f
        if not src.exists():
            continue
        text = src.read_text(encoding='utf-8-sig')
        if uid and uid in text:
            raise RuntimeError(f'{f} 里出现了 UID，停止推送')
        (DATA_OUT / (f'{sub}_{f}' if sub else f)).write_text(text, encoding='utf-8')

    git('add', 'docs/data')
    if not git('diff', '--cached', '--quiet').returncode:
        log('账号数据没有变化，不用推送')
        return
    r = git('commit', '-m', f'data: HoYoLAB sync {synced_at[:10]}')
    if r.returncode:
        raise RuntimeError('git commit 失败：' + r.stderr.strip())
    r = git('push', '-q', ANTECE_REPO, f'HEAD:{BRANCH}', timeout=120)
    if r.returncode:  # 这几秒里 main 又被更新：把这次的数据提交接到最新的 main 上再推一次
        git('fetch', ANTECE_REPO, BRANCH, timeout=60)
        rb = git('rebase', 'FETCH_HEAD')
        if rb.returncode:
            git('rebase', '--abort')
            raise RuntimeError('git push 失败，接到最新 main 上也失败：' + r.stderr.strip()[-300:])
        r = git('push', '-q', ANTECE_REPO, f'HEAD:{BRANCH}', timeout=120)
        if r.returncode:
            raise RuntimeError('git push 失败：' + r.stderr.strip()[-300:])
    git('fetch', '-q', 'origin')  # 刷新 origin/main，界面上的版本对比不会显示「落后」
    log('已推送到 GitHub Pages')


def build_id_map() -> dict[int, str]:
    """HoYoLAB 数字编号 -> 资源调度员的代理人 id，先看本地规划数据，再看已发布的数据"""
    id_map: dict[int, str] = {}
    for p in (DATA_OUT / 'meta_agents.json', STATE / 'meta' / 'agents.json'):
        doc = rj(p, {'list': []})
        id_map.update({x['hid']: x['id'] for x in doc.get('list', []) if x.get('hid')})
    return id_map


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-push', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8')  # pythonw（计划任务不弹窗）时没有 stdout
    cfg = rj(CONFIG, {})
    if not cfg.get('ltoken_v2') or not cfg.get('ltuid_v2'):
        log(f'还没填登录凭证：请在 {CONFIG} 里填 ltoken_v2 和 ltuid_v2')
        return 2
    synced_at = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    try:
        raw = Hoyolab(cfg).fetch_all()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if '-100' in msg or '10001' in msg:
            msg += '（登录凭证失效：请重新从浏览器复制 ltoken_v2 / ltuid_v2）'
        log('读取 HoYoLAB 失败：' + msg)
        return 1
    if not a.dry_run:
        wj(RAW_DIR / f'{synced_at[:10]}.json', raw)
    conv = convert(raw, build_id_map())
    log(f"读到 {len(conv['agents'])} 个代理人，电量 {conv['battery'].get('current')}/{conv['battery'].get('max')}")
    if a.dry_run:
        for x in list(conv['agents'].values())[:5]:
            c = x['cur']
            log(f"  {x['name']} Lv{c['lv']} 技能{c['sk']} 核心{c['core']} 音擎{c['wname']} Lv{c['wl']} 盘{len(x['discs'])}")
        return 0
    changes = merge_into_state(conv, synced_at)
    log('变化：' + ('；'.join(changes) if changes else '无'))
    if cfg.get('push_to_github', True) and not a.no_push:
        try:
            publish(str(cfg.get('role_id') or ''), synced_at)
        except Exception as e:  # noqa: BLE001
            log('推送 GitHub 失败：' + str(e))
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
