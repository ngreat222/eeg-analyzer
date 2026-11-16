"""One-turn, locally approved access to the controls of a selected window."""
import json
import os
import subprocess
from threading import Event

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QInputDialog, QMessageBox

from ..compat import resource_path


def native(mode, **data):
    payload = json.dumps(dict(mode=mode, owner=os.getpid(), **data), ensure_ascii=False)
    system = os.environ["SystemRoot"]
    exe = os.path.join(system, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    response = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-File",
                               resource_path("assets", "desktop_ui.ps1")], input=payload,
                              capture_output=True, encoding="utf-8", errors="replace",
                              timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if response.returncode:
        raise ValueError("无法操作该窗口。请检查窗口是否可见、控件是否变化，以及系统是否允许访问。")
    return json.loads(response.stdout)


class DesktopAccess(QObject):
    approve = Signal(object)

    def __init__(self, panel, window):
        super().__init__(panel)
        self.panel = panel
        self.window = window
        self.connection = panel.connection()[1:]
        self.revoked = Event()
        self.controls = {}
        self.approve.connect(self.confirm, Qt.QueuedConnection)

    @classmethod
    def choose(cls, panel):
        windows = native("windows")
        if not windows:
            raise ValueError("请先打开需要操作的软件窗口。")
        choices = [f"{w['title']}   ·   {w['handle']}" for w in windows]
        _, provider, model, url = panel.connection()
        selected, ok = QInputDialog.getItem(panel, "授权下一条消息",
            f"选择允许 {provider}（{model}，{url}）读取控件名称的窗口。点击和填写会分别请你确认。", choices, 0, False)
        return cls(panel, windows[choices.index(selected)]) if ok else None

    @Slot(object)
    def confirm(self, request):
        try:
            if self.revoked.is_set():
                return
            dialog = QMessageBox(self.panel)
            dialog.setWindowTitle("允许助手操作这个窗口？")
            dialog.setTextFormat(Qt.PlainText)
            dialog.setText(request["description"])
            dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            dialog.setDefaultButton(QMessageBox.No)
            request["allowed"] = dialog.exec() == QMessageBox.Yes
            if not request["allowed"]:
                self.revoked.set()
        finally:
            request["ready"].set()

    def run(self, args):
        if self.revoked.is_set():
            raise ValueError("桌面授权已结束")
        mode = args.get("action")
        if mode == "inspect":
            controls = native(mode, window=self.window)
            if self.revoked.is_set():
                raise ValueError("桌面授权已结束")
            self.controls = {str(i): item for i, item in enumerate(controls, 1)}
            return {"窗口":self.window["title"], "控件":[{"编号":k, "名称":v["name"], "动作":v["action"]}
                    for k,v in self.controls.items()]}
        item = self.controls.get(str(args.get("control")))
        if not item or mode not in ("press", "fill") or item["action"] != mode:
            raise ValueError("请先读取窗口并选择支持该动作的控件")
        value = args.get("text", "")
        if not isinstance(value, str) or len(value)>4000:
            raise ValueError("输入文本最多 4000 字")
        target = item["name"] or f"未命名控件 {args['control']}"
        action = f"点击「{target}」" if mode == "press" else f"替换「{target}」中的文本为：\n{value}"
        request = {"ready": Event(), "allowed":False, "description":self.window["title"]+"\n\n"+action}
        self.approve.emit(request)
        while not request["ready"].wait(.2):
            if self.revoked.is_set():
                raise ValueError("桌面授权已结束")
        if self.revoked.is_set() or not request["allowed"]:
            raise ValueError("用户已停止本轮桌面操作")
        result = native(mode, window=self.window, control=item, value=value)
        self.controls.clear()
        return result
