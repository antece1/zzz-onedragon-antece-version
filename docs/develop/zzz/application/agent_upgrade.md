# 代理人升级（agent_upgrade）

按升级清单 `plan.json` 在游戏里升级代理人。来自 antece 版的 auto-zzz 升级脚本，改成一条龙内置应用：截图、OCR、点击直接用一条龙自己的控制器，不再经过后端 HTTP / MCP。

当前只实现了**技能**升级；等级与突破、核心技、音擎会记成「跳过（未实现）」，不点任何按钮。

## 怎么用

1. 准备清单：在 [资源调度员 2.0](https://antece1.github.io/zzz-onedragon-antece-version/) 网页（源码 `docs/index.html`）的「升级清单」里按背包生成并下载 `plan.json`，放到 `config/agent_upgrade/plan.json`（该目录已 gitignore）；也可以复制 `assets/agent_upgrade/plan.example.json` 手改。
2. 游戏设置为 1920×1080、简体中文；要练的代理人在游戏里标星，让其排在代理人列表第一位。
3. 「应用运行」里找到「代理人升级」，设置里：
   - **升级清单**：留空用默认位置。
   - **只执行这些步骤**：填 order，如 `1`，第一次建议只跑一步。
   - **真的升级**：默认关闭，只演练。开启后还要清单里 `approved_by_user: true` 才会真的花材料。
4. 运行。结果写到 `.log/agent_upgrade/<时间>.json`，关键步骤截图在同名文件夹。把这个结果文件拖进网页的「导入升级结果」，网页会按成功的步骤更新技能等级和背包。

不默认进一条龙列表（`DEFAULT_GROUP = False`），因为它会花材料和丁尼。

## 清单格式

```json
{
  "approved_by_user": false,
  "steps": [
    {"order": 1, "agent": "jane_doe", "name_cn": "简", "what": "skill", "skill": "闪避",
     "from": 4, "to": 5, "display_from": 4, "display_to": 5}
  ]
}
```

- `what`：`skill` / `level` / `core` / `engine`，目前只有 `skill` 会执行。
- `skill`：普攻 / 闪避 / 支援 / 特殊技 / 连携技。
- `display_from` / `display_to`：游戏里显示的等级（含影画加成），脚本按这个核对画面；一步只升 1 级。

## 技能升级流程

1. 按 `C` 打开代理人列表（已在列表或详情页时直接用）。
2. 点列表第一张卡，核对头像模板（`assets/agent_upgrade/templates/<agent>_card.png`，匹配度 ≥ 0.85）和左下角名字。
3. 点「技能」进技能页，读 5 个技能的当前等级，和清单的 `display_from` 对不上就跳过这一步。
4. 点技能图标 → 「技能强化」打开强化弹窗，读「有/需」数字。
5. 材料或丁尼不够：把这一步要的材料写进刷取队列 `config/agent_upgrade/queue.json`，关闭弹窗，跳过。
6. 演练：关闭弹窗，记下会花多少。真升级：点「确定」，回技能页再读一次等级和丁尼，对不上立刻停。

## 安全规则

- 每次点击前先用 OCR 确认画面，对不上就截图停下，不猜坐标连点。
- 同一个代理人连续失败两次，跳过这个代理人。
- 不合成、不分解、不花菲林、不碰驱动盘。
- 界面上点停止（F10）会在当前步骤结束后停下，然后返回大世界。

## 文件

| 文件 | 作用 |
|---|---|
| `src/zzz_od/application/agent_upgrade/agent_upgrade_app.py` | 应用节点：读取清单 → 执行清单（每轮一步）→ 保存结果 → 返回大世界 |
| `src/zzz_od/application/agent_upgrade/agent_upgrade_runner.py` | 每类升级的具体操作（目前只有技能） |
| `src/zzz_od/application/agent_upgrade/agent_upgrade_game.py` | 截图、OCR 按文字定位、先确认画面再点击 |
| `assets/agent_upgrade/agents/` | 各代理人升级消耗表，用来查技能芯片名 |
| `assets/agent_upgrade/templates/` | 代理人头像模板 |
