"""界面公共部件：后台任务、日志、结果图标签页、表单小工具。"""
import logging
import sys
import traceback

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)


# ---------------------------------------------------------------- 日志
class LogBus(QObject):
    message = Signal(str)


LOG = LogBus()


def log(msg):
    LOG.message.emit(str(msg))


class _QtLogHandler(logging.Handler):
    def emit(self, record):
        try:
            log(self.format(record))
        except Exception:  # noqa: BLE001
            pass


class _Stream:
    """把 print / tqdm 输出转到日志面板。"""

    def __init__(self):
        self._buf = ""

    def write(self, s):
        if not s:
            return 0
        s = s.replace("\r", "\n")
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip()
            # tqdm 进度条刷新太频繁，只保留含 100% 的那一行
            if line and ("|" not in line or "100%" in line):
                log(line)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def install_log_capture():
    h = _QtLogHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    mne_logger = logging.getLogger("mne")
    mne_logger.handlers = [h]
    mne_logger.propagate = False
    sys.stdout = _Stream()
    sys.stderr = _Stream()


# ---------------------------------------------------------------- 后台任务
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str, str)


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # noqa: BLE001
            self.signals.failed.emit(str(e) or e.__class__.__name__, traceback.format_exc())
        else:
            self.signals.done.emit(res)


class TaskRunner(QObject):
    """一次只跑一个后台任务；忙的时候通知主窗口锁定按钮。"""
    busy_changed = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(1)
        self.busy = False
        self._tasks = set()

    def run(self, title, fn, on_done=None, on_error=None):
        if self.busy:
            QMessageBox.information(None, "请稍等", "上一个计算还没结束，请等它完成。")
            return
        self.busy = True
        self.busy_changed.emit(True, title)
        log(f"▶ {title}")
        task = _Task(fn)
        self._tasks.add(task)

        def done(res):
            self._finish(task)
            if on_done:
                try:
                    on_done(res)
                except Exception as e:  # noqa: BLE001
                    show_error(f"{title}：显示结果时出错", str(e), traceback.format_exc())
            log(f"✓ {title} 完成")

        def failed(msg, tb):
            self._finish(task)
            log(f"✗ {title} 出错：{msg}")
            log(tb)
            if on_error:
                on_error(msg)
            show_error(f"{title} 出错", msg, tb)

        task.signals.done.connect(done)
        task.signals.failed.connect(failed)
        self.pool.start(task)

    def _finish(self, task):
        self._tasks.discard(task)
        self.busy = False
        self.busy_changed.emit(False, "")


def show_error(title, msg, details=""):
    box = QMessageBox(QMessageBox.Warning, title, msg)
    if details:
        box.setDetailedText(details)
    box.exec()


