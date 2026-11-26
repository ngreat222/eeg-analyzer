"""主窗口：左侧图标导航，按分析流程排列：项目 → 预处理 → ERP / 时频 / 解码 / RSA → 报告。"""
import os
from datetime import datetime

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QStackedWidget,
                               QVBoxLayout, QWidget, QSizePolicy, QScrollArea, QSplitter, QToolBar)

from .. import APP_NAME, __version__
from ..core import project as pj
from .. import theme
from ..theme import C
from .analysis_pages import DecodePage, ERPPage, RSAPage, TFRPage
from .common import LOG, TaskRunner, log
from .events_page import EventsPage
from .preprocess_page import PreprocessPage
from .project_page import ProjectPage, ask_project_name
from .report_page import ReportPage
from .workspace_surface import WorkspaceCanvas
from .widgets import btn, icon, label

NAV = [("项目", "mdi6.folder-multiple-outline", ProjectPage),
       ("预处理", "mdi6.tune-variant", PreprocessPage),
       ("事件", "mdi6.map-marker-path", EventsPage),
       ("ERP", "mdi6.sine-wave", ERPPage),
       ("时频", "mdi6.chart-bell-curve-cumulative", TFRPage),
       ("解码", "mdi6.brain", DecodePage),
       ("RSA", "mdi6.dots-grid", RSAPage),
       ("报告", "mdi6.file-chart-outline", ReportPage)]
NAV_TEXT = {"项目":"项目总览", "事件":"事件与分段", "ERP":"事件相关电位", "时频":"时频与频谱", "解码":"解码与反向编码", "RSA":"表征相似性", "报告":"分析报告"}
NAV_GROUPS = {"项目": "数据", "ERP": "分析", "报告": "输出"}

PROJECTS_ROOT = os.path.join(os.path.expanduser("~"), "Documents", "脑电分析项目")


def _check_qss():
    """勾选框里的对勾：QSS 只能引用图片文件，所以把图标画成 png 放到临时目录。"""
    import tempfile
    try:
        path = os.path.join(tempfile.gettempdir(), "eeg_analyzer_check.png")
        icon("mdi6.check-bold", "#ffffff").pixmap(32, 32).save(path)
        url = path.replace("\\", "/")
        return (f"\nQCheckBox::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked "
                f"{{ image: url({url}); }}\n")
    except Exception:  # noqa: BLE001
        return ""


