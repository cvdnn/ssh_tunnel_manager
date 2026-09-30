# 隧道管家审阅问题修复计划

**目标：** 修复已审阅确认的地址失效、误报连接成功、SSH 错误丢失和托盘状态不完整问题，补充可复现的安装及测试说明。

**方案：** 保留现有单文件 GUI 结构与 JSON 配置格式。SSH 绑定地址、探测地址和客户端地址采用统一规则（通配监听地址在本机连接时转换为回环地址，IPv6 转发参数加方括号）。只有 SSH 进程存活且端口可连接时进入 Connected；启动阶段保留等待期限，避免每次轮询杀掉尚在连接的进程。后台线程读取 stderr，主线程取出并写入现有日志，确保不跨线程操作 Qt 控件。托盘分别表示停用、在线、连接中及断开。

**技术：** Python、PySide6、QFluentWidgets、OpenSSH、unittest。

用户已授权修复上一轮列出的问题，直接在当前非 Git 目录执行。不创建仓库，不修改用户现有隧道规则或开机启动设置。

- [x] 在本地 `.venv` 安装 GUI 依赖；为待修改文件保留备份。
- [x] 在 `tests/test_tunnel_manager.py` 编写回归测试：绑定/复制/RDP 地址、探测失败、启动日志、存活进程与端口联合判断、连接等待、stderr 捕获及清理、托盘颜色。
- [x] 执行 `.venv\Scripts\python.exe -m unittest discover -s tests -v`，确认旧实现失败原因与审阅一致。
- [x] 修改 `ssh_tunnel_manager.pyw`，逐项修复，并执行回归测试。
- [x] 添加 `requirements.txt` 和 `.gitignore`，更新 `README.md` 的安装、真实配置、状态含义及排错说明。
- [x] 执行全套测试、Python 编译检查和 `pip check`；用隔离配置完成 Qt 窗口构造检查，不连接真实 SSH 主机。

验收边界：自动化测试覆盖地址参数、状态流转、子进程错误和 Qt 控件构造；真实远端服务可达性不由本地监听成功保证，文档需明确。

## 验证记录

- 25 项 unittest 全部通过，包含真实本地子进程、stderr 大量输出、线程启动失败清理与 Qt offscreen 窗口构造。
- `py_compile` 通过；`pip check` 返回 `No broken requirements found.`。
- 独立审阅未发现 P1/P2 问题；其发现的线程启动失败清理问题已补回归测试并修复。
- `settings.json`、`tunnels.json`、`ssh_tunnel.log` 的最终 SHA256 与修改前一致。
- 用 `-W error::ResourceWarning` 测试时，退出阶段出现 `gc: 53 uncollectable objects`。单独执行 `python -W error::ResourceWarning -c 'import qfluentwidgets'` 也可复现，尚未归因到第三方库内部具体对象；没有将该警告作为本次子进程清理失败处理。
