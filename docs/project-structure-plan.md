# 项目结构审查与迁移规划

日期：2026-10-04。状态：第一阶段目录与路径迁移已实施；第二阶段主程序拆分待后续开展。

## 审查结论

当前适合从“根目录脚本应用”整理为“薄启动入口 + src 下的应用模块 + 开发脚本 + 独立运行数据”。
根目录只有 3 个应用代码文件，问题主要是职责混放和主程序过大，而不是文件数量本身。
`ssh_tunnel_manager.pyw` 约 106 KB、超过 2400 行，混合启动动画、注册表操作、隧道进程与线程池、配置读写和窗口控件。
工作区存在尚未提交的连接管理功能及测试；后续迁移必须以当前工作树为基线，不能用 HEAD 覆盖这些变更。

| 现状与证据 | 迁移影响 |
| --- | --- |
| 主程序第 233–237 行通过 `__file__` 定位配置、日志和图标 | 单独移动文件会切换数据路径，可能表现为配置丢失 |
| `StartupSplashProcess` 第 160 行直接执行当前文件 | 改为从启动器导入应用模块后，需要显式保留启动动画调度 |
| `set_autostart` 第 289 行把当前文件写入注册表 | 新入口与已有自启命令需要兼容 |
| `MainWindow` 第 1626 行从 settings 路径推导手动连接文件 | 三份用户配置应统一使用同一数据目录 |
| 测试入口通过 `SourceFileLoader` 加载根目录 `.pyw` | 应从 src 导入应用模块，同时调整 mock 的目标模块 |
| 三个 `render_*.py` 放在 tests 中并导入测试模块 | 预览工具应移入 scripts，共享夹具需独立 |
| `render_preview.py` 第 27 行读取本机 tunnels.json | 应使用脱敏夹具，使干净检出也可生成预览 |
| settings.json 已被 Git 跟踪，另两份用户配置被忽略 | 个人设置与可提交的示例需要分离 |
| app_logo.png 与 LOGO_FILE 存在，但当前图标由代码绘制 | 保留资源并集中管理，本轮不判定为可删除文件 |

## 方案比较

1. **推荐：src 平铺模块，分阶段迁移。** bin 仅承载启动器，scripts 放开发维护工具，src 直接放应用实现和资源。职责清楚，可独立完成目录整理，再拆大文件。代价是需要统一导入与路径管理。
2. 仅增加 bin/scripts，把全部程序移到 bin。改动少，但应用逻辑继续与入口混放，也无法解决主程序过大的问题。
3. 一次性完成完整分层、安装发行、AppData 数据迁移。长期更适合正式发行，但扩大本次范围，路径、打包和模块拆分会同时影响回归定位，暂不采用。

## 第一阶段目标结构

```text
ssh_tunnel_manager/
├── README.md
├── requirements.txt                 # 保留当前已验证的固定依赖版本
├── .gitignore
├── bin/
│   └── ssh-tunnel-manager.pyw        # 无业务逻辑的启动入口
├── src/
│   ├── bootstrap.py                 # 启动动画与主窗口启动顺序
│   ├── paths.py                     # 资源、数据、日志路径的唯一来源
│   ├── app.py                       # 主程序主体，先保留现有职责
│   ├── ssh_connections.py
│   ├── connection_settings.py
│   └── assets/
│       └── app_logo.png
├── scripts/
│   ├── dev.ps1                     # 创建/使用 .venv 并安装依赖
│   ├── test.ps1                    # 统一验证入口
│   ├── migrate-layout.ps1          # 一次性数据迁移，支持预览与冲突检查
│   └── preview/
│       ├── support.py              # 预览专用初始化与假数据支持
│       ├── render_preview.py
│       ├── render_status_preview.py
│       └── render_connections_preview.py
├── config/
│   └── examples/                   # 脱敏示例，仅文档用途
│       ├── settings.example.json
│       ├── tunnels.example.json
│       └── ssh_connections.example.json
├── data/                           # 本机运行数据，Git 忽略
│   ├── settings.json
│   ├── tunnels.json
│   └── ssh_connections.json         # 有手动连接时产生
├── logs/                           # Git 忽略
│   └── ssh_tunnel.log
├── tests/
│   ├── support.py                  # 测试对象工厂、mock 和 Qt 测试支持
│   ├── fixtures/                   # 可提交的脱敏测试/预览输入
│   └── test_*.py                   # 现有测试先保持平铺
├── artifacts/
│   └── ui/                         # 已有界面截图继续保留
└── docs/
    ├── project-structure-plan.md
    └── superpowers/                # 保留历史设计和实施记录
```

