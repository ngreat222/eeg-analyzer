"""项目页：新建/打开项目，导入各种格式的数据，查看被试列表。"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHeaderView, QInputDialog, QListWidget, QMessageBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..compat import resource_path
from ..core import arrays, events as ev, project as pj
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
        self._rows = []

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.metrics = MetricGrid(4)
        lay.addWidget(self.metrics)
        imp = Card("添加数据", "原始数据依次经过「预处理」「事件与分段」；分段数据和数组导入后可以直接分析。"
                             "一个被试可以有多条记录（例如不同条件各录一次）")
        imp.add(hbox(btn("导入文件夹", self.add_folder, "primary", "mdi6.folder-plus-outline",
                         "按“被试/条件/文件”的目录结构自动整理"),
                     btn("添加文件", self.add_raw, None, "mdi6.file-plus-outline",
                         "EDF / BDF / BrainVision / EEGLAB / CNT / GDF / FIF …，可多选"),
                     btn("分段数据", self.add_epochs, None, "mdi6.import", "MNE 的 -epo.fif"),
                     btn("数组", self.add_arrays, None, "mdi6.matrix", ".mat / .h5 / .npy"),
                     btn("手册示例数据", self.add_sample, "ghost", "mdi6.book-open-variant"),
                     "stretch"))
        lay.addWidget(imp)
        tc = Card("被试与记录")
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["被试", "条件", "预处理", "事件", "坏导", "分段", "文件 / 备注"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.cellDoubleClicked.connect(self._open_subject)
        tc.add(self.table)
        tc.head.addWidget(btn("改被试 / 条件…", self.edit_rows, "ghost", "mdi6.pencil-outline"))
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
             ("记录", f"{info['rec_done']}/{info['n_rec']}", "已预处理"),
             ("条件", "、".join(map(str, info["conds"][:3])) or "—",
              f"共 {len(info['conds'])} 个" if info["conds"] else "分段后显示")]
        if info["sfreq"]:
            m.append(("采样", f"{info['sfreq']:g} Hz", f"{info['n_ch']} 个脑电通道"))
        self.metrics.set(m)
        rows = []          # (被试, 记录或 None)
        for s in p.subjects:
            rows += [(s, r) for r in s.recordings] or [(s, None)]
        self.table.setRowCount(len(rows))
        self._rows = rows
        for i, (s, r) in enumerate(rows):
            seg = (" / ".join(f"{k} {v}" for k, v in s.n_trials.items()) or "可分析") if s.epochs_file else "—"
            if r is None:
                vals = [s.name, "—", "导入的分段数据", "—", "—", seg, s.note]
                state = None
            else:
                epath = p.events_path(r)
                n_ev = len(ev.load(epath)) if os.path.exists(epath) else None
                vals = [s.name, r.condition or "—", r.status, "—" if n_ev is None else str(n_ev),
                        ", ".join(r.bads) or "—", seg, r.source]
                state = r.status == pj.REC_DONE
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j == 2 and state is not None:
                    it.setForeground(QColor(C["ok"] if state else C["warn"]))
                if j == 5 and s.epochs_file:
                    it.setForeground(QColor(C["ok"]))
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)

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
        r = pj.add_raw_files(self.project, [resource_path("sample_data", "eeglab_data.set")])[0]
        s = self.project.get_rec(r.id)[0]
        s.locs = resource_path("sample_data", "eeglab_chan32.locs")
        s.rename_from_locs = True
        s.note = r.note = "EEGLAB 教程示例（手册第 1 章用的数据）"
        self.ctx.project_changed("已添加手册示例数据。这份数据工频为 60 Hz，预处理时可把陷波改成 60。")

    def add_folder(self):
        root = QFileDialog.getExistingDirectory(self, "选择数据文件夹（里面按 被试/条件/文件 存放）",
                                                os.path.expanduser("~/Desktop"))
        if not root:
            return
        rows = pj.scan_folder(root)
        if not rows:
            QMessageBox.information(self, "没有找到", "这个文件夹里没有找到脑电数据文件。")
            return
        d = FolderImportDialog(self, root, rows)
        if d.exec() and self._ensure():
            rows = d.rows()
            added = pj.add_scanned(self.project, rows)
            subs = {r["subject"] for r in rows if r["use"]}
            self.ctx.project_changed(f"导入 {len(subs)} 个被试、{len(added)} 条记录，下一步到「预处理」")

    def _selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._rows[r] for r in rows if r < len(self._rows)]

    def edit_rows(self):
        sel = [(s, r) for s, r in self._selected() if r is not None]
        if not sel:
            QMessageBox.information(self, "改被试 / 条件", "先在表里选中要改的记录（可多选）。")
            return
        d = QDialog(self)
        d.setWindowTitle("改被试 / 条件")
        f = QFormLayout(d)
        f.addRow(label(f"选中了 {len(sel)} 条记录。留空的项不改。把几条记录的被试改成同一个名字，就合并到同一个被试下。",
                       "Sub", wrap=True))
        subj = line(sel[0][0].name if len(sel) == 1 else "", "被试名")
        cond = line(sel[0][1].condition if len(sel) == 1 else "", "条件，例如 文本")
        f.addRow("被试", subj)
        f.addRow("条件", cond)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        f.addRow(bb)
        if not d.exec():
            return
        p = self.project
        new_s, new_c = subj.text().strip(), cond.text().strip()
        for s, r in sel:
            if new_c:
                r.condition = new_c
            if new_s and new_s != s.name:
                s.recordings.remove(r)
                t = p.find(new_s)
                if t is None:
                    t = pj.Subject(name=pj._safe(new_s))
                    p.subjects.append(t)
                t.recordings.append(r)
                t.epochs_file, t.status = "", pj.STATUS_RAW
            s.epochs_file, s.status = "", pj.STATUS_RAW     # 记录变了，需要重新分段
        p.subjects = [s for s in p.subjects if s.recordings or s.epochs_file]
        self.ctx.project_changed(f"已修改 {len(sel)} 条记录，需要重新分段")

    def remove(self):
        sel = self._selected()
        if not sel:
            return
        names = [r.id if r is not None else s.name for s, r in sel]
        if QMessageBox.question(self, "移除", f"从项目里移除 {', '.join(names)}？\n（不会删除原始文件）") \
                != QMessageBox.Yes:
            return
        for s, r in sel:
            if r is None:
                self.project.remove(s.name)
            else:
                self.project.remove_rec(r.id)
        self.ctx.project_changed(f"已移除 {len(names)} 项")

    def _open_subject(self, row, _col):
        s, r = self._rows[row]
        if r is not None:
            self.ctx.goto("预处理", subject=r.id)


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


class FolderImportDialog(QDialog):
    """扫描到的文件：确认被试、条件，勾选要导入的。表格里的被试和条件可以直接改。"""

    def __init__(self, parent, root, rows):
        super().__init__(parent)
        self.setWindowTitle("导入文件夹")
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        n_sub = len({r["subject"] for r in rows if r["use"]})
        skipped = sum(not r["use"] for r in rows)
        lay.addWidget(label(f"在 {root} 里找到 {len(rows)} 个脑电文件，按目录猜出 {n_sub} 个被试。"
                            "第一层文件夹当作被试，第二层（“脑电”“EEG”这类名字除外）当作条件。"
                            + (f"有 {skipped} 个看起来是附属文件（如 X.md.edf），默认不导入。" if skipped else "")
                            + "被试和条件可以双击修改。", "Sub", wrap=True))
        self.table = QTableWidget(len(rows), 4)
        self.table.setHorizontalHeaderLabels(["导入", "被试", "条件", "文件"])
        self.table.verticalHeader().setVisible(False)
        for i, r in enumerate(rows):
            c = QTableWidgetItem("")
            c.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            c.setCheckState(Qt.Checked if r["use"] else Qt.Unchecked)
            self.table.setItem(i, 0, c)
            self.table.setItem(i, 1, QTableWidgetItem(r["subject"]))
            self.table.setItem(i, 2, QTableWidgetItem(r["condition"]))
            f = QTableWidgetItem(os.path.relpath(r["path"], root))
            f.setFlags(f.flags() & ~Qt.ItemIsEditable)
            f.setToolTip(r["path"])
            self.table.setItem(i, 3, f)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self._paths = [r["path"] for r in rows]
        self.excl = line("", "文件名包含这些文字的不导入，用逗号分开，例如 .md., test")
        lay.addLayout(hbox(label("排除", "Sub"), self.excl, btn("应用", self._exclude, "ghost")))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("导入")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _exclude(self):
        keys = parse_names(self.excl.text())
        for i, p in enumerate(self._paths):
            if any(k in os.path.basename(p) for k in keys):
                self.table.item(i, 0).setCheckState(Qt.Unchecked)

    def rows(self):
        out = []
        for i, p in enumerate(self._paths):
            out.append(dict(path=p, subject=self.table.item(i, 1).text().strip(),
                            condition=self.table.item(i, 2).text().strip(),
                            use=self.table.item(i, 0).checkState() == Qt.Checked))
        return out


def ask_project_name(parent, default):
    name, ok = QInputDialog.getText(parent, "新建项目", "项目名称：", text=default)
    return name.strip() if ok and name.strip() else None
