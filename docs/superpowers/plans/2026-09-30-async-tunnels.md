# 启动隧道异步化审查与修复

审查发现：SSH 握手由子进程执行，但主线程逐条 Popen、socket.create_connection（每次连接超时 0.3 秒，DNS 另计）、stderr join（0.5 秒）和 stop_process wait（2+2 秒）。QTimer 回调仍在 GUI 主线程。启动窗口显示不会等待握手，但启动后的响应和轮询受这些同步工作影响。

方案：最多 8 个线程并行处理隧道 I/O，每条隧道最多一个运行任务，重复心跳不积压。后台独占运行对象；GUI 保留配置和状态快照。编辑、停用、删除、重连递增版本，过期结果不覆盖新状态。结果和日志由 GUI 定时器收取，避免跨线程操作 QWidget。退出时异步清理所有运行对象后退出 Qt。

- [x] 添加慢启动并行、慢探测隔离、GUI 日志线程、重复心跳合并、停用拒绝旧结果和退出清理测试。
- [x] 抽取单隧道健康检查，新增后台调度器，将所有 GUI 生命周期入口接入同一队列。
- [x] 运行全部离屏回归，更新说明，记录验证结果。

验证：`./.venv/Scripts/python.exe -m unittest discover -s tests -q`。不连接真实 SSH，不修改用户配置。

验证结果：45 项测试通过（13.158 秒），含真实本地子进程在启动中退出时的清理，以及连续编辑合并到最新配置。`tests/render_preview.py` 成功生成界面预览。状态回传只更新行内状态和在线计数，避免重建列表导致布局抖动。静态调用链检查确认 MainWindow 已不直接调用 start_process、stop_process、test_port_listening、drain_errors、wait 或 join。
