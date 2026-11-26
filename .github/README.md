<div align="center">

<img src="https://raw.githubusercontent.com/antece1/zzz-onedragon-antece-version/main/assets/ui/antece_logo.png" width="120" alt="绝区零一条龙 antece版"/>

# 绝区零一条龙 antece版

</div>

基于 [绝区零一条龙（ZenlessZoneZero-OneDragon）](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon) 的个人修改版。官方的全部功能都保留，在它上面加了代理人培养相关的功能和一些修复。

## antece版多了什么

| 功能 | 在哪 | 做什么 |
|---|---|---|
| 代理人升级 | 「应用运行」→「代理人升级」 | 按升级清单在游戏里升代理人技能；每次点击前先识别画面，默认只演练不花材料。说明见 [agent_upgrade.md](https://github.com/antece1/zzz-onedragon-antece-version/blob/main/docs/develop/zzz/application/agent_upgrade.md) |
| 资源调度员 2.0 | [网页](https://antece1.github.io/zzz-onedragon-antece-version/)（源码 `docs/index.html`） | 算该练谁、缺多少材料、每天电量怎么排；按背包生成升级清单，跑完后导入结果 |
| 每晚同步和每日推送 | `tools/planner/` | 每晚从 HoYoLAB 读账号数据更新网页；把刷取排期写进体力计划，一条龙照常跑 |
| 桌面启动器 | `deploy/antece_launcher.pyw` | 以管理员身份启动（游戏是管理员进程），启动前自动更新到本仓库最新代码 |
| 代码源 | `config/repository.yml` | 界面里「同步代码」只从本仓库拉，不会被官方代码覆盖 |

修复（官方仓库里有对应 issue 的附了链接）：

- 声音闪避：按进程录音被重复打开，导致回退录音也报错（[官方 issue 3058](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/issues/3058)）
- 咖啡店：账号没有汀曼特调时改喝浓缩咖啡（[官方 issue 3055](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/issues/3055)）
- 锄大地：3D 地图传送卡在六分街地图、前往按钮消失

## 安装和使用

步骤见 [antece_launcher.md](https://github.com/antece1/zzz-onedragon-antece-version/blob/main/docs/develop/setup/antece_launcher.md)：克隆到单独的目录（不要和官方安装共用），`uv sync`，复制账号配置，用桌面快捷方式启动。不要在 antece版的目录里安装官方启动器，它会拉官方代码覆盖 antece版的改动。

## 分支

- `main`：官方代码 + antece版的改动，日常用这个。每晚同步会往这里加一个数据提交（`data: HoYoLAB sync`）。
- `feat/*`、`fix/*`、`docs/*`：每个功能或修复单独一个分支，做完合并进 `main`。
- 旧版 auto-zzz（换成一条龙底座之前的规划器和脚本）：分支 `auto-zzz-old`，标签 `auto-zzz-v0.3.0-backup`。

## 为什么贡献者这么多

本仓库保留了 OneDragon 的完整提交历史（两千多个提交），所以 GitHub 贡献者列表里是写这些代码的 OneDragon 作者们；antece版自己的改动由 antece 提交。保留历史是为了以后能用普通合并拉取官方更新。

## 致谢与许可证

代码来自 [OneDragon-Anything/ZenlessZoneZero-OneDragon](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon)（DoctorReid 和所有贡献者），按 GPL-3.0 发布，见 [LICENSE](https://github.com/antece1/zzz-onedragon-antece-version/blob/main/LICENSE)。antece版的改动同样是 GPL-3.0。

这是非官方修改版，和官方项目、HoYoverse / 米哈游都没有关系；有问题请在本仓库反馈，不要去官方提。自动化操作游戏可能违反游戏的用户协议，风险自负。

---

以下是 OneDragon 官方说明（原文未改）：

<!-- markdownlint-restore -->
<div align="center">

<img src="https://socialify.git.ci/DoctorReid/ZenlessZoneZero-OneDragon/image?description=1&font=Inter&language=1&logo=https%3A%2F%2Fgithub.com%2FDoctorReid%2FZenlessZoneZero-OneDragon%2Fblob%2Fmain%2F.github%2Fimage%2Fellen.png%3Fraw%3Dtrue&name=1&owner=1&pattern=Charlie+Brown&theme=Light" alt="ZenlessZoneZero-OneDragon"/>
<div>

---

</div>

__ZenlessZoneZero-OneDragon__


<div>
    <img alt="platform" src="https://img.shields.io/badge/platform-Windows-blueviolet">
    <img alt="commit" src="https://img.shields.io/github/commit-activity/m/DoctorReid/ZenlessZoneZero-OneDragon?color=blue">
</div>
<div>
    <img alt="stars" src="https://img.shields.io/github/stars/DoctorReid/ZenlessZoneZero-OneDragon?style=social">
    <img alt="GitHub all releases" src="https://img.shields.io/github/downloads/DoctorReid/ZenlessZoneZero-OneDragon/total?style=social">
</div>

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/OneDragon-Anything/ZenlessZoneZero-OneDragon)

学习实践 __图像识别 & 自动化__ 的智能解决方案

✨ 如果喜欢本项目，欢迎右上角点亮`Star`支持 ✨

</div>

## 🚀 功能介绍

<div align="left">

🤖 __自动战斗__：支持自定义逻辑、技能标记、条件、变量等进阶用法

🛡️ __闪避助手__：基于声音、图像一体化模型识别，轻占用，高准确

🧹 __日常清理__：影像店、刮刮乐、咖啡店、材料、奖励等全日常清理

☕ __空洞作战__：基于大模型训练的识别、事件、寻路的功能模块

⏲️ __更多功能__： 定时启动、多账号切换、定时任务、自动对话……

</div>

## 📸 页面预览

<div align="center">

![App Interface](./image/app.webp#gh-light-mode-only "操作界面")
*✨ 简洁直观的操作界面 ✨*

</div>

## ⚠️ 免责声明

📌 本项目仅供学习交流，开发者团队保留最终解释权

🎨 项目LOGO版权归[巡夜子](https://github.com/yokuminto)及全体开发者所有

⚖️ 使用本工具产生的一切风险需自行承担

🚫 本项目未授权任何个人、商家、自媒体账号等进行售卖

🚫 若您遇到商家使用本软件进行代练并收费，产生的任何问题及后果与本软件无关

🚫 开发者团队不会为您提供任何"售后"服务

## 🌟 贡献者们

<div>

感谢参与项目的所有贡献者 ([完整名单](https://github.com/DoctorReid/ZenlessZoneZero-OneDragon/graphs/contributors))：

![Contributors](https://contrib.rocks/image?repo=DoctorReid/ZenlessZoneZero-OneDragon&columns=12)

是你们的参与共同构建了这个项目，让这个项目越来越好♡
如果想要参与开发，欢迎查看 [贡献者招募](../docs/develop/RECRUITING.md)（技术栈 / 参与方式 / 成长故事），并按 [快速开始](../docs/develop/setup/quickstart.md) 把项目跑起来，期待你的加入 ✨

</div>

## 📢 官方渠道

<div>

🔗 [官方网站](https://one-dragon.com/)
> 项目简介 | 新手入门 | 功能说明 | 配置说明

💬 [官方频道](https://pd.qq.com/g/onedrag00n)
> 公告要闻 | 软件科研 | 游戏攻略 | 趣闻分享

❓ [官方文档](https://docs.qq.com/doc/p/7add96a4600d363b75d2df83bb2635a7c6a969b5)
> 常见问题 | 故障速查 | 疑难诊断 | 更新指南

👉 [官方议题](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/issues)
> 故障反馈 | 问题讨论 | 优化建议 | 建言收集

👨‍👨‍👦‍👦 玩家社群
> 闲聊吹水 | 趣闻互动 | 交流同好 | 晒卡吸欧
>
> 见频道关联群聊，入群前先对该项目点个 ⭐star，入群问题的项目仓库的答案为ZenlessZoneZero-OneDragon，入群后需校验你的 Github 账号的 username
>
> 提示：推荐 GitHub 用户加入，并注意 username 和 Name 的区别，请遵守群规，友善交流

</div>

## 📝 签名策略 (Code Signing Policy)

免费代码签名由 [SignPath.io](https://signpath.io/) 提供，证书由 [SignPath Foundation](https://signpath.org/) 颁发

审批人：[DoctorReid](https://github.com/DoctorReid) [ShadowLemoon](https://github.com/ShadowLemoon)

Free code signing provided by [SignPath.io](https://signpath.io/), certificate by [SignPath Foundation](https://signpath.org/)

Approvers: [DoctorReid](https://github.com/DoctorReid) [ShadowLemoon](https://github.com/ShadowLemoon)

## 🔒 隐私政策 (Privacy Policy)

本程序的隐私政策详见：[隐私政策](PRIVACY.md)。

See the [Privacy Policy](PRIVACY.md).

## ☕ 支持我们

<div align="center">

如果本项目为您带来便利，欢迎支持服务器维护：

<img src="./image/sponsor.png" width = "480" alt="图片名称" align=center />

💖 查看[致谢名单](https://onedragon-anything.github.io/other/zh/like/thanks_2024.html)

</div>
