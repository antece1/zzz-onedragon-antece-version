# Application

应用，在一条龙框架中，通常是一系列 Operation 的组合，用于完成一个具体的任务。

> 相关文档：[Operation 操作模块](operation.md) | [应用插件系统架构](application_plugin_system.md) | [应用开发指引](../../guides/application_plugin_guide.md) | [应用设置开发指引](../../guides/application_setting_guide.md)

`Application`继承于[Operation](operation.md)，拥有`Operation`的相同的编排能力，并添加应用配置和运行记录等功能。

## 应用工厂

每个自定义应用，需要继承实现 `ApplicationFactory` 类，用于定义应用唯一标识和提供应用相关内容的创建方式。

## 自定义应用

需继承 `Application` 类，进行任务的操作编排。

(待补充更多说明)

## 应用配置

需继承 `ApplicationConfig` 类，用于定义应用所需配置。

应用配置的维度是 `app_id` + `instance_idx` + `group_id`，即应用(app_id)在不同的账号(instance_idx)和不同的应用组(group_id)中，可以有不同的配置。

```
config/
├── 01/                             # 实例01
│   ├── one_dragon/                 # 默认应用组 (group_id=one_dragon)
│   │   ├── _group.yml               # 应用组配置
│   │   ├── coffee.yml              # 咖啡应用配置 (如果在use_group_config中)
│   │   └── email.yml               # 邮件应用配置 (如果在use_group_config中)
│   ├── daily_tasks/                # 日常应用组
│   │   ├── _group.yml               # 应用组配置
│   │   ├── coffee.yml              # 咖啡应用在日常应用组中的配置
│   │   └── email.yml               # 邮件应用在日常应用组中的配置
│   └── farming/                    # 体力消耗应用组
│       ├── group.yml               # 应用组配置
│       └── coffee.yml              # 咖啡应用在体力消耗组中的配置
└── 02/                             # 实例02
    └── ...
```

## 运行记录

需继承 `ApplicationRunRecord` 类，用于定义应用的运行记录。

一个任务的运行记录应该是账号下唯一的，即不管分配到哪些应用组中，要完成的内容是固定的。

所以运行记录的维度是 `app_id` + `instance_idx`，即应用(app_id)在不同的账号(instance_idx)中，有不同的运行记录。

(待补充更多说明)


## 运行上下文

`ApplicationRunContext` 提供以下功能：

- 应用注册 - 所有需要运行的应用都将 `ApplicationFactory` 注册进来，后续用于获取应用相关内容。
- 提供应用运行记录的统一刷新接口。
- 返回 `ApplicationRunResult` 作为统一结束语义，`STOP` 只表示状态停止，具体结果通过 `RunFinishReason` 区分为正常完成、停止、失败和未启动。
- 通过 `last_run_result` 固化最近一次已经确定的运行终态，保证重复停止和并发收口复用首次结果，不重复派发 STOP 事件或覆盖结束原因。
- `last_application_result` 保存应用 `execute()` 返回的 `OperationResult`，用于读取应用自身的成功状态和状态文本；它与 `ApplicationRunResult` 分别表示应用执行结果和运行生命周期结果。
- GUI 的 `after_done` 配置和 CLI 的收尾参数只在一条龙入口使用：CLI 在 `run_application()` 返回后执行 finalizer；GUI 在 `AppRunner.finished` 回调中读取当时的 `after_done` 并执行 finalizer。
- 一条龙 GUI 使用 `after_done` 配置表达是否执行运行后动作：无操作、关闭游戏、关机、休眠或睡眠；通用 `ApplicationRunContext.run_application()` 只负责运行应用并返回 `ApplicationRunResult`，不负责关闭游戏、关机、休眠或睡眠。

通用 GUI 的 `AppRunner` 只保存本次异步调用返回的 `ApplicationRunResult`。一条龙界面在线程 `finished` 后读取当前 `after_done`，再决定是否调用一条龙专属 finalizer；普通应用和 `run_app_by_item()` 不执行结束后动作。

