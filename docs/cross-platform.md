# 跨平台运行说明

源码已增加 Windows、macOS、Linux 桌面适配。当前本机回归环境为 Windows；CI 已配置三个系统，但尚未运行，不能视为 macOS/Linux 原生验收结果。需要 Python 3.12、桌面环境和 OpenSSH 客户端。

## 安装与启动

Windows 继续使用 README 中的 PowerShell 命令。macOS/Linux：

```sh
python3 scripts/dev.py
.venv/bin/python bin/ssh-tunnel-manager.py
.venv/bin/python scripts/test.py
```

`scripts/dev.py` 创建项目虚拟环境并安装 requirements.txt 中的依赖。不同系统不能共用同一个 `.venv`。Linux 需安装发行版所需的 Qt 图形运行库和中文字体；无桌面服务器不属于此 GUI 的支持范围。macOS 无边框库会安装其声明的 PyObjC/PyCocoa 依赖。尚未提供自包含的安装包，下文启动器只是指向虚拟环境的入口。

### macOS 双击启动

macOS 没有 `pythonw`，Finder 也不会执行脚本文件，所以 `.pyw` 不能像 Windows 那样直接双击。生成启动器 bundle：

```sh
.venv/bin/python scripts/make_macos_app.py
```

默认写入 `~/Applications/SSH Tunnel Manager.app`（`--output` 换目录、`--force` 覆盖、`--no-icon` 跳过图标）。双击该 `.app` 打开界面，可在 Dock 图标右键 →“选项 → 在 Dock 中保留”常驻。bundle 不声明任何扩展名关联——把 `.pyw` 全局指向本程序会改变机器上所有同类文件的行为。

已知边界：启动器记录生成时的解释器与入口绝对路径，移动项目或重建 `.venv` 后必须重跑脚本，解释器缺失时弹原生提示框。签名是 ad-hoc 的，`spctl` 判定为 `rejected`；本机自建文件没有隔离属性可直接双击，压缩分发后首次需右键 →“打开”，要免拦截须用 Developer ID 签名并公证。从启动器运行的进程仍以虚拟环境解释器为可执行文件，Dock 与菜单栏可能显示为 `python`；需要独立图标和应用名请改用真正的打包工具（如 PyInstaller、Briefcase），本仓库暂未提供。

## 平台行为

| 功能 | Windows | macOS | Linux 桌面 |
|---|---|---|---|
| SSH | 系统 OpenSSH 优先，再查 PATH | 查 PATH | 查 PATH |
| 登录自启 | 当前用户 Run 注册表项 | 用户 LaunchAgent | XDG autostart |
| 托盘菜单 | 保留任务栏定位 | 原生菜单 | 原生菜单 |
| RDP 快捷启动 | mstsc.exe | 复制地址到自行安装的客户端 | 复制地址到自行安装的客户端 |

无系统托盘时，关闭窗口会清理隧道后退出。非 Windows 平台使用桌面默认字体及字体回退。布局基准为 1100×660、展开工作区 1520×660 逻辑像素；窗口按当前屏幕可用区域自动收窄，工作区最少保留 320 像素，因此不会再超出小屏，但可用宽度不足 1100 像素时列表列宽仍会受挤压。

## 数据目录

运行数据根目录按以下优先级选择，资源始终从程序目录读取：

1. `--data-dir "绝对目录"`，或环境变量 `SSH_TUNNEL_MANAGER_HOME`；命令行优先。
2. 项目 `data/` 已有 settings.json、tunnels.json 或 ssh_connections.json 时，继续沿用项目目录，不自动复制、移动或覆盖旧数据。
3. 新安装使用用户目录：
   - Windows：`%LOCALAPPDATA%\SshTunnelManager`
   - macOS：`~/Library/Application Support/SshTunnelManager`
   - Linux：`$XDG_DATA_HOME/ssh-tunnel-manager`，默认 `~/.local/share/ssh-tunnel-manager`；忽略相对 XDG 路径。

根目录下 `data/` 保存 JSON、`logs/` 保存日志。示例：

```sh
.venv/bin/python bin/ssh-tunnel-manager.py --data-dir "/path/to/tunnel-data"
```

显式选择空目录不会导入其他位置的数据。保留项目便携模式时，项目目录仍须可写。若从只读安装位置运行，请将旧 data/、logs/ 自行复制到可写根目录后使用 `--data-dir`。跨系统复制配置后需重新选择 SSH 可执行文件和私钥路径。根目录旧版 JSON 迁移可使用 `python scripts/migrate_layout.py --apply`；无参数时只预览。

## 登录自启

保存当前解释器、入口和数据目录的绝对路径，下次登录桌面生效，不立即启动第二个实例。移动程序或虚拟环境后重新保存设置。

- Windows：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 下的 `SshTunnelManager`。
- macOS：`~/Library/LaunchAgents/local.ssh-tunnel-manager.plist`，RunAtLoad、Aqua 会话，不设置 KeepAlive。
- Linux：`$XDG_CONFIG_HOME/autostart/ssh-tunnel-manager.desktop`，默认 `~/.config/autostart/`。

程序只管理自己的自启项。测试使用临时目录及假注册表，不启用本机自启动。macOS 系统策略或用户禁用登录项、Linux 桌面的自启策略仍可能阻止运行。配置开关表示本程序自启文件已配置，并不保证系统已允许运行。

实现依据：[Apple LaunchAgent 文档](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)、[XDG 自启规范](https://specifications.freedesktop.org/autostart/latest/)、[Desktop Entry 命令转义规则](https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html)。

## 验证范围

Windows/Linux 测试使用 Qt offscreen；macOS 无边框窗口需要 Cocoa 原生句柄，测试必须在登录的图形会话运行，可能短暂显示测试窗口。测试使用临时数据，不连接真实 SSH 主机。CI 覆盖 Python 3.12、windows-latest/macOS-latest/ubuntu-latest；未触发 CI 前不视为验证通过。

原生验收清单：三系统真实 OpenSSH 连接与重连；登录自启开关；托盘恢复和退出；无托盘退出；中文与空格路径；HiDPI、多显示器；Linux X11/Wayland。Windows 本机单测不替代此清单。
