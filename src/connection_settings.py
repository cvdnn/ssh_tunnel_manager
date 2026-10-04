"""Draft-only connection editing and explicitly confirmed SSH config export."""
from copy import deepcopy
from collections.abc import Mapping
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QListWidget,
    QListWidgetItem, QLineEdit, QPushButton, QDialog, QPlainTextEdit, QSizePolicy,
)

import ssh_connections as backend


class ExportPreviewDialog(QDialog):
    def __init__(self, preview, parent):
        super().__init__(parent)
        self.preview = preview
        self.setWindowTitle('确认写入 SSH 配置')
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        notice = QLabel('此操作立即写入 SSH 配置，与设置页保存独立。取消设置不会撤销本次导出。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        path = QLineEdit(str(preview['path']))
        path.setReadOnly(True)
        layout.addWidget(path)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(preview['text'])
        layout.addWidget(text)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        self.write_button = QPushButton('确认写入')
        self.write_button.clicked.connect(self.write)
        row.addWidget(cancel)
        row.addWidget(self.write_button)
        layout.addLayout(row)

    def write(self):
        try:
            backup = backend.export_connections(self.preview)
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc))
            return
        self.write_button.setEnabled(False)
        self.parent().export_completed(backup)
        self.accept()


class ConnectionSettingsEditor(QWidget):
    exported = Signal()
    FIELD_LABELS = {
        'name': '连接名称', 'host': '主机 / IP', 'port': '端口',
        'user': '用户名（可选）', 'identityFile': '密钥路径（可选）',
        'proxyJump': '跳板机（可选）', 'exportAlias': '导出别名（ASCII，可选）',
        'connectTimeout': '连接超时秒数（可选）',
        'serverAliveInterval': '保活间隔秒数（可选）',
        'serverAliveCountMax': '保活重试次数（可选）',
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._draft = []
        self._hosts = []
        self._references = set()
        self._reference_names = {}
        self._config_path = ''
        self._active = None
        self._dirty = False
        self._updating = False
        self._new = False
        self._export_dialog = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel('SSH 连接来源')
        layout.addWidget(heading)
        self.config_path_label = QLabel()
        self.config_path_label.setMinimumWidth(0)
        self.config_path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.config_path_label)
        self.refresh_button = QPushButton('刷新配置来源')
        self.refresh_button.clicked.connect(lambda: self.set_config_path(self._config_path))
        layout.addWidget(self.refresh_button)
        self.sources = QListWidget()
        self.sources.setMinimumWidth(0)
        self.sources.setFixedHeight(140)
        self.sources.currentItemChanged.connect(self._selection_changed)
        layout.addWidget(self.sources)
        hint = QLabel('SSH 配置来源只读；勾选手动连接可导出。手动连接在设置页保存后持久化。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        actions = QHBoxLayout()
        self.new_button = QPushButton('新建')
        self.new_button.clicked.connect(self.new_record)
        self.apply_button = QPushButton('应用到草稿')
        self.apply_button.clicked.connect(self.apply_form)
        self.delete_button = QPushButton('删除')
        self.delete_button.clicked.connect(self.delete_selected)
        for button in (self.new_button, self.apply_button, self.delete_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.fields = {}
        for key, label in self.FIELD_LABELS.items():
            field = QLineEdit()
            field.setMinimumWidth(0)
            field.textChanged.connect(self._mark_dirty)
            self.fields[key] = field
            form.addRow(label, field)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #B42318;')
        layout.addWidget(self.error)
        self.export_button = QPushButton('预览并导出勾选连接…')
        self.export_button.clicked.connect(self.open_export)
        layout.insertWidget(layout.indexOf(hint) + 1, self.export_button)
        notice = QLabel('导出立即写入 SSH 配置，与设置保存独立。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self._show_record(None)

    def load(self, records, hosts, config_path, referenced_ids=()):
        self._draft = deepcopy(records)
        self._hosts = list(hosts)
        self._config_path = str(config_path)
        self._references = set(referenced_ids)
        self._reference_names = deepcopy(dict(referenced_ids)) if isinstance(referenced_ids, Mapping) else {}
        self._active = None
        self._dirty = self._new = False
        self.error.clear()
        self.status.clear()
        self._show_config_path()
        self._rebuild()
        self._show_record(None)

    def records(self):
        self._apply()
        self._rebuild()
        return deepcopy([backend.validate_connection(record) for record in self._draft])

    def set_config_path(self, path):
        self._config_path = str(path)
        self._show_config_path()
        try:
            hosts = backend.discover_hosts(path)
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc))
            self._hosts = []
            self._rebuild()
            return
        self._hosts = hosts
        self._rebuild()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The workspace width follows the screen, so elide against the real space.
        self._show_config_path()

    def _show_config_path(self):
        text = '入口文件：' + self._config_path
        available = max(120, self.width() - 24)
        self.config_path_label.setText(self.config_path_label.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideMiddle, available))
        self.config_path_label.setToolTip(text)

    def _mark_dirty(self):
        if not self._updating:
            self._dirty = True

    def _rebuild(self):
        checked = {
            self.sources.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.sources.count())
            if self.sources.item(i).checkState() == Qt.CheckState.Checked
        }
        self.sources.blockSignals(True)
        self.sources.clear()
        for host in self._hosts:
            item = QListWidgetItem(f'{host}  [SSH 配置 · 只读]')
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole, 'config:' + host)
            self.sources.addItem(item)
            if 'config:' + host == self._active:
                self.sources.setCurrentItem(item)
        for record in self._draft:
            item = QListWidgetItem(f'{record["name"]}  [手动 · {record["host"]}]')
            key = 'manual:' + record['id']
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in checked else Qt.CheckState.Unchecked)
            self.sources.addItem(item)
            if key == self._active:
                self.sources.setCurrentItem(item)
        self.sources.blockSignals(False)

    def _show_record(self, record, editable=False):
        self._updating = True
        for key, field in self.fields.items():
            field.setText(str((record or {}).get(key, '')))
            field.setEnabled(editable)
        self.apply_button.setEnabled(editable)
        self.delete_button.setEnabled(editable and not self._new)
        self._updating = False
        self._dirty = False

    def _selection_changed(self, current, previous):
        if current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        try:
            self._apply()
        except ValueError as exc:
            self.error.setText(str(exc))
            self.sources.blockSignals(True)
            self.sources.setCurrentItem(previous)
            self.sources.blockSignals(False)
            return
        self._active = key
        self._new = False
        if key.startswith('manual:'):
            record = next(r for r in self._draft if 'manual:' + r['id'] == key)
            self._show_record(record, True)
        else:
            self._show_record({'name': key[7:], 'host': key[7:]})
        self._rebuild()
        self.error.clear()

    def _apply(self):
        if not self._dirty:
            return
        values = {key: field.text().strip() for key, field in self.fields.items() if field.text().strip()}
        values['id'] = self._active[7:]
        try:
            record = backend.validate_connection(values)
        except ValueError as exc:
            self.error.setText(str(exc))
            raise
        if self._new:
            self._draft.append(record)
        else:
            index = next(i for i, r in enumerate(self._draft) if r['id'] == record['id'])
            self._draft[index] = record
        self._dirty = self._new = False
        self.delete_button.setEnabled(True)
        self.error.clear()

    def apply_form(self):
        try:
            self._apply()
        except ValueError:
            return
        self._rebuild()

    def new_record(self):
        try:
            self._apply()
        except ValueError:
            return
        self.sources.blockSignals(True)
        self.sources.setCurrentRow(-1)
        self.sources.blockSignals(False)
        self._active = 'manual:' + uuid.uuid4().hex
        self._new = True
        self._show_record({'port': 22}, True)

    def delete_selected(self):
        if not self._active or not self._active.startswith('manual:'):
            return
        if self._active in self._references:
            names = self._reference_names.get(self._active, [])
            details = '（' + '、'.join(names) + '）' if names else ''
            self.error.setText(f'此连接被隧道引用{details}，请先修改或删除引用它的隧道。')
            return
        self._draft = [r for r in self._draft if 'manual:' + r['id'] != self._active]
        self._active = None
        self._dirty = self._new = False
        self._rebuild()
        self._show_record(None)
        self.error.clear()

    def open_export(self):
        try:
            records = self.records()
            selected = {
                self.sources.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.sources.count())
                if self.sources.item(i).checkState() == Qt.CheckState.Checked
            }
            records = [r for r in records if 'manual:' + r['id'] in selected]
            if not records:
                raise ValueError('请先勾选需要导出的手动连接。')
            preview = backend.preview_export(self._config_path, records)
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc))
            return None
        self._export_dialog = ExportPreviewDialog(preview, self)
        self._export_dialog.setModal(True)
        self._export_dialog.open()
        return self._export_dialog

    def export_completed(self, backup=None):
        self.set_config_path(self._config_path)
        self.status.setText(f'导出成功。原配置备份：{backup}' if backup else '导出成功，已创建 SSH 配置文件。')
        self.status.setToolTip(self.status.text())
        self.exported.emit()
