"""项目页：新建/打开项目，导入各种格式的数据，查看被试列表。"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHeaderView, QInputDialog, QListWidget, QMessageBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..compat import resource_path
from ..core import arrays, project as pj
from ..core.reading import RAW_FILE_FILTER
from ..core.single import parse_names
from ..theme import C
from .base import BasePage
from .common import combo, fspin, ispin, line, log
from .files_widget import ConditionDialog, FileList
from .widgets import Card, EmptyState, MetricGrid, btn, checkbox, hbox, label


class ProjectPage(BasePage):
    title = "项目"
    subtitle = "一个项目 = 一组被试 + 一套预处理设置，所有分析都在这里汇总"

    def __init__(self, ctx):
        super().__init__(ctx, with_side=False)
        self.stack = QStackedWidget()
        self.body.addWidget(self.stack, 1)
        self.empty = EmptyState(
            "mdi6.folder-multiple-outline", "从一个项目开始",
            "新建项目后，可以一次导入多个被试的原始数据（EDF、BDF、BrainVision、EEGLAB…），"
            "也可以导入已经分段好的数据或手册格式的 .mat 数组。没有数据的话，先试试合成演示项目。",
            [btn("新建项目", ctx.new_project, "primary", "mdi6.plus"),
             btn("打开项目", ctx.open_project, None, "mdi6.folder-open-outline"),
             btn("体验演示项目", ctx.demo_project, "accent", "mdi6.flask-outline")])
        self.stack.addWidget(self.empty)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.metrics = MetricGrid(4)
        lay.addWidget(self.metrics)
        imp = Card("添加数据", "原始数据需要到「预处理」处理；分段数据和数组导入后可以直接分析")
        imp.add(hbox(btn("原始数据", self.add_raw, "accent", "mdi6.file-plus-outline",
                         "EDF / BDF / BrainVision / EEGLAB / CNT / GDF / FIF …，可多选"),
                     btn("分段数据 -epo.fif", self.add_epochs, None, "mdi6.import"),
                     btn("数组 .mat / .h5 / .npy", self.add_arrays, None, "mdi6.matrix"),
                     btn("手册示例数据", self.add_sample, "ghost", "mdi6.book-open-variant"),
                     "stretch"))
        lay.addWidget(imp)
        tc = Card("被试")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["被试", "状态", "试次", "坏导", "ICA 去除", "来源 / 备注"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.cellDoubleClicked.connect(self._open_subject)
        tc.add(self.table)
        tc.head.addWidget(btn("去预处理", lambda: ctx.goto("预处理"), "ghost", "mdi6.arrow-right"))
        tc.head.addWidget(btn("移除所选", self.remove, "danger", "mdi6.delete-outline"))
        lay.addWidget(tc, 1)
        foot = hbox(btn("保存项目", ctx.save_project, None, "mdi6.content-save-outline"),
                    btn("打开其他项目", ctx.open_project, "ghost", "mdi6.folder-open-outline"),
                    btn("新建项目", ctx.new_project, "ghost", "mdi6.plus"), "stretch")
        lay.addLayout(foot)
        self.stack.addWidget(w)

    def on_show(self):
        super().on_show()
        p = self.project
        if p is None:
            self.stack.setCurrentIndex(0)
            self.sub.setText(self.subtitle)
            return
        self.stack.setCurrentIndex(1)
        self.sub.setText(f"{p.folder}")
        info = p.summary()
        m = [("被试", str(info["n"]), f"{info['done']} 人可分析"),
             ("条件", "、".join(map(str, info["conds"][:4])) or "—", f"共 {len(info['conds'])} 个" if info["conds"] else "")]
        if info["sfreq"]:
            m += [("采样", f"{info['sfreq']:g} Hz", f"{info['n_ch']} 个 EEG 通道"),
                  ("分段", f"{info['tmin']:.2f} – {info['tmax']:.2f} s", "")]
        self.metrics.set(m)
        self.table.setRowCount(len(p.subjects))
        for i, s in enumerate(p.subjects):
            done = s.status == pj.STATUS_DONE
            vals = [s.name, ("✓ " if done else "○ ") + s.status, " / ".join(f"{k} {v}" for k, v in s.n_trials.items()),
                    ", ".join(s.bads) or "—", ", ".join(map(str, s.ica_exclude)) or "—",
                    s.source or s.note]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 1:
                    it.setForeground(QColor(C["ok"] if done else C["warn"]))
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)

    # ------------------------------------------------------------ 导入
    def _ensure(self):
        return self.ctx.ensure_project()

    def add_raw(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "选择原始脑电文件（可多选）", os.path.expanduser("~/Desktop"),
                                             RAW_FILE_FILTER)
        if ps and self._ensure():
            added = pj.add_raw_files(self.project, ps)
            self.ctx.project_changed(f"添加 {len(added)} 个原始数据，到「预处理」处理后即可分析")

    def add_epochs(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "选择分段数据", os.path.expanduser("~/Desktop"),
                                             "MNE 分段数据 (*-epo.fif *_epo.fif *.fif);;所有文件 (*)")
        if ps and self._ensure():
            p = self.project
            self.run("导入分段数据", lambda: pj.add_epochs_files(p, ps, log=log),
                     lambda a: self.ctx.project_changed(f"导入 {len(a)} 个被试"))

    def add_arrays(self):
        if not self._ensure():
            return
        d = ArrayImportDialog(self)
        if d.exec():
            p = self.project
            fn = d.job(p)
            self.run("导入数组数据", fn, lambda a: self.ctx.project_changed(f"导入 {len(a)} 个被试"))

    def add_sample(self):
        if not self._ensure():
            return
        s = pj.add_raw_files(self.project, [resource_path("sample_data", "eeglab_data.set")])[0]
        s.locs = resource_path("sample_data", "eeglab_chan32.locs")
        s.rename_from_locs = True
        s.note = "EEGLAB 教程示例（手册第 1 章用的数据）"
        self.ctx.project_changed("已添加手册示例数据。这份数据工频为 60 Hz，预处理时可把陷波改成 60。")

    def remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        names = [self.table.item(r, 0).text() for r in rows]
        if QMessageBox.question(self, "移除被试", f"从项目里移除 {', '.join(names)}？\n（不会删除原始文件）") \
                != QMessageBox.Yes:
            return
        for n in names:
            self.project.remove(n)
        self.ctx.project_changed(f"已移除 {len(names)} 个被试")

    def _open_subject(self, row, _col):
        self.ctx.goto("预处理", subject=self.table.item(row, 0).text())


class LabelSetDialog(QDialog):
    def __init__(self, parent, n_files):
        super().__init__(parent)
        self.setWindowTitle("添加一组标签")
        self.resize(600, 420)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.name = line("", "例：朝向")
        self.col = ispin(2, 1, 100)
        f.addRow("标签名称", self.name)
        f.addRow("用第几列", self.col)
        lay.addLayout(f)
        lay.addWidget(label(f"每个被试一个标签文件，顺序与数据文件一致（共 {n_files} 个）。"
                            "手册第 4 章 ori_201.txt 的第 2 列是 0–15 的朝向类别。", "Sub", wrap=True))
        self.files = FileList(arrays.LABEL_FILE_FILTER, "选择标签文件")
        lay.addWidget(self.files)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def value(self):
        return self.name.text().strip() or "标签", self.files.files(), self.col.value() - 1


class ArrayImportDialog(QDialog):
    """导入手册格式的数组数据，转成项目里的分段数据。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("导入数组数据")
        self.resize(760, 640)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        a = QWidget()
        al = QVBoxLayout(a)
        al.addWidget(label("每个条件一组文件，每个被试一个文件（手册第 3 章：sub1_first.mat、sub1_rep.mat …）。"
                           "不同条件的文件顺序要一致。", "Sub", wrap=True))
        self.conds = QListWidget()
        self._conds = []
        al.addWidget(self.conds)
        al.addLayout(hbox(btn("添加条件…", self._add_cond, "accent", "mdi6.plus"),
                          btn("删除", self._del_cond, "ghost"), "stretch"))
        self.tabs.addTab(a, "条件分文件")

        b = QWidget()
        bl = QVBoxLayout(b)
        bl.addWidget(label("每个被试一个数据文件，再配一组或几组标签文件（手册第 4 章：ERP201.mat + ori_201.txt、"
                           "pos_201.txt）。第一组标签同时作为“条件”。", "Sub", wrap=True))
        self.files = FileList(arrays.GROUP_FILE_FILTER, "选择每个被试的数据文件", on_change=self._keys)
        bl.addWidget(self.files)
        f = QFormLayout()
        self.key = combo([])
        self.key.setEditable(True)
        f.addRow("变量名", self.key)
        bl.addLayout(f)
        self.labels = QListWidget()
        self.labels.setMaximumHeight(90)
        self._labels = []
        bl.addWidget(self.labels)
        bl.addLayout(hbox(btn("添加标签…", self._add_label, "accent", "mdi6.tag-plus-outline"), "stretch"))
        self.tabs.addTab(b, "被试文件 + 标签")

        c = QFormLayout()
        self.dim = combo(list(arrays.DIM_ORDERS))
        self.sfreq = fspin(250, 1, 100000, 50, 2, " Hz")
        self.tmin = fspin(-0.5, -60, 60, 0.1, 4, " s")
        self.crop = checkbox("只保留一段时间")
        self.c0, self.c1 = fspin(-0.5, -60, 60, 0.1, 3, " s"), fspin(1.5, -60, 60, 0.1, 3, " s")
        self.chn = line("", "可不填；填标准名（Fp1, Fz…）可画地形图")
        c.addRow("维度顺序", self.dim)
        c.addRow("采样率", self.sfreq)
        c.addRow("分段起点", self.tmin)
        c.addRow(self.crop)
        w = QWidget()
        w.setLayout(hbox(self.c0, label("到", "Muted"), self.c1))
        c.addRow("时间段", w)
        c.addRow("通道名", self.chn)
        lay.addLayout(c)
        lay.addWidget(label("手册第 3 章：通道×时间×试次、250 Hz、从 −0.5 s 开始；第 4 章：试次×通道×时间、"
                            "250 Hz、从 −1.5 s 开始，只用 −0.5 到 1.5 s。", "Muted", wrap=True))
        self.tabs.currentChanged.connect(self._tab)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._tab(0)

    def _tab(self, i):
        if i == 0:
            self.dim.setCurrentIndex(0)
            self.tmin.setValue(-0.5)
            self.crop.setChecked(False)
        else:
            self.dim.setCurrentIndex(1)
            self.tmin.setValue(-1.5)
            self.crop.setChecked(True)

    def _add_cond(self):
        d = ConditionDialog(self, files=self._conds[-1][1] if self._conds else None)
        if d.exec():
            self._conds.append(d.value())
            n, fs, k = d.value()
            self.conds.addItem(f"{n}　←　{len(fs)} 个文件" + (f"　[{k}]" if k else ""))

    def _del_cond(self):
        r = self.conds.currentRow()
        if r >= 0:
            del self._conds[r]
            self.conds.takeItem(r)

    def _keys(self):
        fs = self.files.files()
        self.key.clear()
        if fs:
            try:
                self.key.addItems(arrays.list_keys(fs[0]))
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "读取文件", str(e))

    def _add_label(self):
        d = LabelSetDialog(self, len(self.files.files()))
        if d.exec():
            v = d.value()
            self._labels.append(v)
            self.labels.addItem(f"{v[0]}　←　{len(v[1])} 个文件，第 {v[2] + 1} 列")

    def _ok(self):
        if self.tabs.currentIndex() == 0 and not self._conds:
            QMessageBox.information(self, "条件", "请添加至少一个条件。")
            return
        if self.tabs.currentIndex() == 1 and (not self.files.files() or not self._labels):
            QMessageBox.information(self, "数据", "请添加数据文件和至少一组标签。")
            return
        self.accept()

    def job(self, project):
        dim = arrays.DIM_ORDERS[self.dim.currentText()]
        sf, t0 = self.sfreq.value(), self.tmin.value()
        chn = parse_names(self.chn.text()) or None
        if self.tabs.currentIndex() == 0:
            conds = list(self._conds)
            return lambda: pj.import_arrays_by_condition(project, conds, dim, sf, t0, chn, log=log)
        files, key = self.files.files(), self.key.currentText().strip()
        key = "" if key == "(整个数组)" else key
        labels = list(self._labels)
        crop = (self.c0.value(), self.c1.value()) if self.crop.isChecked() else None
        return lambda: pj.import_arrays_with_labels(project, files, key, dim, labels, sf, t0, crop, chn, log=log)


def ask_project_name(parent, default):
    name, ok = QInputDialog.getText(parent, "新建项目", "项目名称：", text=default)
    return name.strip() if ok and name.strip() else None
