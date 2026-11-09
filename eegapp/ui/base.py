"""页面基类：中间是结果区，右侧是可滚动的设置面板，底部固定放运行按钮。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMessageBox, QScrollArea, QVBoxLayout, QWidget

from ..core.analysis import GROUP
from .widgets import Section, chip, label


class BasePage(QWidget):
    title = ""
    subtitle = ""
    side_width = 340

    def __init__(self, ctx, with_side=True):
        super().__init__()
        self.ctx = ctx
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.main = QWidget()
        self.main.setObjectName("Main")
        ml = QVBoxLayout(self.main)
        ml.setContentsMargins(26, 20, 20, 12)
        ml.setSpacing(12)
        head = QHBoxLayout()
        tl = QVBoxLayout()
        tl.setSpacing(2)
        tl.addWidget(label(self.title, "H1"))
        self.sub = label(self.subtitle, "Sub", wrap=True)
        tl.addWidget(self.sub)
        head.addLayout(tl, 1)
        self.proj_chip = chip("")
        head.addWidget(self.proj_chip, 0, Qt.AlignTop)
        ml.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        ml.addLayout(self.body, 1)
        root.addWidget(self.main, 1)

        if with_side:
            side = QWidget()
            side.setObjectName("Side")
            side.setFixedWidth(self.side_width)
            sl = QVBoxLayout(side)
            sl.setContentsMargins(0, 16, 0, 14)
            sl.setSpacing(10)
            t = label("分析设置", "H2")
            t.setContentsMargins(18, 0, 0, 0)
            sl.addWidget(t)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            inner = QWidget()
            self.side_lay = QVBoxLayout(inner)
            self.side_lay.setContentsMargins(18, 0, 16, 0)
            self.side_lay.setSpacing(4)
            sc.setWidget(inner)
            sl.addWidget(sc, 1)
            self.actions = QVBoxLayout()
            self.actions.setContentsMargins(18, 4, 18, 0)
            self.actions.setSpacing(8)
            sl.addLayout(self.actions)
            root.addWidget(side)

    # ------------------------------------------------------------ 构建
    def section(self, title, expanded=True):
        s = Section(title, expanded)
        self.side_lay.addWidget(s)
        return s

    def end_side(self):
        self.side_lay.addStretch(1)

    def action_row(self, *buttons):
        row = QHBoxLayout()
        row.setSpacing(8)
        for b in buttons:
            row.addWidget(b)
        self.actions.addLayout(row)

    # ------------------------------------------------------------ 公共
    @property
    def project(self):
        return self.ctx.project

    def run(self, title, fn, done=None):
        self.ctx.runner.run(title, fn, done)

    def need_project(self, need_done=True):
        p = self.project
        if p is None:
            QMessageBox.information(self, "还没有项目", "请先在「项目」页新建或打开项目，并导入数据。")
            return False
        if need_done and not p.done_subjects():
            QMessageBox.information(self, "还没有可分析的数据",
                                    "项目里还没有预处理好的被试。请在「项目」导入分段数据，或到「预处理」处理原始数据。")
            return False
        return True

    def on_show(self):
        p = self.project
        if p is None:
            self.proj_chip.setText("未打开项目")
        else:
            self.proj_chip.setText(f"{p.name} · {len(p.done_subjects())}/{len(p.subjects)} 人可分析")


def scope_combo():
    c = QComboBox()
    c.setMinimumContentsLength(10)
    return c


def refresh_scope(cb, project):
    cur = cb.currentData()
    cb.blockSignals(True)
    cb.clear()
    if project is not None:
        done = project.done_subjects()
        if done:
            cb.addItem(f"全组（{len(done)} 人）", GROUP)
            for s in done:
                cb.addItem(f"单个被试 · {s.name}", s.name)
    i = cb.findData(cur)
    cb.setCurrentIndex(i if i >= 0 else 0)
    cb.blockSignals(False)


def label_values(project, label_name):
    done = project.done_subjects()
    if not done:
        return []
    ep = project.epochs(done[0].name)
    if label_name == "条件":
        return list(ep.event_id)
    vals = list(dict.fromkeys(ep.metadata[label_name].astype(str)))
    try:
        return sorted(vals, key=float)
    except ValueError:
        return sorted(vals)
