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
    lab.setObjectName("Sub")
    return lab


def info_box(min_h=80):
    w = QTextBrowser()
    w.setMinimumHeight(min_h)
    w.setOpenExternalLinks(False)
    w.setFrameShape(QFrame.StyledPanel)
    return w
