"""页面工作区：在结果与分组设置之间切换，共用当前记录和运行操作。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMessageBox, QScrollArea, QVBoxLayout, QWidget, QFrame, QStackedWidget, QFormLayout

from ..core.analysis import GROUP
from .widgets import Section, btn, label


class BasePage(QWidget):
    title = ""
    subtitle = ""
    side_width = 300

    def __init__(self, ctx, with_side=True):
        super().__init__()
        self.ctx = ctx
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.main = QWidget()
        self.main.setObjectName("Main")
        ml = QVBoxLayout(self.main)
        ml.setContentsMargins(24, 20, 24, 18)
        ml.setSpacing(12)
        head = QHBoxLayout()
        tl = QVBoxLayout()
        tl.setSpacing(2)
        self.heading = label(self.title, "H1", wrap=True)
        tl.addWidget(self.heading)
        self.sub = label(self.subtitle, "Sub", wrap=True)
        tl.addWidget(self.sub)
        head.addLayout(tl, 1)
        self.project_context = label("", "Sub")
        if with_side:
            self.project_context.setObjectName("ProjectContext")
            tl.insertWidget(0, self.project_context)
        if not with_side:
            self.project_context.hide()
        ml.addLayout(head)
        self.context_row = QHBoxLayout()
        ml.addLayout(self.context_row)
        work = QWidget()
        self.body = QVBoxLayout(work)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(12)
        root.addWidget(self.main, 1)
        if not with_side:
            ml.addWidget(work, 1)
            return

        self.heading.setObjectName("WorkspaceTitle")
        self.side_button = btn("参数设置", None, None, "mdi6.tune-variant")
        self.side_button.setCheckable(True)
        status = QFrame()
        status.setObjectName("ContextCard")
        summary = QVBoxLayout(status)
        summary.setContentsMargins(16, 8, 16, 9)
        summary.setSpacing(0)
        self.record_workspace = self.title in ("预处理", "事件与分段")
        summary.addWidget(label("连续记录" if self.record_workspace else "可分析被试", "Caption"))
        self.context_stat = label("—", "ContextValue")
        summary.addWidget(self.context_stat)
        head.addWidget(status, 0, Qt.AlignVCenter)
        self.context_row.addStretch()
        self.context_row.addWidget(self.side_button)
        self.content = QStackedWidget()
        self.content.addWidget(work)
        ml.addWidget(self.content, 1)

        settings = QFrame()
        settings.setObjectName("SettingsSheet")
        layout = QVBoxLayout(settings)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        group_row = QHBoxLayout()
        group_row.addWidget(label("设置类别", "Sub"))
        self.group_picker = QComboBox()
        self.group_picker.setMinimumWidth(155)
        group_row.addWidget(self.group_picker)
        group_row.addStretch()
        layout.addLayout(group_row)
        self.groups = QStackedWidget()
        layout.addWidget(self.groups, 1)
        self.group_picker.currentIndexChanged.connect(self.groups.setCurrentIndex)
        self.content.addWidget(settings)
        self.side_button.toggled.connect(self.show_settings)

        self.actions = QVBoxLayout()
        self.actions.setSpacing(6)
        ml.addLayout(self.actions)

    def show_settings(self, enabled):
        self.content.setCurrentIndex(int(enabled))
        self.side_button.setText("返回工作区" if enabled else "参数设置")
        self.side_button.setChecked(enabled)

    def section(self, title, expanded=True):
        group = Section(title)
        group.toggle.hide()
        group.form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        group.form.setVerticalSpacing(10)
        section_container = QWidget()
        column = QVBoxLayout(section_container)
        column.setContentsMargins(2, 2, 2, 2)
        group.setMaximumWidth(760)
        column.addWidget(group)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(section_container)
        self.groups.addWidget(scroll)
        self.group_picker.addItem(title)
        return group

    def end_side(self):
        pass

    def action_row(self, *buttons):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch()
        for b in buttons:
            row.addWidget(b)
        self.actions.addLayout(row)

    def record_picker(self):
        self.context_row.insertWidget(0, label("当前记录", "Sub"))
        selector = QComboBox()
        selector.setMinimumContentsLength(12)
        selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        selector.setPlaceholderText("尚未导入连续记录")
        self.context_row.insertWidget(1, selector)
        return selector

    # ------------------------------------------------------------ 公共
    @property
    def project(self):
        return self.ctx.project

    def run(self, title, fn, done=None):
        if hasattr(self, "content"):
            self.show_settings(False)
        return self.ctx.runner.run(title, fn, done)

    def need_project(self, need_done=True):
        p = self.project
        if p is None:
            QMessageBox.information(self, "还没有项目", "请先在「项目」页新建或打开项目，并导入数据。")
            return False
        if need_done and not p.done_subjects():
            QMessageBox.information(self, "还没有可分析的数据",
                                    "项目里还没有分段好的被试。原始数据要先到「预处理」处理，再到「事件与分段」分段；"
                                    "也可以在「项目」直接导入分段数据。")
            return False
        return True

    def on_show(self):
        p = self.project
        if hasattr(self, "context_stat"):
            if not p:
                self.context_stat.setText("—")
            elif self.record_workspace:
                self.context_stat.setText(f"{len(p.recordings())} 条")
            else:
                self.context_stat.setText(f"{len(p.done_subjects())} / {len(p.subjects)} 人")
        if p is None:
            self.project_context.setText("")
        else:
            self.project_context.setText(p.name)


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
        if len(done) > 1:
            cb.addItem(f"全组（{len(done)} 人）", GROUP)
        for s in done:
            cb.addItem(f"单个被试 · {s.name}", s.name)
    i = cb.findData(cur)
    cb.setCurrentIndex(i if i >= 0 else 0)
    cb.blockSignals(False)


def label_values(project, label_name, scope=None):
    done = project.done_subjects()
    if not done:
        return []
    ep = project.epochs(scope if scope and scope != GROUP else done[0].name)
    if label_name == "条件":
        return list(ep.event_id)
    vals = list(dict.fromkeys(ep.metadata[label_name].astype(str)))
    try:
        return sorted(vals, key=float)
    except ValueError:
        return sorted(vals)