`.venv/`、`__pycache__/`、`.review-backup/` 属于忽略的本地环境、缓存与历史备份，不纳入源码架构；不在本次自动删除。
根目录旧 `ssh_tunnel_manager.pyw` 可在过渡期保留为转发新入口的薄兼容文件，避免已有快捷方式和自启路径立即失效；确认入口切换后再移除。

## 目录与依赖规则

- **bin：** 用户日常启动的薄入口；不保存配置、不实现 GUI 或 SSH 逻辑。明确要求使用项目虚拟环境；双击 `.pyw` 仍依赖 Windows 文件关联，不能宣传为自动选择 `.venv`。
- **scripts：** 环境初始化、测试、预览、迁移工具；应用运行时不得导入 scripts 或 tests。
- **src：** 应用模块直接位于 src 下，不再创建同名包目录；模块之间使用绝对导入。bin 入口只在一处将 src 加入模块搜索路径，测试入口也统一设置 src 路径，业务模块不自行修改 `sys.path`。
- **config/examples：** 可版本控制的配置说明；真实配置只存 data。示例不得默认为可自动连接的真实隧道。
- **data/logs：** 当前推荐继续采用仓库内便携部署方式；绝不通过当前工作目录 CWD 推导位置。paths.py 在本阶段明确以源码部署根目录定位，集中提供路径，且不在导入时创建文件。
- **assets：** 位于 src/assets，使用 paths.py 从源码位置读取；与可写数据路径分开。第一阶段不承诺 wheel/冻结程序发行，正式发行时再设计 AppData 与打包适配。
- **tests：** 不再从另一个 `test_*.py` 导入共享工厂；预览也不导入测试用例。预览需要的通用样例可读取 tests/fixtures，但运行初始化由 scripts/preview/support.py 自己提供。
- **artifacts/ui：** 保留现有跟踪截图；后续可把认可基线与临时输出分开，避免本轮无必要地搬动历史产物。

requirements.txt 继续作为精确版本约束的唯一维护位置。本阶段无需为平铺源码添加打包元数据；正式发行时再设计 pyproject.toml。
新增脚本时从脚本自身位置解析仓库及 `.venv`，正确处理含空格路径，并透传程序参数和退出码。

## 原文件迁移映射

| 当前文件 | 目标 |
| --- | --- |
| ssh_tunnel_manager.pyw | 主体 → src/app.py；入口 → bin 与 src/bootstrap.py；旧文件暂作兼容启动器 |
| ssh_connections.py | src/ssh_connections.py |
| connection_settings.py | src/connection_settings.py |
| app_logo.png | src/assets/app_logo.png |
| settings.json、tunnels.json、ssh_connections.json（如存在） | data/，保留字节内容，不重新生成 |
| ssh_tunnel.log | logs/，保留已有日志 |
| tests/render_*.py | scripts/preview/，同时解耦测试模块依赖 |
| tests/test_*.py | 原位保留，更新导入与必要的 mock 路径 |
| README.md、requirements.txt、docs/、artifacts/ | 保留目录位置；只更新当前使用说明 |

## 关键兼容设计

### 启动链

所有入口调用 bootstrap。bootstrap 先识别启动动画子进程参数，再决定是否加载主界面；子进程不能递归拉起主应用。
正常启动先创建启动动画进程，然后延迟导入主 GUI。不要在 bootstrap 顶层导入 app，破坏现有加载反馈。
子进程使用当前 Python 的绝对路径和统一 bin 入口协议（例如 `bin/ssh-tunnel-manager.pyw --startup-splash`），覆盖 python.exe 与 pythonw.exe。
不能只把原文件改成 app.py 然后 import：现有模块中段的 `if __name__ == '__main__'` 不会随导入执行。

