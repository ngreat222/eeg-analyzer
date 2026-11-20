"""Channel choices taken from the current recording or analysis scope."""
import re

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLineEdit,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .widgets import btn, checkbox, hbox, label


def channel_field(section, caption, editor, button_text, open_dialog):
    row = QWidget()
    editor.setAccessibleName(caption)
    layout = hbox(editor, btn(button_text, open_dialog))
    layout.setStretch(0, 1)
    row.setLayout(layout)
    section.row(caption, row)
    return editor


_REGIONS = {"额区":"Fp|AF|F", "中央区":"FC|C|CP", "颞区":"FT|T|TP", "顶区":"P", "枕区":"PO|O|I"}


class ChannelPicker(QDialog):
    def __init__(self, parent, title, description, names, types, selected, hints=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        available = (parent.screen() if parent else self.screen()).availableGeometry()
        self.resize(min(600, available.width()-32), min(580, available.height()-64))
        layout = QVBoxLayout(self)
        layout.addWidget(label(description, "Sub", wrap=True))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索通道名称或类型")
        self.search.setAccessibleName("搜索通道")
        self.group = QComboBox()
        self.group.setAccessibleName("通道范围")
        self.group.addItem("全部通道")
        self.filters = {}
        for kind in dict.fromkeys(types):
            self.filters[kind.upper()] = {n for n,t in zip(names, types) if t == kind}
        for region, prefix in _REGIONS.items():
            pattern = rf"(?:EEG\s+)?(?:{prefix})(?:z|\d+)(?:[-_](?:REF|LE|RE))?"
            hits = {n for n,t in zip(names, types) if t == "eeg" and re.fullmatch(pattern, n, re.I)}
            if hits:
                self.filters[region] = hits
        self.group.addItems(list(self.filters))
        layout.addLayout(hbox(self.search, self.group))
        self.table = QTableWidget(len(names), 3 if hints is not None else 2)
        self.table.setHorizontalHeaderLabels(["通道","类型"] + (["说明"] if hints is not None else []))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(60)
        selected = set(selected)
        for row, (name, kind) in enumerate(zip(names, types)):
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in selected else Qt.Unchecked)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(kind.upper()))
            if hints is not None:
                self.table.setItem(row, 2, QTableWidgetItem(hints.get(name, "")))
        self.table.resizeColumnsToContents()
        metrics = self.table.fontMetrics()
        widest = max((metrics.horizontalAdvance(t.upper()) for t in types), default=0)
        self.table.setColumnWidth(1, max(76, widest + 32, self.table.columnWidth(1) + 20))
        if hints is not None:
            self.table.setColumnWidth(0, max(116, self.table.columnWidth(0) + 24))
        self.table.horizontalHeader().setSectionResizeMode(2 if hints is not None else 0, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.actions = hbox(btn("勾选筛选结果", lambda: self._all(True)),
                            btn("取消勾选筛选结果", lambda: self._all(False)),
                            btn("清空全部", lambda: self._all(False, visible_only=False)), "stretch")
        layout.addLayout(self.actions)
        self.summary = label("", "Sub", wrap=True)
        layout.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("应用")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.table.itemChanged.connect(self.update_count)
        self.search.textChanged.connect(self.filter_rows)
        self.group.currentTextChanged.connect(self.filter_rows)
        self.update_count()

    def selected_names(self, checked=True):
        return [self.table.item(i, 0).text() for i in range(self.table.rowCount())
                if (self.table.item(i, 0).checkState() == Qt.Checked) == checked]

    def filter_rows(self, *_):
        subset = self.filters.get(self.group.currentText())
        term = self.search.text().strip().casefold()
        for i in range(self.table.rowCount()):
            name = self.table.item(i, 0).text()
            text = name + " " + self.table.item(i, 1).text()
            self.table.setRowHidden(i, term not in text.casefold() or subset is not None and name not in subset)
        self.update_count()

    def _all(self, on, visible_only=True):
        with QSignalBlocker(self.table):
            for i in range(self.table.rowCount()):
                if not visible_only or not self.table.isRowHidden(i):
                    self.table.item(i, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.update_count()

    def update_count(self, *_):
        names = self.selected_names()
        visible = sum(not self.table.isRowHidden(i) for i in range(self.table.rowCount()))
        preview = "、".join(names[:8]) + ("…" if len(names) > 8 else "")
        self.summary.setText(f"已选 {len(names)} / {self.table.rowCount()} 个，当前显示 {visible} 个" + (f"  ·  {preview}" if names else ""))


class ChannelDialog(ChannelPicker):
    """Checked channels are retained with their actual type."""
    def __init__(self, parent, names, types, hint, non_eeg, n_recs):
        selected = set(names) - set(non_eeg)
        super().__init__(parent, "检查通道", "勾选保留的通道。未勾选的通道将在处理时去除。",
                         names, types, selected, hint)
        self.actions.insertWidget(self.actions.count()-1, btn("恢复自动识别", lambda: self._auto(hint)))
        self.apply_all = checkbox(f"将本次勾选变更同步到其他记录中的同名通道（共 {n_recs} 条记录）", False)
        self.apply_all.setVisible(n_recs > 1)
        self.layout().insertWidget(self.layout().count()-1, self.apply_all)

    def _auto(self, hint):
        with QSignalBlocker(self.table):
            for i in range(self.table.rowCount()):
                item = self.table.item(i, 0)
                item.setCheckState(Qt.Unchecked if item.text() in hint else Qt.Checked)
        self.update_count()

    def non_eeg(self):
        return self.selected_names(False)