class MainWindow(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1520, 940)
        # 样式只加在主窗口上（对话框是它的子窗口，会继承）；MNE 的波形浏览器有自己的主题，不能被它影响
        self.setStyleSheet(theme.QSS + _check_qss())
        self.project = None
        self.navigation_visit = 0
        self.reports = []
        self._project_reports = {}
        self.runner = TaskRunner(self)
        self._windows = []
        self._mark_editors = {}
        self.settings = settings if settings is not None else QSettings("EEGAnalyzer", "EEGAnalyzer")

        self.logw = QPlainTextEdit()
        self.logw.setReadOnly(True)
        self.logw.setMaximumBlockCount(5000)
        self.logw.setFixedHeight(170)
        self.logw.setStyleSheet(f"QPlainTextEdit{{background:{C['log_bg']};border:none;border-top:1px solid {C['line']};"
                                f"border-radius:0;font-family:{theme.MONO};font-size:12px;"
                                f"color:{C['text2']};padding:8px 14px;}}")
        self.logw.hide()
        central = WorkspaceCanvas()
        root = QHBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(0)
        root.addWidget(self._rail())
        right = QWidget()
        right.setObjectName("Main")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.setSpacing(0)
        self.stack = QStackedWidget()
        rl.addWidget(self.stack, 1)
        rl.addWidget(self.logw)
        root.addWidget(right, 1)
        self.panels = QSplitter(Qt.Horizontal)
        self.panels.setChildrenCollapsible(False)
        self.work_area = QScrollArea()
        self.work_area.setFrameShape(QScrollArea.NoFrame)
        self.work_area.setWidgetResizable(True)
        self.work_area.setWidget(central)
        self.panels.addWidget(self.work_area)
        self.setCentralWidget(self.panels)
        self.assistant = None
        self.project_sources = None
        bar = QToolBar("视图",self)
        bar.setMovable(False)
        self.addToolBar(bar)
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        bar.setIconSize(QSize(16,16))
        self.location = label("BIAP 工作台", "Sub")
        bar.addWidget(self.location)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        work_action = bar.addAction(icon("mdi6.view-dashboard-outline", C["text2"]), "工作窗口")
        self.work_action = work_action
        work_action.setCheckable(True)
        work_action.setChecked(True)
        work_action.setShortcut("Ctrl+1")
        work_action.setToolTip("显示或隐藏工作窗口（Ctrl+1）")
        work_action.toggled.connect(self.work_area.setVisible)
        self.ai_action = bar.addAction(icon("mdi6.message-text-outline", C["text2"]), "AI 助手")
        self.ai_action.setCheckable(True)
        self.ai_action.setShortcut("Ctrl+2")
        self.ai_action.setToolTip("显示或隐藏 AI 助手（Ctrl+2）")
        self.ai_action.toggled.connect(self.toggle_assistant)
        self.sources_action = QAction("项目引用", self)
        self.sources_action.setShortcut("Ctrl+3")
        self.addAction(self.sources_action)
        self.sources_action.setCheckable(True)
        self.sources_action.toggled.connect(self.show_project_sources)
        self.pages = {}
        for name, _, cls in NAV:
            page = cls(self)
            self.pages[name] = page
            self.stack.addWidget(page)

        from .project_bridge import analysis_values
        self.analysis_defaults=analysis_values(self)
        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{C['text2']};padding-left:8px;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(160)
        self.progress.hide()
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().messageChanged.connect(lambda message: self.status.setVisible(not message))
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().setSizeGripEnabled(False)
        from .update_dialog import UpdateController
        self.updates = UpdateController(self, self.update_button, schedule=settings is None)
        LOG.message.connect(self._append_log)
        self.runner.busy_changed.connect(self._busy)
        self.goto("项目")
        log(f"{APP_NAME} {__version__} 已启动")
        last = self.settings.value("last_project", "")
        if last and os.path.exists(os.path.join(last, "project.json")):
            try:
                self.set_project(pj.Project.load(last), quiet=True)
                log(f"已打开上次的项目：{last}")
            except Exception as e:  # noqa: BLE001
                log(f"上次的项目打不开：{e}")

    # ------------------------------------------------------------ 导航
    def show_project_sources(self, visible):
        if visible:
            self.ai_action.setChecked(True)
        if self.assistant is not None:
            self.assistant.show_library(visible)

    def toggle_assistant(self, visible):
        if visible and self.assistant is None:
            try:
                from .assistant_panel import Studio
                self.assistant = Studio(host=self)
            except Exception as exc:
                self.ai_action.setChecked(False)
                QMessageBox.warning(self,"打开 AI 助手",str(exc))
                return
            self.panels.addWidget(self.assistant)
            self.panels.setSizes([max(500, self.width()-440),440])
        if self.assistant is not None:
            if not visible:
                self.sources_action.setChecked(False)
            self.assistant.setVisible(visible)
            if visible:
                self.assistant.edit.setFocus()

    def discuss_quality(self, summary):
        self.ai_action.setChecked(True)
        if self.assistant is not None:
            import json
            self.assistant.edit.appendPlainText("帮我看看这条记录的质量摘要。\n"+json.dumps(summary,ensure_ascii=False,indent=2))

    def _rail(self):
        rail = QWidget()
        rail.setObjectName("Rail")
        rail.setFixedWidth(180)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(12, 16, 12, 12)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 0, 0)
        head.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(32, 32))
        head.addWidget(logo)
        identity = QVBoxLayout()
        identity.setSpacing(0)
        identity.addWidget(label("BIAP", "Brand"))
        identity.addWidget(label("脑电分析工作台", "Caption"))
        head.addLayout(identity)
        head.addStretch(1)
        lay.addLayout(head)
        lay.addSpacing(14)
        nb = self._rail_btn("新建项目", "mdi6.plus")
        nb.clicked.connect(self.new_project)
        nb.setIcon(icon("mdi6.plus","#344423"))
        nb.setObjectName("NewProject")
        ob = self._rail_btn("打开项目", "mdi6.folder-open-outline")
        ob.clicked.connect(self.open_project)

        lay.addSpacing(10)
        self.proj_name = label("未打开项目", "Sub", wrap=True)
        self.proj_name.setContentsMargins(10, 0, 0, 0)
        lay.addWidget(self.proj_name)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.nav_btns = {}
        self.nav_sections = {}
        self.nav_group_for = {}
        for i, (name, ic, _) in enumerate(NAV):
            if name in NAV_GROUPS:
                group_name = NAV_GROUPS[name]
                toggle = btn(group_name, None)
                toggle.setProperty("kind", "sectionNav")
                toggle.setCheckable(True)
                toggle.clicked.connect(lambda enabled, group=group_name:
                                       self.expand_navigation(group if enabled else None))
                lay.addWidget(toggle)
                group = QWidget()
                links = QVBoxLayout(group)
                links.setContentsMargins(6, 0, 0, 4)
                links.setSpacing(2)
                lay.addWidget(group)
                self.nav_sections[group_name] = (toggle, group)
            self.nav_group_for[name] = group_name
            b = self._rail_btn(NAV_TEXT.get(name, name), ic)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, n=name: self.goto(n))
            self.nav.addButton(b, i)
            self.nav_btns[name] = b
            links.addWidget(b)
        lay.addStretch(1)
        lay.addWidget(nb)
        lay.addWidget(ob)
        self.log_btn = self._rail_btn("运行日志", "mdi6.console-line")
        self.log_btn.setCheckable(True)
        self.log_btn.toggled.connect(self.logw.setVisible)
        lay.addWidget(self.log_btn)
        light = theme.MODE == "light"
        tb = self._rail_btn("切换到深色" if light else "切换到亮色",
                            "mdi6.weather-night" if light else "mdi6.white-balance-sunny")
        self.theme_button = tb
        tb.setCheckable(False)
        tb.clicked.connect(self.toggle_theme)
        lay.addWidget(tb)
        self.workspace_buttons = (nb, ob, self.log_btn, tb)
        self.update_button = self._rail_btn(f"检查更新 · {__version__}", "mdi6.update")
        self.update_button.setToolTip("查看版本和软件更新")
        lay.addWidget(self.update_button)
        return rail

    @staticmethod
    def _rail_btn(text, ic):
        b = btn(text, None, "nav")
        b.setIcon(icon(ic, C["text2"], C["text"]))
        b.setIconSize(QSize(17, 17))
        return b

    def expand_navigation(self, current):
        for name, (toggle, group) in self.nav_sections.items():
            active = name == current
            toggle.setChecked(active)
            toggle.setIcon(icon("mdi6.chevron-down" if active else "mdi6.chevron-right", C["muted"]))
            group.setVisible(active)

    def goto(self, name, subject=None):
        self.navigation_visit += 1
        self.commit_focused_input()
        page = self.pages[name]
        self.expand_navigation(self.nav_group_for[name])
        self.location.setText("BIAP 工作台    /    " + NAV_TEXT.get(name, name))
        self.work_area.widget().set_overview(name == "项目")
        self.stack.setCurrentWidget(page)
        self.nav_btns[name].setChecked(True)
        if subject is not None and hasattr(page, "on_show"):
            page.on_show(subject=subject)
        else:
            page.on_show()

    # ------------------------------------------------------------ 项目
    def commit_focused_input(self):
        editor = QApplication.focusWidget()
        if isinstance(editor, (QLineEdit, QAbstractSpinBox)) and self.stack.isAncestorOf(editor):
            editor.clearFocus()

    def set_project(self, p, quiet=False):
        if self.runner.busy and p is not self.project:
            QMessageBox.information(self, "计算进行中", "当前步骤结束后可以切换项目。")
            return
        if p is not self.project:
            self.commit_focused_input()
            if not self.finish_mark_editors():
                return
            for viewer in list(self._windows):
                viewer.close()
            page = self.pages["预处理"]
            page.raw = page.ica = page.raw_ica = page._sig = page.fitted_ica = None
        self.project = p
        self.reports = self._project_reports.setdefault(os.path.normcase(p.folder), [])
        from .project_bridge import restore_analysis
        restore_analysis(self)
        if self.assistant is not None:
            self.assistant.bind_project()
        if self.project_sources is not None:
            self.project_sources.fill()
        self.pages["事件"]._headers.clear()
        self.settings.setValue("last_project", p.folder)
        self.setWindowTitle(f"{p.name} — {APP_NAME} {__version__}")
        if not quiet:
            log(f"项目：{p.folder}")
        self.refresh()

    def refresh(self):
        self.stack.currentWidget().on_show()
        p = self.project
        if p:
            self.proj_name.setText(p.name)
            self.proj_name.setToolTip(p.folder)
            self.status.setText(f"{p.name}　 ·　 {len(p.done_subjects())} / {len(p.subjects)} 名被试可分析")
            self.status.setToolTip(p.folder)

    def project_changed(self, msg=""):
        if self.project:
            self.project.save()
        if self.project_sources is not None:
            self.project_sources.fill()
        if msg:
            log(msg)
            self.statusBar().showMessage(msg, 6000)
        self.refresh()

    def new_project(self):
        name = ask_project_name(self, f"项目_{datetime.now():%Y%m%d}")
        if not name:
            return None
        os.makedirs(PROJECTS_ROOT, exist_ok=True)
        folder = QFileDialog.getExistingDirectory(self, "项目保存在哪个文件夹（会在里面新建一个同名文件夹）",
                                                  PROJECTS_ROOT)
        if not folder:
            return None
        path = os.path.join(folder, name)
        if os.path.exists(os.path.join(path, "project.json")):
            QMessageBox.warning(self, "已存在", f"{path} 已经是一个项目，请换个名字或直接打开它。")
            return None
        os.makedirs(path, exist_ok=True)
        p = pj.Project(path, name)
        p.save()
        self.set_project(p)
        return p

    def ensure_project(self):
        if self.project is not None:
            return self.project
        path = os.path.join(PROJECTS_ROOT, f"项目_{datetime.now():%Y%m%d_%H%M%S}")
        os.makedirs(path, exist_ok=True)
        p = pj.Project(path)
        p.save()
        self.set_project(p)
        log(f"还没有项目，已自动新建：{path}")
        return p

    def open_project(self):
        folder = QFileDialog.getExistingDirectory(self, "选择项目文件夹（里面有 project.json）",
                                                  self.project.folder if self.project else PROJECTS_ROOT)
        if not folder:
            return
        if not os.path.exists(os.path.join(folder, "project.json")):
            QMessageBox.warning(self, "不是项目文件夹", "这个文件夹里没有 project.json。")
            return
        self.set_project(pj.Project.load(folder))
        self.goto("项目")

    def save_project(self):
        if self.project:
            self.commit_focused_input()
            if not self.finish_mark_editors():
                return
            try:
                self.project.save()
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "项目未保存", str(exc))
            else:
                self.statusBar().showMessage("项目已保存", 4000)

    def demo_project(self):
        path = os.path.join(PROJECTS_ROOT, "合成演示项目")
        if os.path.exists(os.path.join(path, "project.json")):
            self.set_project(pj.Project.load(path))
            self.goto("项目")
            return
        os.makedirs(path, exist_ok=True)
        self.runner.run("生成合成演示项目", lambda: pj.make_demo_project(path, log=log),
                        lambda p: (self.set_project(p), self.goto("项目"),
                                   QMessageBox.information(
                                       self, "演示项目", "已生成 10 名被试的合成数据（模拟信号，不是真实数据）。\n\n"
                                       "包含 First / Repetition 两个条件和 8 类“朝向”标签，"
                                       "可以直接去 ERP、时频、解码、RSA 页试试。")))

    def toggle_theme(self):
        new = "dark" if theme.MODE == "light" else "light"
        from .appearance import apply_window_theme
        apply_window_theme(self, new)
        self.settings.setValue("theme", new)

    def add_report(self, res):
        entry = (datetime.now(), res)
        if res.report_key is not None:
            for i, (_, previous) in enumerate(self.reports):
                if previous.report_key == res.report_key:
                    self.reports.pop(i)
                    break
        self.reports.append(entry)
        if self.stack.currentWidget() is self.pages["报告"]:
            self.pages["报告"].on_show()

    def keep_window(self, win, on_close=None, *, record_id=None):
        self._windows.append(win)
        sig = getattr(win, "gotClosed", None)
        commit = {"record": record_id, "saved": False}
        if record_id is not None:
            self._mark_editors[win] = commit

        def closed(*_):
            if win not in self._windows:
                return
            self._windows.remove(win)
            self._mark_editors.pop(win, None)
            try:
                commit["saved"] = not on_close or on_close() is not False
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                QMessageBox.warning(self, "标记未保存", str(exc))
        if sig is not None:
            sig.connect(closed)
        elif hasattr(win, "canvas"):
            win.canvas.mpl_connect("close_event", closed)

    def finish_mark_editors(self, record_ids=None):
        """Commit open waveform editors before a task snapshots its inputs."""
        for viewer, commit in list(self._mark_editors.items()):
            if record_ids is None or commit["record"] in record_ids:
                viewer.close()
                if not commit["saved"]:
                    return False
        return True

    # ------------------------------------------------------------ 内部
    def _append_log(self, msg):
        self.logw.appendPlainText(msg)
        self.logw.moveCursor(QTextCursor.End)
        if msg.startswith(("▶", "✓", "✗", "⚠")) or "：" in msg[:20]:
            self.status.setText(msg[:160])

    def _busy(self, busy, title):
        self.stack.currentWidget().setEnabled(not busy)
        for b in self.nav_btns.values():
            b.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.status.setText(f"正在{title}…　可以点左下角「日志」看进度")
            QApplication.setOverrideCursor(Qt.BusyCursor)
        else:
            QApplication.restoreOverrideCursor()
            self.refresh()

    def closeEvent(self, e):
        if self.assistant is not None and self.assistant.worker and self.assistant.worker.isRunning():
            QMessageBox.information(self, "AI 助手", "回复完成后即可退出。你也可以先隐藏助手。")
            e.ignore()
            return
        if self.runner.busy and QMessageBox.question(self, "还在计算", "还有计算没完成，确定退出吗？") != QMessageBox.Yes:
            e.ignore()
            return
        self.commit_focused_input()
        if not self.finish_mark_editors():
            e.ignore()
            return
        if self.project:
            try:
                self.project.save()
            except (ValueError, OSError) as exc:
                if QMessageBox.question(self,"项目保存失败",str(exc)+"。放弃本窗口未保存的修改并关闭吗？",
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    e.ignore()
                    return
        for w in list(self._windows):
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        self.updates.shutdown()
        e.accept()
        if self.runner.busy:
            os._exit(0)