### 数据迁移与自启

迁移时应用应先正常退出，避免两个版本分别写入根目录与 data。
迁移工具先预览源/目标、检查路径与冲突，再备份并复制配置；成功校验后才清理旧运行文件。
目标已存在时不覆盖；新旧同时存在且不同则明确报告冲突。缺失可选文件正常跳过，损坏 JSON 报错并保留原件。
迁移可重复运行，失败保留原件和备份。路径层发现仅有旧数据时提示迁移，不能静默启动空配置，也不长期维持双目录写入。
settings.json 从 Git 跟踪中移除应与脱敏示例加入同批完成，不能用示例替换用户原文件。
已有自启由兼容启动器维持；用户通过应用设置重新保存自启时写入虚拟环境解释器和 bin 入口的绝对路径。不要因调整源码目录就额外修改真实注册表。

## 第二阶段：按职责拆主程序

目录迁移通过回归后，再逐组提取，避免搬目录与行为重构同时进行：

```text
src/
├── core/
│   ├── tunnels.py          # TunnelItem、SSH 命令与进程生命周期
│   ├── workers.py          # TunnelWorkerPool、任务排队与过期结果过滤
│   ├── connections.py      # 现 ssh_connections.py
│   └── storage.py          # 原子写入与配置加载共用逻辑
├── platform/
│   └── windows.py          # 注册表、自启、解释器与系统 SSH 定位
└── ui/
    ├── main_window.py
    ├── tunnel_workspace.py
    ├── settings_workspace.py
    ├── connection_settings.py
    ├── status_widgets.py
    └── splash.py
```

bootstrap 只负责组装；UI 可依赖 core，core 不反向依赖窗口控件；Windows 系统操作从业务与 UI 中隔离。
workers 可以继续依赖 Qt 的 QObject/Signal，无需为了层次整齐强行改成纯 Python。
按上述现有职责建立模块即可，不预建空的 services/repositories/controllers 等抽象层。

## 实施顺序与验收

1. 记录现有未提交变更与测试基线；保护本机配置，避免清理或覆盖现有功能。
2. 增加 paths、bootstrap 和薄入口；移动应用模块并修正内部导入。数据路径暂时兼容原位置。
3. 更新测试导入与 mock；将共享支持从测试用例提取；移出预览脚本并改用脱敏夹具。
4. 实现并验证数据迁移工具；在应用退出后迁移本机配置/日志，切换为 data/logs；更新忽略规则和示例。
5. 更新 README 启动、测试、文件说明；保留历史设计文档原文，不批量替换历史路径。核对兼容入口和自启设置行为。
6. 第一阶段验收通过后，另行开展第二阶段模块拆分，每组拆分后运行对应回归。

第一阶段完成标准：

- 根目录无业务实现与活动配置/日志，只保留项目元数据、说明和临时兼容入口。
- 从项目根目录和任意其他目录启动，读取相同配置；覆盖含空格的路径。
- 新入口与兼容入口均可用；启动页动画、管道 EOF 退出与异常清理保持原行为。
- 旧三份配置内容和连接 ID 保持不变；测试迁移缺文件、冲突、损坏、失败回滚与重复运行。
- 现有 unittest 全套回归、src 模块语法编译与 pip check 通过；注册表变更与 SSH 启动使用 mock 验证。
- Qt offscreen 预览不依赖个人配置，不连接真实 SSH，截图输出仍在 artifacts/ui。
- 单独评估第二阶段，不把“大文件已拆分”列作第一阶段已完成事项。

实施记录：应用模块和资源已迁入 `src/`，入口位于 `bin/`，开发工具位于 `scripts/`；本机配置与日志已迁入 `data/`、`logs/`，SHA-256 与迁移前一致，并在 `.review-backup/layout/` 留有本地备份。根目录旧 `.pyw` 保留为兼容入口。测试和预览脚本已更新，第二阶段主程序拆分尚未执行。未创建 Git 提交。
