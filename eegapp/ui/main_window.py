"""主窗口：左侧步骤导航，中间参数页，右侧结果图，底部日志。"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont, QTextCursor
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QSplitter,
                               QStackedWidget, QTreeWidget, QTreeWidgetItem)

from .. import APP_NAME, __version__
from ..core import demo
from . import advanced_pages as ap
from . import group_pages as gp
from . import single_pages as sp
from .common import LOG, FigureTabs, TaskRunner, log
from .state import State

STYLE = """
QMainWindow, QWidget { font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }
QTreeWidget { border: none; background: #f4f6f9; padding-top: 6px; }
QTreeWidget::item { height: 28px; }
QTreeWidget::item:selected { background: #dbe8ff; color: #1a3d7c; }
QGroupBox { border: 1px solid #dde2ea; border-radius: 6px; margin-top: 12px; padding: 8px 8px 6px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #2b3a4f; font-weight: 600; }
QPushButton { padding: 5px 12px; border: 1px solid #c7ced9; border-radius: 4px; background: #ffffff; }
QPushButton:hover { background: #eef3fb; }
QPushButton:disabled { color: #9aa3ae; }
QPushButton[primary="true"] { background: #2f6fde; color: white; border-color: #2f6fde; }
QPushButton[primary="true"]:hover { background: #2a62c4; }
QPlainTextEdit { font-family: Consolas, "Microsoft YaHei UI"; font-size: 12px; background: #fbfbfc; }
QTabBar::tab { padding: 5px 10px; }
"""

CHAPTERS = [
    ("第 1 章 · 单被试分析", [sp.ReadPage, sp.ChannelPage, sp.ViewPage, sp.FilterPage, sp.BadsPage,
                          sp.ICAPage, sp.EpochPage, sp.ERPPage, sp.TFRPage, sp.ExportPage]),
    ("第 3 章 · 多被试分析（含第 2 章统计）", [gp.GroupDataPage, gp.GroupERPPage, gp.GroupTFRPage]),
    ("第 4 章 · 高级分析", [ap.DecDataPage, ap.TbytPage, ap.CTPage, ap.RSAPage, ap.IEMPage]),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 920)
        self.state = State()
        self.runner = TaskRunner(self)
        self._windows = []

        self.nav = QTreeWidget()
        self.nav.setHeaderHidden(True)
        self.nav.setMinimumWidth(230)
        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(400)
        self.figs = FigureTabs()
        self.pages = []
        for ch_title, pages in CHAPTERS:
            top = QTreeWidgetItem([ch_title])
            f = QFont()
            f.setBold(True)
            top.setFont(0, f)
            top.setFlags(top.flags() & ~Qt.ItemIsSelectable)
            self.nav.addTopLevelItem(top)
            for cls in pages:
                page = cls(self)
                idx = self.stack.addWidget(page)
                self.pages.append(page)
                it = QTreeWidgetItem([page.title_text])
                it.setData(0, Qt.UserRole, idx)
                top.addChild(it)
        self.nav.expandAll()
        self.nav.currentItemChanged.connect(self._nav)
        self.nav.setCurrentItem(self.nav.topLevelItem(0).child(0))

        split = QSplitter()
        split.addWidget(self.nav)
        split.addWidget(self.stack)
        split.addWidget(self.figs)
        split.setStretchFactor(2, 1)
        split.setSizes([230, 480, 790])
        self.setCentralWidget(split)

        self.logw = QPlainTextEdit()
        self.logw.setReadOnly(True)
        self.logw.setMaximumBlockCount(5000)
        dock = QDockWidget("运行日志", self)
        dock.setWidget(self.logw)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [170], Qt.Vertical)
        LOG.message.connect(self._append_log)

        self.status = QLabel("未读取数据")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(160)
        self.progress.hide()
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.runner.busy_changed.connect(self._busy)
        self._menus()
        self.setStyleSheet(STYLE)
        log(f"{APP_NAME} {__version__} 已启动。左侧按步骤操作；第 1 章可点「打开手册示例数据」体验。")

    # ------------------------------------------------------------ 菜单
    def _menus(self):
        m = self.menuBar().addMenu("文件")
        a = QAction("打开脑电文件…", self)
        a.triggered.connect(self._open)
        m.addAction(a)
        a = QAction("关闭所有结果图", self)
        a.triggered.connect(self.figs.close_all)
        m.addAction(a)
        m.addSeparator()
        a = QAction("退出", self)
        a.triggered.connect(self.close)
        m.addAction(a)
        m = self.menuBar().addMenu("工具")
        a = QAction("生成第 3、4 章合成演示数据…", self)
        a.triggered.connect(self._demo)
        m.addAction(a)
        a = QAction("清空日志", self)
        a.triggered.connect(self.logw.clear)
        m.addAction(a)
        m = self.menuBar().addMenu("帮助")
        a = QAction("关于", self)
        a.triggered.connect(self._about)
        m.addAction(a)

    def _open(self):
        page = self.pages[0]
        p, _ = QFileDialog.getOpenFileName(self, "选择脑电文件", os.path.expanduser("~/Desktop"),
                                           sp.reading.RAW_FILE_FILTER)
        if p:
            self.goto(0)
            page.path.setText(os.path.normpath(p))
            page.load()

    def _demo(self):
        folder = QFileDialog.getExistingDirectory(self, "选择保存演示数据的文件夹", os.path.expanduser("~/Desktop"))
        if not folder:
            return
        folder = os.path.join(folder, "脑电软件合成演示数据")
        self.runner.run("生成合成演示数据", lambda: demo.make_all(folder, log=log),
                        lambda f: QMessageBox.information(
                            self, "完成", f"已生成到：\n{f}\n\n这是模拟信号，只用于熟悉操作，不是真实数据。"))

    def _about(self):
        import mne
        QMessageBox.about(self, "关于", f"<b>{APP_NAME}</b> {__version__}<br><br>"
                          "把 EEG Handbook（MNE-Python 教程）的分析流程做成桌面程序：<br>"
                          "第 1 章单被试预处理与分析，第 3 章多被试 ERP / 时频（含第 2 章统计方法），"
                          "第 4 章解码、RSA、IEM。<br><br>"
                          f"MNE-Python {mne.__version__}　NeuroRA　inverted_encoding<br>"
                          "所有计算都在本机完成，不上传数据。")

    # ------------------------------------------------------------ 公共接口（页面通过 ctx 调用）
    def goto(self, idx):
        n = 0
        for i in range(self.nav.topLevelItemCount()):
            top = self.nav.topLevelItem(i)
            for j in range(top.childCount()):
                if n == idx:
                    self.nav.setCurrentItem(top.child(j))
                    return
                n += 1

    def data_changed(self):
        st = self.state
        parts = []
        if st.raw is not None:
            r = st.raw
            parts.append(f"数据：{os.path.basename(str(st.raw_path))}　{len(r.ch_names)} 通道　"
                         f"{r.info['sfreq']:g} Hz　{r.times[-1]:.1f} s　坏导 {len(r.info['bads'])}")
        if st.ica is not None:
            parts.append(f"ICA {st.ica.n_components_} 成分")
        if st.epochs is not None:
            parts.append(f"分段 {len(st.epochs)} 个")
        if st.power is not None:
            parts.append(f"时频（{st.tfr_condition}）")
        if st.group is not None:
            parts.append(f"组数据 {len(st.group.conditions)} 个条件")
        if st.dec is not None:
            parts.append(f"解码数据 {st.dec.data.shape[0]} 被试")
        self.status.setText("　|　".join(parts) or "未读取数据")
        cur = self.stack.currentWidget()
        if cur is not None and cur is not self.pages[0]:
            cur.on_show()

    def keep_window(self, win, on_close=None):
        """保存交互式浏览器窗口的引用，关闭时刷新状态（例如在浏览器里标的坏导）。"""
        self._windows.append(win)
        sig = getattr(win, "gotClosed", None)

        def closed(*_):
            if win in self._windows:
                self._windows.remove(win)
            if on_close:
                on_close()
            self.data_changed()
        if sig is not None:
            sig.connect(closed)
        elif hasattr(win, "canvas"):
            win.canvas.mpl_connect("close_event", closed)

    # ------------------------------------------------------------ 内部
    def _nav(self, cur, _prev):
        if cur is None:
            return
        idx = cur.data(0, Qt.UserRole)
        if idx is None:
            if cur.childCount():
                self.nav.setCurrentItem(cur.child(0))
            return
        self.stack.setCurrentIndex(idx)
        self.pages[idx].on_show()

    def _append_log(self, msg):
        self.logw.appendPlainText(msg)
        self.logw.moveCursor(QTextCursor.End)

    def _busy(self, busy, title):
        self.stack.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.statusBar().showMessage(f"正在{title}…")
            QApplication.setOverrideCursor(Qt.BusyCursor)
        else:
            self.statusBar().clearMessage()
            QApplication.restoreOverrideCursor()
            self.data_changed()

    def closeEvent(self, e):
        if self.runner.busy:
            r = QMessageBox.question(self, "还在计算", "还有计算没完成，确定退出吗？")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        for w in list(self._windows):
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        e.accept()
        if self.runner.busy:
            os._exit(0)  # 后台计算线程无法中断，直接结束进程
