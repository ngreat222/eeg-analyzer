"""多文件列表 + 条件编辑对话框（第 3、4 章共用）。"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QListWidget, QMessageBox, QVBoxLayout, QWidget)

from ..core import arrays
from .common import button, combo, hrow, line, note


class FileList(QWidget):
    """可增删、可排序的文件列表。被试按列表顺序对应。"""

    def __init__(self, filt, title="选择文件", parent=None, on_change=None):
        super().__init__(parent)
        self.filt, self.title, self.on_change = filt, title, on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setMinimumHeight(110)
        lay.addWidget(self.list)
        lay.addLayout(hrow(button("添加文件…", self.add_files), button("移除所选", self.remove),
                           button("按文件名排序", self.sort), button("清空", self.clear), "stretch"))

    def add_files(self):
        start = os.path.dirname(self.files()[-1]) if self.files() else os.path.expanduser("~/Desktop")
        ps, _ = QFileDialog.getOpenFileNames(self, self.title, start, self.filt)
        for p in ps:
            self.list.addItem(os.path.normpath(p))
        self._changed()

    def remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))
        self._changed()

    def sort(self):
        self.list.sortItems(Qt.AscendingOrder)
        self._changed()

    def clear(self):
        self.list.clear()
        self._changed()

    def files(self):
        return [self.list.item(i).text() for i in range(self.list.count())]

    def set_files(self, fs):
        self.list.clear()
        self.list.addItems(fs)
        self._changed()

    def _changed(self):
        if self.on_change:
            self.on_change()


class ConditionDialog(QDialog):
    """编辑一个条件：名称、每个被试的文件、文件里的事件名或变量名。"""

    def __init__(self, parent=None, name="", files=None, key=""):
        super().__init__(parent)
        self.setWindowTitle("条件")
        self.resize(640, 460)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = line(name, "例：First")
        self.key = combo([])
        self.key.setEditable(True)
        self.files = FileList(arrays.GROUP_FILE_FILTER, "选择每个被试的文件", on_change=self.refresh_keys)
        form.addRow("条件名称", self.name)
        lay.addLayout(form)
        lay.addWidget(note("每个被试一个文件，被试按列表顺序对应（不同条件的列表顺序要一致）。"))
        lay.addWidget(self.files)
        form2 = QFormLayout()
        form2.addRow("事件名 / 变量名", self.key)
        lay.addLayout(form2)
        lay.addWidget(note("-epo.fif 文件：选要用的事件（留空 = 全部试次）。"
                           ".mat / .h5 / .npz 文件：选存数据的变量名，例如手册的 data、data_first。"))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept_if_ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if files:
            self.files.set_files(files)
        if key:
            self.key.setCurrentText(key)

    def refresh_keys(self):
        fs = self.files.files()
        cur = self.key.currentText()
        self.key.clear()
        if not fs:
            return
        try:
            keys = arrays.list_keys(fs[0])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "读取文件", f"无法读取 {fs[0]}：{e}")
            return
        self.key.addItems(keys)
        if cur and cur in keys:
            self.key.setCurrentText(cur)
        if not self.name.text().strip() and len(keys) == 1:
            self.name.setText(os.path.splitext(os.path.basename(fs[0]))[0])

    def accept_if_ok(self):
        if not self.name.text().strip():
            QMessageBox.information(self, "条件", "请填写条件名称。")
            return
        if not self.files.files():
            QMessageBox.information(self, "条件", "请添加文件。")
            return
        self.accept()

    def value(self):
        k = self.key.currentText().strip()
        return self.name.text().strip(), self.files.files(), "" if k == "(整个数组)" else k
