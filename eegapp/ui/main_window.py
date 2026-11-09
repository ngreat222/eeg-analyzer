"""主窗口：左侧图标导航，按分析流程排列：项目 → 预处理 → ERP / 时频 / 解码 / RSA → 报告。"""
import os
from datetime import datetime

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core import project as pj
from ..theme import C, QSS
from .analysis_pages import DecodePage, ERPPage, RSAPage, TFRPage
from .common import LOG, TaskRunner, log
from .preprocess_page import PreprocessPage
from .project_page import ProjectPage, ask_project_name
from .report_page import ReportPage
from .widgets import icon

NAV = [("项目", "mdi6.folder-multiple-outline", ProjectPage),
       ("预处理", "mdi6.tune-variant", PreprocessPage),
       ("ERP", "mdi6.sine-wave", ERPPage),
       ("时频", "mdi6.chart-bell-curve-cumulative", TFRPage),
       ("解码", "mdi6.brain", DecodePage),
       ("RSA", "mdi6.dots-grid", RSAPage),
       ("报告", "mdi6.file-chart-outline", ReportPage)]

PROJECTS_ROOT = os.path.join(os.path.expanduser("~"), "Documents", "脑电分析项目")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1520, 940)
        self.setStyleSheet(QSS)
        self.project = None
        self.reports = []
        self.runner = TaskRunner(self)
        self._windows = []
        self.settings = QSettings("EEGAnalyzer", "EEGAnalyzer")

        self.logw = QPlainTextEdit()
        self.logw.setReadOnly(True)
        self.logw.setMaximumBlockCount(5000)
        self.logw.setFixedHeight(170)
        self.logw.setStyleSheet(f"QPlainTextEdit{{background:#191918;border:none;border-top:1px solid {C['line']};"
                                f"border-radius:0;font-family:Consolas,'Microsoft YaHei UI';font-size:12px;"
                                f"color:{C['text2']};padding:8px 14px;}}")
        self.logw.hide()
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._rail())
        right = QWidget()
        right.setObjectName("Main")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.stack = QStackedWidget()
        rl.addWidget(self.stack, 1)
        rl.addWidget(self.logw)
        root.addWidget(right, 1)
        self.setCentralWidget(central)

        self.pages = {}
        for name, _, cls in NAV:
            page = cls(self)
            self.pages[name] = page
            self.stack.addWidget(page)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{C['text2']};padding-left:8px;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(160)
        self.progress.hide()
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().setSizeGripEnabled(False)
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
    def _rail(self):
        rail = QWidget()
        rail.setObjectName("Rail")
        rail.setFixedWidth(84)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(10, 16, 10, 14)
        lay.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(icon("mdi6.head-snowflake-outline", "#5DCAA5").pixmap(34, 34))
        logo.setAlignment(Qt.AlignCenter)
        logo.setToolTip(f"{APP_NAME} {__version__}")
        lay.addWidget(logo)
        lay.addSpacing(14)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.nav_btns = {}
        for i, (name, ic, _) in enumerate(NAV):
            b = self._rail_btn(name, ic)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, n=name: self.goto(n))
            self.nav.addButton(b, i)
            self.nav_btns[name] = b
            lay.addWidget(b)
            if name in ("预处理", "RSA"):
                lay.addSpacing(8)
        lay.addStretch(1)
        self.log_btn = self._rail_btn("日志", "mdi6.console-line")
        self.log_btn.setCheckable(True)
        self.log_btn.toggled.connect(self.logw.setVisible)
        lay.addWidget(self.log_btn)
        return rail

    @staticmethod
    def _rail_btn(text, ic):
        b = QToolButton()
        b.setObjectName("RailBtn")
        b.setText(text)
        b.setIcon(icon(ic, "#CECBF6"))
        b.setIconSize(QSize(22, 22))
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setFixedSize(64, 58)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def goto(self, name, subject=None):
        page = self.pages[name]
        self.stack.setCurrentWidget(page)
        self.nav_btns[name].setChecked(True)
        if subject is not None and hasattr(page, "on_show"):
            page.on_show(subject=subject)
        else:
            page.on_show()

    # ------------------------------------------------------------ 项目
    def set_project(self, p, quiet=False):
        self.project = p
        self.settings.setValue("last_project", p.folder)
        self.setWindowTitle(f"{p.name} — {APP_NAME} {__version__}")
        if not quiet:
            log(f"项目：{p.folder}")
        self.refresh()

    def refresh(self):
        self.stack.currentWidget().on_show()
        p = self.project
        if p:
            self.status.setText(f"{p.name}　·　{len(p.done_subjects())}/{len(p.subjects)} 名被试可分析　·　{p.folder}")

    def project_changed(self, msg=""):
        if self.project:
            self.project.save()
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
            self.project.save()
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

    def add_report(self, res):
        self.reports.append((datetime.now(), res))

    def keep_window(self, win, on_close=None):
        self._windows.append(win)
        sig = getattr(win, "gotClosed", None)

        def closed(*_):
            if win in self._windows:
                self._windows.remove(win)
            if on_close:
                on_close()
        if sig is not None:
            sig.connect(closed)
        elif hasattr(win, "canvas"):
            win.canvas.mpl_connect("close_event", closed)

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
        if self.runner.busy and QMessageBox.question(self, "还在计算", "还有计算没完成，确定退出吗？") != QMessageBox.Yes:
            e.ignore()
            return
        if self.project:
            self.project.save()
        for w in list(self._windows):
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        e.accept()
        if self.runner.busy:
            os._exit(0)