# ---------------------------------------------------------------- 结果图
class FigureTabs(QTabWidget):
    """右侧的结果区：每张图一个可关闭的标签页，带 matplotlib 工具栏（缩放、保存图片）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.tabCloseRequested.connect(self._close)
        self._placeholder()

    def _placeholder(self):
        w = QLabel("运行分析后，图会显示在这里。\n每张图一个标签页，可用图上方的工具栏缩放、平移、保存图片。")
        w.setAlignment(Qt.AlignCenter)
        w.setStyleSheet("color:#888;font-size:14px;")
        w.setProperty("placeholder", True)
        self.addTab(w, "结果")
        self.tabBar().setTabButton(0, self.tabBar().ButtonPosition.RightSide, None)

    def _clear_placeholder(self):
        if self.count() and self.widget(0).property("placeholder"):
            self.removeTab(0)

    def add_figure(self, fig, title):
        if fig is None:
            return
        if isinstance(fig, (list, tuple)):
            for i, f in enumerate(fig):
                self.add_figure(f, f"{title} {i + 1}" if len(fig) > 1 else title)
            return
        if not isinstance(fig, Figure):
            log(f"（{title} 不是 matplotlib 图，已跳过）")
            return
        import matplotlib.pyplot as plt
        try:
            plt.close(fig)  # 从 pyplot 的全局列表里移除，避免越积越多
        except Exception:  # noqa: BLE001
            pass
        self._clear_placeholder()
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        canvas = FigureCanvasQTAgg(fig)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(NavigationToolbar2QT(canvas, page))
        lay.addWidget(canvas)
        page.figure = fig
        idx = self.addTab(page, title)
        self.setCurrentIndex(idx)
        canvas.draw_idle()

    def _close(self, idx):
        w = self.widget(idx)
        self.removeTab(idx)
        if w is not None:
            w.deleteLater()
        if self.count() == 0:
            self._placeholder()

    def close_all(self):
        while self.count():
            self._close(0)


# ---------------------------------------------------------------- 表单小工具
def fspin(value, lo=-1e6, hi=1e6, step=0.1, decimals=3, suffix=""):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(decimals)
    w.setSingleStep(step)
    w.setValue(value)
    if suffix:
        w.setSuffix(suffix)
    w.setKeyboardTracking(False)
    return w


def ispin(value, lo=0, hi=100000, step=1, suffix=""):
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(value)
    if suffix:
        w.setSuffix(suffix)
    return w


def line(text="", placeholder=""):
    w = QLineEdit(text)
    w.setPlaceholderText(placeholder)
    return w


def combo(items, current=None):
    w = QComboBox()
    w.addItems(items)
    if current is not None:
        w.setCurrentText(current)
    return w


def check(text, checked=False):
    w = QCheckBox(text)
    w.setChecked(checked)
    return w


def button(text, slot=None, primary=False):
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    if slot:
        b.clicked.connect(slot)
    return b


def group(title, rows=None, widgets=None):
    """rows: [(标签, 控件)]；widgets: 直接放进去的控件/按钮行。"""
    g = QGroupBox(title)
    lay = QVBoxLayout(g)
    if rows:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        for label, w in rows:
            if label is None:
                form.addRow(w)
            else:
                form.addRow(label, w)
        lay.addLayout(form)
    for w in widgets or []:
        if isinstance(w, (list, tuple)):
            lay.addLayout(hrow(*w))
        else:
            lay.addWidget(w)
    return g


def hrow(*widgets):
    h = QHBoxLayout()
    for w in widgets:
        if w == "stretch":
            h.addStretch(1)
        else:
            h.addWidget(w)
    return h


def note(text):
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    lab.setStyleSheet("color:#5f6b7a;")
    return lab


def info_box(min_h=80):
    w = QTextBrowser()
    w.setMinimumHeight(min_h)
    w.setOpenExternalLinks(False)
    w.setFrameShape(QFrame.StyledPanel)
    return w


class Page(QScrollArea):
    """左侧参数区的一页：标题 + 说明 + 若干分组，可滚动。"""

    def __init__(self, ctx, title, desc=""):
        super().__init__()
        self.ctx = ctx
        self.title_text = title
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(10)
        t = QLabel(title)
        t.setStyleSheet("font-size:17px;font-weight:600;")
        self.body.addWidget(t)
        if desc:
            self.body.addWidget(note(desc))
        self.setWidget(inner)

    def add(self, w):
        self.body.addWidget(w)
        return w

    def finish(self):
        self.body.addStretch(1)

    def on_show(self):
        """切换到这一页时调用，用来刷新通道列表等。"""

    # 快捷方式
    @property
    def state(self):
        return self.ctx.state

    def run(self, title, fn, on_done=None):
        self.ctx.runner.run(title, fn, on_done)

    def show_fig(self, fig, title):
        self.ctx.figs.add_figure(fig, title)

    def need(self, attr, msg):
        if getattr(self.state, attr, None) is None:
            QMessageBox.information(self, "还不能做这一步", msg)
            return False
        return True
