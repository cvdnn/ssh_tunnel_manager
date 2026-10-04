"""Render the actual Item widgets in all stages; no live connections."""
from pathlib import Path
from support import tm, tunnel, ROOT
from PySide6.QtGui import QFontDatabase

app = tm.QApplication.instance() or tm.QApplication([])
for name in ("segoeui.ttf", "msyh.ttc", "msyhbd.ttc", "consola.ttf"):
    QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
panel = tm.QWidget()
panel.setStyleSheet("background: #f8fafc;")
layout = tm.QVBoxLayout(panel)
heading = tm.QLabel("隧道状态 · 异步连接与检测")
heading.setFont(tm.QFont("Microsoft YaHei UI", 14))
layout.addWidget(heading)
states = [
    ("等待后台任务", "Disconnected", "start", "queued", 0),
    ("创建 SSH 进程", "Connecting", "start", "starting", 0),
    ("等待本地入口", "Connecting", None, None, 0),
    ("检查进程存活", "Connected", "check", "checking", 0),
    ("探测本地端口", "Connected", "check", "probing", 0),
    ("自动恢复连接", "Connecting", "check", "starting", 2),
    ("等待重连结果", "Connecting", None, None, 2),
    ("本地入口就绪", "Connected", None, None, 0),
    ("连接已断开", "Disconnected", None, None, 0),
    ("正在回收进程", "Stopped", "stop", "stopping", 0),
    ("正在移除隧道", "Stopped", "remove", "stopping", 0),
    ("隧道已停用", "Stopped", None, None, 0),
]
for i, (name, status, action, phase, retry) in enumerate(states):
    item = tunnel(name=name, enabled=status != "Stopped")
    item.status, item.activity, item.phase, item.retry_count = status, action, phase, retry
    layout.addWidget(tm.TunnelRowWidget(i, item))
panel.resize(1050, 100)
panel.show()
app.processEvents()
out = ROOT / "artifacts" / "ui"
out.mkdir(parents=True, exist_ok=True)
panel.grab().save(str(out / "status-icons.png"))
print(out / "status-icons.png")
panel.close()
