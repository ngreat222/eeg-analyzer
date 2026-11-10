"""主窗口：左侧图标导航，按分析流程排列：项目 → 预处理 → ERP / 时频 / 解码 / RSA → 报告。"""
import os
from datetime import datetime

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QStackedWidget,
                               QVBoxLayout, QWidget)

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
from .widgets import btn, icon, label

NAV = [("项目", "mdi6.folder-multiple-outline", ProjectPage),
       ("预处理", "mdi6.tune-variant", PreprocessPage),
       ("事件", "mdi6.map-marker-path", EventsPage),
       ("ERP", "mdi6.sine-wave", ERPPage),
       ("时频", "mdi6.chart-bell-curve-cumulative", TFRPage),
       ("解码", "mdi6.brain", DecodePage),
       ("RSA", "mdi6.dots-grid", RSAPage),
       ("报告", "mdi6.file-chart-outline", ReportPage)]
NAV_TEXT = {"事件": "事件与分段"}
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
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1520, 940)
        # 样式只加在主窗口上（对话框是它的子窗口，会继承）；MNE 的波形浏览器有自己的主题，不能被它影响
        self.setStyleSheet(theme.QSS + _check_qss())
        self.project = None
        self.reports = []
        self.runner = TaskRunner(self)
        self._windows = []
        self.settings = QSettings("EEGAnalyzer", "EEGAnalyzer")

        self.logw = QPlainTextEdit()
        self.logw.setReadOnly(True)
        self.logw.setMaximumBlockCount(5000)
        self.logw.setFixedHeight(170)
        self.logw.setStyleSheet(f"QPlainTextEdit{{background:{C['log_bg']};border:none;border-top:1px solid {C['line']};"
                                f"border-radius:0;font-family:{theme.MONO};font-size:12px;"
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
        rail.setFixedWidth(212)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(12, 16, 12, 12)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 0, 0)
        head.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(icon("mdi6.head-snowflake-outline", C["accent"]).pixmap(22, 22))
        head.addWidget(logo)
        head.addWidget(label("脑电分析", "Brand"))
        head.addStretch(1)
        lay.addLayout(head)
        lay.addSpacing(14)
        nb = btn("新建项目", self.new_project, None, "mdi6.plus")
        nb.setStyleSheet("text-align:left;padding-left:10px;")
        lay.addWidget(nb)
        ob = btn("打开项目", self.open_project, "nav", "mdi6.folder-open-outline")
        lay.addWidget(ob)
        lay.addSpacing(10)
        self.proj_name = label("未打开项目", "Sub")
        self.proj_name.setContentsMargins(10, 0, 0, 0)
        self.proj_name.setWordWrap(True)
        lay.addWidget(self.proj_name)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.nav_btns = {}
        for i, (name, ic, _) in enumerate(NAV):
            if name in NAV_GROUPS:
                cap = label(NAV_GROUPS[name], "Caption")
                cap.setContentsMargins(10, 14, 0, 4)
                lay.addWidget(cap)
            b = self._rail_btn(NAV_TEXT.get(name, name), ic)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, n=name: self.goto(n))
            self.nav.addButton(b, i)
            self.nav_btns[name] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.log_btn = self._rail_btn("运行日志", "mdi6.console-line")
        self.log_btn.setCheckable(True)
        self.log_btn.toggled.connect(self.logw.setVisible)
        lay.addWidget(self.log_btn)
        light = theme.MODE == "light"
        tb = self._rail_btn("切换到深色" if light else "切换到亮色",
                            "mdi6.weather-night" if light else "mdi6.white-balance-sunny")
        tb.setCheckable(False)
        tb.clicked.connect(self.toggle_theme)
        lay.addWidget(tb)
        ver = label(f"版本 {__version__}", "Caption")
        ver.setContentsMargins(10, 6, 0, 0)
        lay.addWidget(ver)
        return rail

    @staticmethod
    def _rail_btn(text, ic):
        b = btn(text, None, "nav")
        b.setIcon(icon(ic, C["muted"], C["text"]))
        b.setIconSize(QSize(17, 17))
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
            self.proj_name.setText(p.name)
            self.proj_name.setToolTip(p.folder)
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

    def toggle_theme(self):
        new = "dark" if theme.MODE == "light" else "light"
        name = "亮色" if new == "light" else "深色"
        if self.runner.busy:
            QMessageBox.information(self, "请稍等", "计算完成后再切换外观。")
            return
        if QMessageBox.question(self, "切换外观", f"换成{name}外观需要重启软件，项目会先自动保存。现在重启吗？") \
                != QMessageBox.Yes:
            return
        QSettings("EEGAnalyzer", "EEGAnalyzer").setValue("theme", new)
        if self.project:
            self.project.save()
        import sys
        from PySide6.QtCore import QProcess
        args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
        QProcess.startDetached(sys.executable, args)
        self.close()

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