可以自由组合不同的应用成为一个应用组，每个应用组会有一个唯一标识 `group_id`。

默认会有一个 `gourp_id='one_dragon'` 的应用组。

### 应用组配置

使用 `ApplicationGroupConfig`，存放位置 `config/{instance_idx}/{group_id}/_group.yml`。

主要包含：

- 一个应用列表，说明了应用的运行顺序和是否启用。
- 完成后是否推送消息。
- `added_app_list`：用户在一条龙「任务列表」里手动新增的应用（只对默认应用组 one_dragon 有意义，见下方「一条龙任务列表」）。

### 应用组管理

使用 `ApplicationGroupManager` 获取具体的应用组配置。

默认应用组(one_dragon)会在初始化的注册应用后，添加到应用组管理器中。配置中从未出现过的新应用会按当前默认顺序显示在列表头部，初始为禁用状态，但不会立即写入用户保存的应用顺序；用户第一次启用、拖拽排序或点击运行后才会保存，拖拽排序会保存当前显示的完整顺序，点击运行会保存被运行应用的当前位置。已有应用的顺序和启用状态保持不变。

### 应用组运行

通过 `GroupApplication` 执行一个应用组，该类提供：

- 按顺序执行应用
- 完成后推送消息
- 开始运行时复制一份应用顺序（`_app_list`）再逐个执行，运行中界面新增 / 删除任务不会让下标错位、跳过任务；复制的是列表，配置项还是同一批对象，运行中改开关仍然生效。
- 从指定应用开始执行：运行前把 `ApplicationRunContext.group_start` 设成 `(账号实例下标, app_id)`，这个实例这次运行就从这个应用开始，前面的跳过；这个应用不管开关和完成标记都运行一次，后面的照常按开关和完成标记判断。值在读取后清空，只对一次运行有效，实例对不上时不生效（一条龙界面在运行结束时也会清空，防止没走到应用组时留到下次）。

### 一条龙任务列表

一条龙页面左侧的任务列表（`OneDragonRunInterface` + `AppRunList(enable_task_actions=True)`），参考 BetterGI 的一条龙任务列表：

- 标题栏「任务列表」右边有两个按钮：▷ 按顺序运行一条龙（运行中变成暂停），+ 新增任务。
- 每个任务的「更多」菜单和右键菜单都有：
  - 新增：从还不在列表里的应用（`standalone_apps` 里的）选一个加到末尾，默认打开开关，记到 `_group.yml` 的 `added_app_list`。加载默认应用组时，这些应用会和默认应用一起放进列表。
  - 删除：只有手动新增、并且现在不是默认一条龙应用的任务能删（「删除」只出现在这些任务的菜单里），删掉后从 `added_app_list` 和列表里去掉，应用自己的设置还在，可以再新增回来。默认一条龙任务不能删除（下次加载会自动补回），只能关掉开关。`remove_app()` 本身也会同步去掉 `added_app_list` 里的记录，所以 MCP 删除配置项也不会让任务在下次加载时冒回来。
  - 从此执行：从这个任务开始运行一条龙，见上面「应用组运行」。运行实例选「全部」、而当前账号没开「一条龙中运行」时，一条龙会先切到别的账号，这时不运行，提示先打开。
  - 清除标记：把这个任务今天的完成状态改回「未运行」，下次一条龙会重新运行它。只改完成状态（`update_status(STATUS_WAIT, only_status=True)`），不调用 `reset_record()`，所以不会清掉应用自己记的进度，例如恶名狩猎剩余次数、世界巡逻已完成路线。有些运行记录按自己的进度判断完成（重写了 `is_done` / `run_status_under_now`，如恶名狩猎本周次数用完），清了状态也还算完成，这时提示用「从此执行」强制运行。没有运行记录的任务（非默认一条龙应用）本来每次都会运行，没有标记可清。
- 一条龙运行中（含暂停）这四项和标题栏的 + 都锁住，处理函数里也会再检查一次，避免运行中改列表。
