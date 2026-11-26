"""Check for BIAP releases without occupying the analysis worker or changing the project."""
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QStandardPaths, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QProgressBar, QVBoxLayout

from .. import __version__
from ..core import updater
from .widgets import btn, label


class _TransferSignals(QObject):
    completed = Signal(str, object, str)
    progress = Signal(object, object)


class UpdateController(QObject):
    def __init__(self, window, button, *, schedule=True):
        super().__init__(window)
        self.window = window
        self.button = button
        self.dialog = None
        self.update = None
        self.path = None
        self.job = ""
        self.closed = False
        self.error = ""
        self.install_requested = False
        self.stopped = threading.Event()
        self.signals = _TransferSignals()
        self.signals.completed.connect(self._completed, Qt.QueuedConnection)
        self.signals.progress.connect(self._progress, Qt.QueuedConnection)
        self.auto = window.settings.value("updates/automatic", True, type=bool)
        self.timer = QTimer(self)
        self.timer.setInterval(6 * 60 * 60 * 1000)
        self.timer.timeout.connect(self._automatic_check)
        button.clicked.connect(self.show)
        if schedule:
            self.timer.start()
            QTimer.singleShot(4000, self, self._automatic_check)

    def _automatic_check(self):
        if self.auto and not self.closed and not self.job and not self.install_requested:
            self._check()

    def _check(self):
        self._run("check", lambda: updater.check_for_update(__version__, cancelled=self.stopped.is_set))

    def _run(self, job, work):
        if self.closed or self.job:
            return
        self.job = job
        self.error = ""
        self.stopped = threading.Event()
        signals = self.signals

        def run():
            try:
                result = work()
            except Exception as exc:
                signals.completed.emit(job, None, str(exc) or type(exc).__name__)
            else:
                signals.completed.emit(job, result, "")

        threading.Thread(target=run, daemon=True, name=f"BIAP-update-{job}").start()
        self._render()

    @Slot()
    def show(self):
        if self.dialog is None:
            self._make_dialog()
        if not self.job and self.update is None:
            self._check()
        self._render()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _make_dialog(self):
        self.dialog = dialog = QDialog(self.window)
        dialog.setWindowTitle("BIAP 软件更新")
        dialog.resize(410, 245)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        self.title = label("", "H2", wrap=True)
        self.detail = label("", wrap=True)
        self.source = label("", "Sub", wrap=True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.notice = label("", wrap=True)
        self.notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for widget in (self.title, self.detail, self.source, self.bar, self.notice):
            layout.addWidget(widget)
        layout.addStretch()
        self.preference = QCheckBox("自动检查更新（每 6 小时）")
        self.preference.setChecked(self.auto)
        self.preference.toggled.connect(self._set_auto)
        layout.addWidget(self.preference)
        actions = QHBoxLayout()
        self.dismiss = btn("关闭", dialog.reject)
        self.action = btn("", self._action, "primary")
        actions.addStretch()
        actions.addWidget(self.dismiss)
        actions.addWidget(self.action)
        layout.addLayout(actions)
        dialog.finished.connect(self._dismissed)

    @Slot(bool)
    def _set_auto(self, enabled):
        self.auto = enabled
        self.window.settings.setValue("updates/automatic", enabled)

    def _dismissed(self, _result):
        self.install_requested = False

    @Slot()
    def _action(self):
        if self.job == "download":
            self.stopped.set()
            self.action.setEnabled(False)
            self.action.setText("正在取消…")
        elif not self.job:
            if self.path:
                if self._can_install():
                    self.install_requested = True
                    self._run("verify", lambda: updater.verify_file(self.path, self.update, cancelled=self.stopped.is_set))
            elif self.update:
                self.bar.setValue(0)
                cache = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
                if not cache:
                    self.error = "无法找到更新缓存目录。"
                    self._render()
                    return
                target = Path(cache) / "updates"
                self._run("download", lambda: updater.download_update(
                    self.update, target, progress=self.signals.progress.emit, cancelled=self.stopped.is_set))
            else:
                self._check()

    def _can_install(self):
        ai = self.window.assistant
        if self.window.runner.busy or (ai and ai.worker and ai.worker.isRunning()):
            self.error = "当前有任务正在运行。任务结束后可打开安装包。"
            self._render()
            return False
        return True

    @Slot(object, object)
    def _progress(self, received, total):
        if not self.closed and self.job == "download" and self.dialog and total:
            self.bar.setValue(int(received * 100 / total))

    @Slot(str, object, str)
    def _completed(self, job, result, error):
        if self.closed:
            return
        self.job = ""
        self.error = error
        if job == "check" and not error:
            new_version = result and (not self.update or self.update.version != result.version)
            if not result or not self.update or self.update.asset.sha256 != result.asset.sha256:
                self.path = None
            self.update = result
            self.button.setText(f"有新版 {result.version}" if result else f"检查更新 · {__version__}")
            if new_version and self.auto and not self.window.runner.busy:
                self.window.statusBar().showMessage(f"BIAP {result.version} 已发布，可从左下角查看更新。", 10000)
        elif job == "download":
            if self.stopped.is_set():
                self.error = "下载已取消。"
            elif not error:
                self.path = Path(result)
        elif job == "verify":
            launch = self.install_requested and self.dialog.isVisible()
            self.install_requested = False
            if error or not result:
                self.path = None
                self.error = "安装包校验失败，请重新下载。"
            elif launch and self._can_install():
                self._open_installer()
        self._render()

    def _open_installer(self):
        if sys.platform == "win32":
            # Leave closure to the user and BIAP's normal project-save flow.
            opened, _pid = QProcess.startDetached(str(self.path), ["/NOCLOSEAPPLICATIONS"], str(self.path.parent))
        else:
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))
        if opened:
            self.dialog.accept()
        else:
            self.error = "系统未能打开安装包，可再试一次。"

    def _render(self):
        if self.dialog is None:
            return
        update = self.update
        self.bar.setVisible(self.job == "download")
        self.notice.setText(self.error)
        self.notice.setVisible(bool(self.error))
        self.action.setEnabled(not self.job or self.job == "download")
        self.dismiss.setText("稍后" if update else "关闭")
        self.source.setVisible(bool(update))
        if update:
            host = urlsplit(update.source).hostname or ""
            self.source.setText(f"更新来源　{host}")
        if self.job == "check":
            title, detail, action = "正在检查更新…", f"当前版本 {__version__}", "检查中"
        elif self.job == "verify":
            title, detail, action = "正在校验安装包…", self.path.name, "校验中"
        elif self.job == "download":
            title, detail, action = f"正在下载 BIAP {update.version}", update.asset.name, "取消下载"
        elif self.path:
            title = f"BIAP {update.version} 已下载并通过校验"
            detail = "打开安装包后，请先导出需要保留的报告，再自行退出 BIAP 完成升级。"
            action = "打开安装包"
        elif update:
            title, detail, action = (f"BIAP {update.version} 可供下载",
                                     f"当前版本 {__version__}　·　安装包 {update.asset.size / (1024 ** 2):.1f} MB",
                                     "重新下载" if self.error else "下载更新")
        elif self.error:
            title, detail, action = "暂时无法检查更新", f"当前版本 {__version__}", "重试"
        else:
            title, detail, action = "已是最新版本", f"BIAP {__version__}", "再次检查"
        self.title.setText(title)
        self.detail.setText(detail)
        self.action.setText(action)

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        self.install_requested = False
        self.stopped.set()
