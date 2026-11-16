"""可选 AI 助手：对话、附件、SQL 预览与工作副本。"""
import csv
import json
import os
import sys
from queue import Empty, Queue
from copy import deepcopy
from functools import partial
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QEvent, QIODevice, QModelIndex, QSaveFile, QSettings, Qt, QThread, Signal)
from PySide6.QtGui import QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QComboBox, QFormLayout, QMenu, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QStackedWidget, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QTableView,
    QTextBrowser, QVBoxLayout, QWidget)

from eegapp import theme
from eegapp.core import workbench as engine, attachments
from .widgets import WrappedLabel
from .chat_transcript import ChatTranscript
from . import project_bridge as bridge

MIME = "application/x-biap-reference"
USER = Qt.ItemDataRole.UserRole
STATE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BIAP-Workbench" / "workspace.json"


def button(text, fn, primary=False):
    b = QPushButton(text)
    b.clicked.connect(fn)
    if primary:
        b.setObjectName("primary")
    return b


def display(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


def panel():
    w = QWidget()
    layout = QVBoxLayout(w)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    return w, layout


class RefList(QListWidget):
    def __init__(self, reorder=False):
        super().__init__()
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.MoveAction if reorder else Qt.CopyAction)
        if reorder:
            self.setDragDropMode(QAbstractItemView.InternalMove)

    def startDrag(self, supported):
        item = self.currentItem()
        if not item:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData([item]))
        drag.setPixmap(self.viewport().grab(self.visualItemRect(item)))
        drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)

    def dropEvent(self, event):
        if event.source() is self and self.dragDropMode() == QAbstractItemView.InternalMove:
            row = self.currentRow()
            pos = event.position().toPoint()
            other = self.itemAt(pos)
            dest = self.count() if not other else self.row(other) + int(pos.y() > self.visualItemRect(other).center().y())
            if row >= 0 and dest not in (row, row+1):
                self.model().moveRow(QModelIndex(), row, QModelIndex(), dest)
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def mimeData(self, items):
        mime = super().mimeData(items)
        mime.setData(MIME, json.dumps(items[0].data(USER)).encode())
        return mime


class Composer(QPlainTextEdit):
    send_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() and not event.isAutoRepeat():
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class TableModel(QAbstractTableModel):
    def __init__(self, data):
        super().__init__()
        self.table = data

    def rowCount(self, parent=QModelIndex()):
        return len(self.table["rows"])

    def columnCount(self, parent=QModelIndex()):
        return len(self.table["columns"])

    def headerData(self, idx, direction, role):
        if role == Qt.DisplayRole:
            return self.table["columns"][idx] if direction == Qt.Horizontal else str(idx+1)

    def data(self, idx, role):
        if idx.isValid() and role == Qt.DisplayRole:
            v = self.table["rows"][idx.row()][idx.column()]
            return "" if v is None else str(v)


def table_view(data):
    view = QTableView()
    model = TableModel(data)
    model.setParent(view)
    view.setModel(model)
    view.setAlternatingRowColors(True)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    view.horizontalHeader().setStretchLastSection(True)
    return view


class Worker(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(object)
    workflow_requested = Signal(object)
    view_requested = Signal(object)

    def workflow_call(self, op, data=None):
        reply = Queue(1)
        self.workflow_requested.emit((op, data, reply))
        while not self.isInterruptionRequested():
            try:
                result = reply.get(timeout=.25)
            except Empty:
                continue
            if isinstance(result, Exception):
                raise ValueError(str(result))
            return result
        raise ValueError("工作流已停止。")
    def request_view(self, target):
        reply = Queue(1)
        self.view_requested.emit((target, reply))
        try:
            result = reply.get(timeout=12)
        except Empty:
            raise ValueError("工作窗口忙，未能确认打开参数页。")
        if isinstance(result, Exception):
            raise ValueError(str(result))
        return result

    def __init__(self, args):
        super().__init__()
        self.args = args

    def run(self):
        try:
            self.result.emit(engine.discuss(*self.args, progress=self.progress.emit))
        except Exception as e:
            self.error.emit(str(e))


class Studio(QMainWindow):
    def __init__(self, path=STATE, host=None):
        super().__init__()
        self.path = path
        self.host = host
        self.bound = host.project if host else None
        self.event_refs = {}
        self.files = {}
        self.ica_dirs = []
        self.state = bridge.session_state(host) if self.bound else json.loads(path.read_text(encoding="utf8")) if path.exists() else engine.initial_state()
        self.refs, self.history, self.proposals, self.undo = [], [], [], []
        self.worker = None
        self.workflow = None
        self.desktop = None
        self.review_links = []
        self.trace = []
        self.prefs = QSettings("EEGAnalyzer","Assistant")
        self.provider = self.prefs.value("provider", "DeepSeek")
        if self.provider not in engine.PROVIDERS:
            self.provider = "DeepSeek"
        self.keys = {}

        self.setWindowTitle("AI 助手")
        self.setWindowIcon(QIcon(str(engine.ROOT/"assets/icon.ico")))
        self.resize(1080, 750)
        left, lay = panel()
        left.setObjectName("left")
        lay.addWidget(self.heading("分析流程"))
        self.allflow = RefList()
        self.allflow.setMaximumHeight(42)
        self.item(self.allflow, "整条流程", ("flow", "全部步骤"))
        lay.addWidget(self.allflow)
        self.flow = RefList(True)
        self.flow.setToolTip("拖动排序；拖入右侧对话区可引用单个步骤。双击可查看参数。")
        lay.addWidget(self.flow, 3)
        lay.addWidget(self.heading("工作表"))
        self.tables = RefList()
        lay.addWidget(self.tables, 1)
        row = QHBoxLayout()
        row.addWidget(button("导入表格", self.import_table))
        row.addWidget(button("导出副本", self.export_table))
        lay.addLayout(row)
        center, lay = panel()
        center.setObjectName("ChatPane")
        self.setCentralWidget(center)
        head = QHBoxLayout()
        head.addWidget(self.heading("AI 助手"))
        head.addStretch()
        self.models_button = button("选择模型", self.configure)
        head.addWidget(self.models_button)
        more = QPushButton("更多")
        menu = QMenu(more)
        if host:
            menu.addAction("运行当前记录到导出…", self.draft_record_workflow)
            menu.addAction("核对已保存参数", self.show_saved_parameters)
        if sys.platform == "win32":
            menu.addAction("授权操作其他软件窗口…", self.choose_desktop)
        menu.addAction("新对话", self.reset_chat)
        menu.addAction("试试看（离线示例）", self.demo)
        menu.addAction("查看本次工具记录", lambda: self.inspect("工具记录", self.trace))
        menu.addAction("撤销上一次修改", self.revert)
        more.setMenu(menu)
        head.addWidget(more)
        lay.addLayout(head)
        self.workflow_bar = QWidget()
        flow_row = QHBoxLayout(self.workflow_bar)
        self.workflow_pause = QPushButton("暂停在下一步")
        self.workflow_pause.setCheckable(True)
        self.workflow_pause.toggled.connect(self.toggle_workflow_pause)
        flow_row.addWidget(self.workflow_pause)
        flow_row.addWidget(button("停止工作流", self.stop_workflow))
        lay.addWidget(self.workflow_bar)
        self.workflow_bar.hide()

        self.middle = QStackedWidget()
        lay.addWidget(self.middle, 1)
        conversation = QWidget()
        messages = QVBoxLayout(conversation)
        messages.setContentsMargins(0, 0, 0, 0)
        self.middle.addWidget(conversation)
        self.library = QTabWidget()
        if host:
            from .project_sources import ProjectSources
            host.project_sources = ProjectSources(host)
            self.library.addTab(host.project_sources, "项目素材")
        self.library.addTab(left, "流程与工作表")
        self.middle.addWidget(self.library)
        self.welcome = QLabel("想讨论什么？")
        self.welcome.setAlignment(Qt.AlignCenter)
        self.welcome.setObjectName("welcome")
        messages.addWidget(self.welcome, 1)
        self.chat = ChatTranscript()
        self.chat.hide()
        messages.addWidget(self.chat, 3)

        box = QFrame()
        box.setObjectName("composer")
        compose = QVBoxLayout(box)
        self.attachbar = QWidget()
        chiprow = QHBoxLayout(self.attachbar)
        chiprow.setContentsMargins(0, 0, 0, 0)
        self.attachment_label = WrappedLabel()
        self.attachment_label.setWordWrap(True)
        chiprow.addWidget(self.attachment_label, 1)
        self.image_button = button("查看图片", self.show_images)
        chiprow.addWidget(self.image_button)
        chiprow.addWidget(button("移除引用", self.clear_refs))
        self.attachbar.hide()
        compose.addWidget(self.attachbar)
        self.desktop_indicator = button("", self.stop_desktop)
        self.desktop_indicator.hide()
        compose.addWidget(self.desktop_indicator)
        self.edit = Composer()
        self.edit.setMaximumHeight(92)
        self.edit.setPlaceholderText("直接问我，也可以把流程、步骤或文件拖到这里…")
        self.edit.send_requested.connect(self.submit)
        compose.addWidget(self.edit)
        row = QHBoxLayout()
        self.refs_button = button("引用项目", self.toggle_library)
        self.refs_button.setCheckable(True)
        row.addWidget(self.refs_button)
        row.addWidget(button("附件", self.choose_files))
        row.addStretch()
        self.send = button("发送  ↑", self.submit, True)
        row.addWidget(self.send)
        compose.addLayout(row)
        lay.addWidget(box)
        for target in (self.chat.viewport(), self.edit.viewport(), self.welcome, center, box, self.attachbar):
            target.setAcceptDrops(True)
            target.installEventFilter(self)

        self.review = QDialog(self)
        self.review.setWindowTitle("看看这次的建议")
        self.review.resize(800, 580)
        lay = QVBoxLayout(self.review)
        self.pending = QListWidget()
        self.pending.setMaximumHeight(140)
        self.pending.currentRowChanged.connect(self.preview)
        lay.addWidget(self.pending)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        row = QHBoxLayout()
        row.addWidget(button("忽略这项", self.dismiss))
        row.addStretch()
        row.addWidget(button("继续聊", self.review.hide))
        row.addWidget(button("采用勾选项", self.accept, True))
        lay.addLayout(row)
        self.flow.itemDoubleClicked.connect(lambda item: self.inspect("步骤参数", self.step(item.text())))
        self.allflow.itemDoubleClicked.connect(lambda _: self.attach(("flow","全部步骤")))
        self.tables.itemDoubleClicked.connect(lambda item: self.inspect(item.text(), self.state["tables"][item.text()], True))
        self.flow.model().rowsMoved.connect(self.moved)
        self.refresh()
        self.edit.setFocus()
        self.apply_theme()
        self.sync_refs()
        if self.prefs.contains("provider"):
            self.models_button.setText(self.provider)

    def apply_theme(self):
        c = theme.C
        self.setStyleSheet(theme.QSS + f"""
            QWidget#ChatPane {{background:{c['bg']};}}
            QWidget#left {{background:{c['bg']};}}
            QLabel#heading {{font-size:15px;font-weight:600;}}
            QLabel#welcome {{font-size:18px;color:{c['text2']};font-weight:600;}}
            QTextBrowser {{border:0;border-radius:14px;background:{c['card']};font-size:14px;padding:10px;}}
            QWidget#Transcript {{background:{c['bg']};}}
            QFrame#UserBubble {{background:{c['soft']};border-radius:15px;}}
            QFrame#ReplyBubble {{background:{c['card']};border:1px solid {c['line']};border-radius:15px;}}
            QLabel#MessageAuthor {{color:{c['muted']};font-size:11px;font-weight:600;background:transparent;}}
            QTextBrowser#MessageBody {{background:transparent;color:{c['text']};border:0;padding:0;font-size:14px;}}
            QFrame#Activity {{background:transparent;border-left:2px solid {c['accent']};}}
            QPushButton#ActivityHeading {{background:transparent;border:0;text-align:left;color:{c['muted']};padding:3px 0;}}
            QPushButton#ReviewChanges {{background:{c['soft']};color:{c['on_soft']};border:0;padding:6px 12px;}}
            QFrame#composer {{border:1px solid {c['line']};border-radius:17px;background:{c['field']};}}
            QFrame#composer QPlainTextEdit {{border:0;background:transparent;}}
            QPushButton#primary {{background:{c['accent']};color:white;border-color:{c['accent']};}}
        """)

    def toggle_library(self, visible):
        if self.host:
            self.host.sources_action.setChecked(visible)
        else:
            self.show_library(visible)

    def show_library(self, visible):
        self.middle.setCurrentIndex(int(visible))
        self.refs_button.setChecked(visible)
        self.refs_button.setText("返回对话" if visible else "引用项目")
        if visible and self.host:
            self.host.project_sources.fill()
        elif not visible:
            self.edit.setFocus()

    def stop_desktop(self):
        if self.desktop:
            self.desktop.revoked.set()
            self.desktop = None
        self.desktop_indicator.hide()

    def choose_desktop(self):
        from .desktop_access import DesktopAccess
        if self.worker and self.worker.isRunning():
            return self.warn("请在发送下一条消息前选择窗口。")
        try:
            session = DesktopAccess.choose(self)
        except Exception as exc:
            return self.warn(exc)
        if session:
            self.stop_desktop()
            self.desktop = session
            self.desktop_indicator.setText("停止授权 · " + session.window["title"])
            self.desktop_indicator.show()

    def bind_project(self):
        self.stop_workflow()
        self.stop_desktop()
        if self.worker is not None:
            self.worker.bound = object()
        self.toggle_library(False)
        self.bound=self.host.project
        self.event_refs={}
        self.files.clear()
        self.ica_dirs.clear()
        self.refs.clear()
        self.history.clear()
        self.trace.clear()
        self.proposals.clear()
        self.undo.clear()
        self.pending.clear()
        self.review.hide()
        self.edit.clear()
        self.chat.clear()
        self.review_links.clear()
        self.chat.hide()
        self.welcome.show()
        self.sync_refs()
        self.state=bridge.session_state(self.host)
        self.refresh()

    def current_snapshot(self):
        latest=bridge.session_state(self.host)
        latest["tables"]={**self.state["tables"],**latest["tables"]}
        for name, rid in self.event_refs.items():
            latest["tables"][name]=bridge.event_table(self.bound,self.bound.get_rec(rid)[1])
        self.state=latest

    def show_saved_parameters(self):
        if not self.bound:
            return self.warn("请先打开一个项目。")
        try:
            saved = bridge.saved_parameters(self.bound.json_path)
        except (OSError, ValueError, KeyError) as exc:
            return self.warn(exc)
        self.inspect(f"{self.bound.name} · 已保存参数", saved)

    def project_ref(self, folder, kind, name):
        if not self.bound or self.host.project is not self.bound or self.bound.folder!=folder:
            raise ValueError("引用来自其他项目，请重新拖入。")
        self.current_snapshot()
        if kind=="events":
            rec=self.bound.get_rec(name)[1]
            name=bridge.event_name(rec)
            self.state["tables"][name]=bridge.event_table(self.bound,rec)
            self.event_refs[name]=rec.id
            kind="table"
        elif kind in ("record","epochs"):
            if kind=="record":
                from ..core import reading
                rec=self.bound.get_rec(name)[1]
                data=reading.read_header(rec.source)
                title="记录摘要 · "+name
                details=[["采样率Hz",data.info["sfreq"]],["采样点",data.n_times],["条件",rec.condition],
                         ["状态",rec.status],["通道",", ".join(data.ch_names)]]
            else:
                import mne
                subj=self.bound.get(name)
                data=mne.read_epochs(self.bound.path(subj.epochs_file),preload=False,verbose=False)
                title="分段摘要 · "+name
                details=[["采样率Hz",data.info["sfreq"]],["分段数",len(data)],["通道",", ".join(data.ch_names)],
                         ["事件计数",json.dumps(subj.n_trials,ensure_ascii=False)]]
            if hasattr(data,"close"): data.close()
            self.state["tables"][title]=engine.valid_table(["项目","值"],details)
            kind,name="table",title
        self.refresh()
        self.attach((kind,name))

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            mime = event.mimeData()
            if mime.hasFormat(MIME) or mime.hasUrls():
                if event.type()==QEvent.Drop:
                    if mime.hasFormat(MIME):
                        self.attach(tuple(json.loads(bytes(mime.data(MIME)))))
                    else:
                        for u in mime.urls():
                            if u.isLocalFile():
                                self.attach(("file",u.toLocalFile()))
                event.setDropAction(Qt.CopyAction)
                event.accept()
                return True
        return super().eventFilter(obj,event)

    def configure(self):
        dlg=QDialog(self)
        dlg.setWindowTitle("模型连接")
        form=QFormLayout(dlg)
        providers=QComboBox()
        providers.addItems(engine.PROVIDERS)
        providers.setCurrentText(self.provider)
        model, url, key = QLineEdit(), QLineEdit(), QLineEdit()
        key.setEchoMode(QLineEdit.Password)
        key.setPlaceholderText("仅本次会话；本地服务可留空")
        form.addRow("服务",providers)
        form.addRow("模型 ID",model)
        form.addRow("API 根地址",url)
        form.addRow("API Key",key)
        def load(name):
            default_url, default_model, env = engine.PROVIDERS[name]
            url.setText(self.prefs.value(name+"/url",default_url))
            url.setReadOnly(name not in ("自定义兼容服务","通义千问"))
            model.setText(self.prefs.value(name+"/model",default_model))
            key.setText(self.keys.get((name,url.text()),os.environ.get(env,"") if env and url.text()==default_url else ""))
        url.textEdited.connect(lambda _:key.clear())
        providers.currentTextChanged.connect(load)
        load(self.provider)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        dlg.resize(500,190)
        while dlg.exec():
            from urllib.parse import urlparse
            name, base = providers.currentText(), url.text().strip().rstrip("/")
            parsed=urlparse(base)
            local=parsed.hostname in ("localhost","127.0.0.1","::1")
            if not model.text().strip() or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not (parsed.scheme=="https" or local and parsed.scheme=="http"):
                self.warn("请填写模型 ID 和服务的 HTTPS 根地址。本机服务也可以使用 HTTP。")
                continue
            self.provider=name
            self.keys[(name,base)]=key.text().strip()
            self.prefs.setValue("provider",name)
            self.prefs.setValue(name+"/url",base)
            self.prefs.setValue(name+"/model",model.text().strip())
            self.models_button.setText(name)
            return True
        return False

    def connection(self):
        url, model, env = engine.PROVIDERS[self.provider]
        base = self.prefs.value(self.provider+"/url",url)
        key = self.keys.get((self.provider,base), os.environ.get(env,"") if env and base==url else "")
        return key,self.provider,self.prefs.value(self.provider+"/model",model),base

    def sync_refs(self):
        self.attachment_label.setText("  ·  ".join(f"{n}（{self.files[n]['kind']}）" if k=="attachment" else n for k,n in self.refs))
        self.image_button.setVisible(any(f["kind"] == "图片预览" for f in self.files.values()))
        self.attachbar.setVisible(bool(self.refs))
        self.edit.setFocus()

    def open_review(self):
        if self.proposals:
            self.review.show()
            self.review.raise_()
        else:
            self.say("BIAP","这次的建议已经处理好了。")

    @staticmethod
    def heading(text):
        w = QLabel(text)
        w.setObjectName("heading")
        return w

    @staticmethod
    def item(widget, text, ref):
        it = QListWidgetItem(text)
        it.setData(USER, ref)
        widget.addItem(it)

    def step(self, name):
        return next(s for s in self.state["flow"] if s["name"] == name)

    def refresh(self):
        self.flow.setDragDropMode(QAbstractItemView.DragOnly if self.bound else QAbstractItemView.InternalMove)
        self.flow.clear()
        self.tables.clear()
        for s in self.state["flow"]:
            self.item(self.flow, s["name"], ("step", s["name"]))
        for name in self.state["tables"]:
            self.item(self.tables, name, ("table", name))
        self.tables.setCurrentRow(0)

    def save(self, state):
        self.path.parent.mkdir(exist_ok=True, parents=True)
        data = json.dumps(state, ensure_ascii=False, allow_nan=False).encode()
        f = QSaveFile(str(self.path))
        if not f.open(QIODevice.WriteOnly) or f.write(data) != len(data) or not f.commit():
            raise OSError(f.errorString())

    def commit(self, state):
        if self.bound:
            self.state=state
            self.refresh()
            return
        self.save(state)
        self.undo = (self.undo + [self.state])[-8:]
        self.state = state
        self.refresh()

    def moved(self, *_):
        if self.bound:
            self.refresh()
            return
        names = [self.flow.item(i).text() for i in range(self.flow.count())]
        state = dict(self.state, flow=[self.step(n) for n in names])
        try:
            self.save(state)
            self.undo = (self.undo + [self.state])[-8:]
            self.state = state
        except Exception as e:
            self.refresh()
            self.warn(e)

    def warn(self, e):
        QMessageBox.warning(self, "流程工作台", str(e))

    def attach(self, ref):
        if ref[0] == "project":
            try: self.project_ref(*ref[1:])
            except Exception as exc: self.warn(exc)
            return
        if ref[0] == "file":
            self.add_file(ref[1])
        elif ref not in self.refs:
            self.refs.append(ref)
            self.sync_refs()

    def clear_refs(self):
        self.refs.clear()
        self.files.clear()
        self.ica_dirs.clear()
        self.sync_refs()

    def reset_chat(self):
        if self.worker and self.worker.isRunning():
            return self.warn("请等本轮请求结束。")
        self.toggle_library(False)
        self.stop_desktop()
        self.history.clear()
        self.chat.clear()
        self.review_links.clear()
        self.chat.hide()
        self.welcome.show()
        self.review.hide()
        self.trace.clear()
        self.clear_refs()
        self.edit.clear()
        self.proposals.clear()
        self.pending.clear()
        self.preview(-1)

    def choose_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,"添加聊天附件","","所有文件 (*)")
        for path in paths:
            self.add_file(path)

    def add_file(self, path):
        if Path(path).suffix.lower() in bridge.TABULAR:
            self.import_table(path=path)
            return
        self.add_attachment(path)

    def add_attachment(self,path):
        try:
            info=attachments.inspect_file(path)
            name=info["name"]
            n=1
            while name in self.files and self.files[name]["path"]!=info["path"]:
                n+=1
                name=f"{info['name']} ({n})"
            self.files[name]=info
            self.attach(("attachment",name))
            self.sync_refs()
            return name
        except (OSError,ValueError) as exc:
            self.warn(exc)

    def show_images(self):
        images = {name: item["path"] for name, item in self.files.items() if item["kind"] == "图片预览"}
        if not images:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("本轮图片")
        layout = QVBoxLayout(dlg)
        picker = QComboBox()
        picker.addItems(images.keys())
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        def show(name):
            pix = QPixmap(images[name])
            if pix.isNull():
                label.setText("图片预览不可用")
            else:
                screen = dlg.screen().availableGeometry()
                label.setPixmap(pix.scaled(int(screen.width()*.75), int(screen.height()*.72),
                                           Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(picker)
        layout.addWidget(label)
        picker.currentTextChanged.connect(show)
        show(picker.currentText())
        dlg.exec()

    def add_ica_images(self, record, token, picks, paths, folder, marked):
        # Keep the temporary images alive while the composer or a sending worker references them.
        self.ica_dirs.append(folder)
        for name, info in list(self.files.items()):
            old = info.get("ica")
            if old and (old["fit"] != token or old["component"] in picks):
                self.refs.remove(("attachment", name))
                del self.files[name]
        for i, path in zip(picks, paths):
            name = self.add_attachment(path)
            if name:
                self.files[name]["ica"] = dict(record=record, fit=token, component=i, excluded=i in marked)
        if not self.edit.toPlainText().strip():
            self.edit.setPlainText("看看这些 ICA 成分。请按编号结合地形、波形和频谱说明可能的伪迹和依据。拿不准的留给我复核，有充分依据且需要更改标记的建议放进预览。")
        self.sync_refs()

    def import_table(self, checked=False, path=None):
        if not path:
            path = QFileDialog.getOpenFileName(self, "选择工作表", "", "表格及文档 (*.csv *.tsv *.xlsx *.xls *.txt *.md *.json *.docx *.pdf)")[0]
        if not path:
            return
        try:
            from itertools import islice
            from datetime import date, time
            path = Path(path)
            if path.suffix.lower() == ".xlsx":
                from openpyxl import load_workbook
                book = load_workbook(path, read_only=True, data_only=True)
                try:
                    sheet, ok = QInputDialog.getItem(self, "选择工作表", "工作表", book.sheetnames, 0, False)
                    if not ok:
                        return
                    values = book[sheet].values
                    cols = [str(v) if v is not None else "" for v in next(values)]
                    rows = [[v.isoformat() if isinstance(v,(date,time)) else v for v in row]
                            for row in islice(values,engine.MAX_ROWS+1)]
                finally:
                    book.close()
                name = path.stem+" · "+sheet
            elif path.suffix.lower() == ".xls":
                import xlrd
                book = xlrd.open_workbook(path, on_demand=True)
                try:
                    sheet, ok = QInputDialog.getItem(self, "选择工作表", "工作表", book.sheet_names(), 0, False)
                    if not ok:
                        return
                    tab = book.sheet_by_name(sheet)
                    cols = list(map(str,tab.row_values(0)))
                    rows = [tab.row_values(i) for i in range(1,min(tab.nrows, engine.MAX_ROWS+2))]
                finally:
                    book.release_resources()
                name = path.stem+" · "+sheet
            elif path.suffix.lower() in (".csv",".tsv"):
                for enc in ("utf-8-sig","gb18030"):
                    try:
                        with path.open(encoding=enc,newline="") as f:
                            reader=csv.reader(f,delimiter="\t" if path.suffix.lower()==".tsv" else ",")
                            cols=next(reader)
                            rows=list(islice(reader,engine.MAX_ROWS+1))
                        break
                    except UnicodeDecodeError:
                        if enc=="gb18030": raise
                if any(len(row)!=len(cols) for row in rows):
                    raise ValueError("列数不一致，请检查文件的分隔符")
                name=path.stem
            else:
                self.add_attachment(path)
                return
            base=name
            i=1
            while name in self.state["tables"] or (self.bound and any(name==bridge.event_name(r) for _,r in self.bound.recordings())):
                i+=1
                name=f"{base} ({i})"
            table=engine.valid_table(cols,rows)
            self.commit(dict(self.state,tables={**self.state["tables"], name:table}))
            self.attach(("table",name))
            self.tables.setCurrentRow(list(self.state["tables"]).index(name))
        except Exception as e:
            if QMessageBox.question(self,"表格无法导入",f"{e}。仍作为普通附件加入吗？")==QMessageBox.Yes:
                self.add_attachment(path)

    def export_table(self):
        it=self.tables.currentItem()
        if not it: return
        path=QFileDialog.getSaveFileName(self,"导出工作副本",it.text()+"-副本.csv","CSV (*.csv)")[0]
        if path:
            try:
                import io
                t=self.state["tables"][it.text()]
                stream=io.StringIO(newline="")
                writer=csv.writer(stream)
                writer.writerow(t["columns"])
                writer.writerows(t["rows"])
                data=stream.getvalue().encode("utf-8-sig")
                f=QSaveFile(path)
                if not f.open(QIODevice.WriteOnly) or f.write(data)!=len(data) or not f.commit():
                    raise OSError(f.errorString())
            except Exception as e: self.warn(e)

    @staticmethod
    def text_view(text):
        w=QPlainTextEdit(text)
        w.setReadOnly(True)
        return w

    def inspect(self,title,obj,table=False):
        dialog=QDialog(self)
        dialog.setWindowTitle(title)
        layout=QVBoxLayout(dialog)
        layout.addWidget(table_view(obj) if table else self.text_view(display(obj)))
        dialog.resize(720,420)
        dialog.exec()

    def say(self,who,text):
        self.welcome.hide()
        self.chat.show()
        self.chat.message(who, text, markdown=who in engine.PROVIDERS)

    def submit(self):
        q=self.edit.toPlainText().strip()
        if not q or not self.send.isEnabled(): return
        connection = self.connection()
        if not connection[0] and (self.provider,connection[3]) not in self.keys:
            if not self.configure():
                return
            connection = self.connection()
        if not connection[0] and self.provider != "自定义兼容服务":
            return self.warn("请在模型连接里填写该服务的 API Key。")
        bound = self.bound
        if bound:
            try: self.current_snapshot()
            except Exception as exc: return self.warn(exc)
        provider = self.provider
        state=deepcopy(self.state)
        if self.desktop:
            if self.desktop.connection != connection[1:]:
                self.stop_desktop()
                return self.warn("模型连接已更改，请重新选择要授权的窗口。")
            state["desktop"] = self.desktop
        state["attachments"]=deepcopy(self.files)
        if bound:
            state["read_saved"] = partial(bridge.saved_parameters, bound.json_path)
            state["local_records"] = deepcopy({rec.id: (subject, rec) for subject, rec in bound.recordings()})
            state["local_pipeline"] = deepcopy(bound.pipeline)
            state["quality_threshold"] = self.host.pages["预处理"].segment_limit.value()
        for info in state["attachments"].values():
            item = info.get("ica")
            if item and bound:
                try:
                    page = self.host.pages["预处理"]
                    page.check_fit(item["fit"])
                    item["excluded"] = item["component"] in page.marked_components()
                except ValueError as exc:
                    self.edit.setPlainText(q)
                    return self.warn(exc)
        self.toggle_library(False)
        self.edit.clear()
        self.say("你",q)
        args=(state,list(self.refs),list(self.history),q,*connection)
        worker=Worker(args)
        if bound:
            state["show_parameters"] = worker.request_view
            from .record_workflow import RecordWorkflow, current_record
            self.workflow = RecordWorkflow(self, current_record(self.host))
            state["workflow_call"] = worker.workflow_call
            worker.workflow_requested.connect(self.dispatch_workflow)
            worker.view_requested.connect(self.open_parameter_view)
        worker.image_dirs = list(self.ica_dirs)
        self.worker=worker
        self.send.setEnabled(False)
        worker.bound = bound
        worker.question = q
        worker.provider = provider
        worker.activity = self.chat.add_activity()
        worker.progress.connect(self.on_tool_progress)
        worker.result.connect(self.on_answer)
        worker.error.connect(self.on_request_error)
        worker.finished.connect(self.finished)
        worker.start()

    def dispatch_workflow(self, request):
        if self.sender() is not self.worker or not self.workflow or self.worker.bound is not self.bound:
            request[2].put(ValueError("该任务已结束或项目已切换。"))
            return
        self.workflow.dispatch(*request)

    def draft_record_workflow(self):
        if not self.bound:
            return self.warn("请先打开有连续记录的项目。")
        if self.worker and self.worker.isRunning():
            return self.warn("当前请求仍在进行。")
        self.toggle_library(False)
        self.edit.insertPlainText("请把当前记录从预处理做到分析并导出报告。")
        self.edit.setFocus()

    def toggle_workflow_pause(self, paused):
        self.workflow_pause.setText("继续运行" if paused else "暂停在下一步")
        if self.workflow and not paused:
            self.workflow.resume()

    def stop_workflow(self):
        if self.workflow:
            self.workflow.cancel()
            if self.worker and self.worker.isRunning():
                self.worker.requestInterruption()

    def open_parameter_view(self, request):
        target, reply = request
        try:
            if self.sender() is not self.worker or self.worker.bound is not self.host.project:
                raise ValueError("项目已切换。")
            if target in bridge.PRE:
                page_name = "事件" if target == "事件分段" else "预处理"
                fields = {label: widget for label, _, widget in bridge.PRE[target]}
            elif target in bridge.ANALYSIS:
                page_name, fields = target, bridge.ANALYSIS[target]
            else:
                raise ValueError("该步骤没有可打开的参数页。")
            page = self.host.pages[page_name]
            self.host.work_action.setChecked(True)
            if self.host.stack.currentWidget() is not page:
                self.host.goto(page_name)
            page.show_settings(True)
            control = getattr(page, next(iter(fields.values())))
            for index in range(page.groups.count()):
                tab = page.groups.widget(index)
                if tab.isAncestorOf(control):
                    page.group_picker.setCurrentIndex(index)
                    tab.ensureWidgetVisible(control)
                    break
            reply.put({"已打开工作窗口": page.title, "设置类别": page.group_picker.currentText(),
                       "界面参数": {label: bridge.read_control(getattr(page, field)) for label, field in fields.items()}})
        except Exception as exc:
            reply.put(exc)

    def on_tool_progress(self, event):
        worker = self.sender()
        if worker is self.worker and worker.bound is self.bound:
            worker.activity.record(event)

    def on_answer(self, out):
        worker = self.sender()
        if worker is not self.worker or worker.bound is not self.bound:
            return
        worker.activity.finish()
        answer, proposals, trace = out
        self.say(worker.provider, answer)
        self.history.extend([{"role":"user","content":worker.question},{"role":"assistant","content":answer}])
        self.trace.extend(trace)
        self.add_proposals(proposals)

    def on_request_error(self, msg):
        worker = self.sender()
        if worker is self.worker and worker.bound is self.bound:
            worker.activity.finish(error=True)
            self.say("请求失败", msg)

    def finished(self):
        self.workflow = None
        self.workflow_bar.hide()
        self.workflow_pause.setChecked(False)
        self.stop_desktop()
        self.send.setEnabled(True)

    def demo(self):
        """完全本地的交互示例，清楚区分远端模型回复。"""
        if not self.refs:
            if not self.bound: self.attach(("table","EEGLAB 示例事件"))
            self.attach(("step","事件分段"))
        props=[]
        for kind,name in self.refs:
            try:
                if kind=="table":
                    cols=self.state["tables"][name]["columns"]
                    field="事件" if "事件" in cols else cols[0]
                    ident='"'+field.replace('"','""')+'"'
                    new="分析条件"
                    while new in cols: new+="副本"
                    sql=f'SELECT *, {ident} AS "{new}" FROM data'
                    _,p=engine.run_tool(self.state,self.refs,"transform_table",dict(target=name,sql=sql,title=f"从「{field}」复制分析条件列"))
                elif kind=="flow":
                    order=[s["name"] for s in self.state["flow"]]
                    a,b=(order.index("陷波"),order.index("高低通")) if self.bound else (order.index("检测坏道"),order.index("标记坏段"))
                    order[a],order[b]=order[b],order[a]
                    _,p=engine.run_tool(self.state,self.refs,"reorder_flow",dict(order=order,title="交换陷波和高低通" if self.bound else "交换坏段标记和坏道检测"))
                elif kind=="attachment":
                    info=self.files[name]
                    self.say("离线附件预览",f"{name} · {info['kind']} · {info['size']} 字节")
                    continue
                else:
                    params=self.step(name)["params"]
                    self.say("离线步骤预览",name+"\n"+display(params))
                    examples={"事件分段":{"结束秒":1.0}, "陷波":{"频率Hz":50}, "高低通":{"低通Hz":40},
                              "ERP":{"通道":"Cz"}, "ICA":{"算法":"infomax"}, "标记坏段":{"检测窗秒":2}}
                    if name not in examples:
                        continue
                    _,p=engine.run_tool(self.state,self.refs,"edit_step",dict(target=name,params=examples[name],title=f"示例：调整{name}参数"))
                props.append(p)
            except Exception as e: self.warn(e)
        self.say("离线示例","我们可以先看看改完是什么样，再决定用不用。")
        self.add_proposals(props)

    def add_proposals(self,props):
        first_new=len(self.proposals)
        for p in props:
            if self.bound:
                p["project_folder"]=self.bound.folder
                if p["kind"]=="table" and p["name"] in self.event_refs:
                    try: df=bridge.normalized_events(p["after"])
                    except ValueError as exc:
                        self.say("项目校验",f"{p['title']}：{exc}")
                        continue
                    p["after"]={"columns":list(df.columns),"rows":df.values.tolist()}
            self.proposals.append(p)
            it=QListWidgetItem(p["title"])
            it.setFlags(it.flags()|Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.pending.addItem(it)
        if first_new<len(self.proposals):
            self.pending.setCurrentRow(first_new)
            group = len(self.review_links)
            for prop in self.proposals[first_new:]:
                prop["_review_group"] = group
            link = self.chat.review_button(len(self.proposals)-first_new, lambda: self.review_group(group))
            self.review_links.append((link, group))

    def review_group(self, group):
        for i, prop in enumerate(self.proposals):
            if prop.get("_review_group") == group:
                self.pending.setCurrentRow(i)
                self.open_review()
                return

    def update_review_links(self):
        for link, group in self.review_links:
            count = sum(p.get("_review_group") == group for p in self.proposals)
            link.setEnabled(bool(count))
            link.setText(f"预览修改（{count} 项）" if count else "已处理")

    def preview(self,index):
        while self.tabs.count():
            w=self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()
        if not 0<=index<len(self.proposals): return
        p=self.proposals[index]
        if self.bound:
            dest = self.bound.name+" · "+p["name"]
            if p["name"] in self.event_refs: dest+="（当前记录的事件表；需重新分段）"
            elif p["kind"]=="table": dest+="（项目工作表副本）"
            elif p["kind"]=="order" or p["name"] in bridge.PRE:
                dest+="（需重新分段）" if p["name"]=="事件分段" else "（需重新预处理、分段）"
            self.review.setWindowTitle("采用到 "+dest)
        if p["kind"] == "ica":
            change = "保留 → 标记排除" if p["after"] else "标记排除 → 保留"
            self.tabs.addTab(self.text_view(f"{p['name']}\n{change}\n\n{p['reason']}\n\n采用后更新去除成分列表。运行预处理时使用选定的排除列表。"), "排除建议")
            return
        if p["kind"] == "table":
            before, after = p["before"], p["after"]
            added = [c for c in after["columns"] if c not in before["columns"]]
            removed = [c for c in before["columns"] if c not in after["columns"]]
            common = [(i,after["columns"].index(c)) for i,c in enumerate(before["columns"]) if c in after["columns"]]
            changed = []
            for i,(a,b) in enumerate(zip(before["rows"],after["rows"]),1):
                if any(a[x]!=b[y] for x,y in common):
                    changed.append(i)
            details = {"行数变化":[len(before["rows"]),len(after["rows"])],"新增列":added,
                       "移除列":removed,"共有列按位置比较发生变化的行数":len(changed),
                       "前20个变化行的位置":changed[:20]}
            self.tabs.addTab(self.text_view(display(details)),"差异")
        for title,key in [("当前","before"),("建议","after")]:
            obj=p[key]
            if p["kind"]=="table":
                self.tabs.addTab(table_view(obj),f"{title} · {len(obj['rows'])}行/{len(obj['columns'])}列")
            else:
                self.tabs.addTab(self.text_view(display(obj)),title)
        if p["code"]: self.tabs.addTab(self.text_view(p["code"]),"SQL 程序")
        self.tabs.setCurrentIndex(0 if p["kind"]=="table" else 1)

    def dismiss(self):
        i = self.pending.currentRow()
        if i >= 0:
            self.proposals.pop(i)
            self.pending.takeItem(i)
            self.update_review_links()
            self.preview(self.pending.currentRow())

    def accept(self):
        indexes=[i for i in range(self.pending.count()) if self.pending.item(i).checkState()==Qt.Checked]
        if not indexes: return self.warn("请先勾选需要采用的建议。")
        try:
            chosen=[self.proposals[i] for i in indexes]
            if self.bound:
                if self.host.project is not self.bound or any(p.get("project_folder")!=self.bound.folder for p in chosen):
                    raise ValueError("项目已切换，请重新生成建议")
                correction = next((p for p in chosen if p["kind"] == "ica"), None)
                restore = None
                if correction:
                    page = self.host.pages["预处理"]
                    _, rec = page.check_fit(correction["fit"])
                    restore = dict(auto=not page.excl.text().strip(), marks=sorted(page.marked_components()),
                                   identity=rec.reviewed_ica)
                self.write_project(chosen)
                inverse=[dict(p,before=p["after"],after=p["before"],base=engine.stamp(p["after"])) for p in reversed(chosen)]
                if restore:
                    next(p for p in inverse if p["kind"] == "ica")["restore_marks"] = restore
                self.undo=(self.undo+[inverse])[-8:]
            else:
                state=deepcopy(self.state)
                for p in chosen: engine.apply_proposal(state,p)
                self.commit(state)
        except Exception as e:
            return self.warn(e)
        titles=[self.proposals[i]["title"] for i in indexes]
        for i in reversed(indexes):
            self.proposals.pop(i)
            self.pending.takeItem(i)
        self.preview(self.pending.currentRow())
        self.review.hide()
        msg=("已写回「"+self.bound.name+"」：" if self.bound else "已采用：")+"、".join(titles)
        if self.bound:
            try:
                receipt = bridge.parameter_receipt(self.bound.json_path, chosen)
            except (OSError, ValueError, KeyError) as exc:
                receipt = f"保存后重新读取失败：{exc}"
            if receipt:
                msg += "\n" + receipt
        self.update_review_links()
        self.say("当前项目" if self.bound else "工作副本",msg)
        self.history.append({"role":"user","content":msg})

    def write_project(self, chosen):
        bridge.apply_selected(self.host,chosen,self.state["tables"])
        for prop in chosen:
            if prop["kind"]=="table": self.state["tables"][prop["name"]]=prop["after"]
        self.current_snapshot()
        self.refresh()
        page=self.host.stack.currentWidget()
        if page is self.host.pages["预处理"] or page is self.host.pages["事件"]:
            controls=[getattr(page,widget) for name,fields in bridge.PRE.items()
                      if (name=="事件分段")== (page is self.host.pages["事件"]) for _,_,widget in fields]
            values=[bridge.read_control(w) for w in controls]
            self.host.refresh()
            for w,value in zip(controls,values): bridge.put_control(w,value)
        else:
            self.host.refresh()
        if self.host.project_sources: self.host.project_sources.fill()

    def revert(self):
        if not self.undo: return self.warn("本次会话没有可撤销的修改。")
        if self.bound:
            try: self.write_project(self.undo[-1])
            except Exception as exc: return self.warn(exc)
            self.undo.pop()
            self.say("当前项目","已撤销上次采用的修改。")
            return
        state=self.undo[-1]
        try: self.save(state)
        except Exception as e: return self.warn(e)
        self.state=self.undo.pop()
        self.refresh()
        self.refs=[r for r in self.refs if r[0]!="table" or r[1] in state["tables"]]
        self.sync_refs()
        self.say("工作副本","已撤销上一次修改。")
        self.history.append({"role":"user","content":"已撤销上一次工作副本修改，以最新引用为准。"})

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.warn("本轮请求正在执行，请在结束后关闭窗口。")
            event.ignore()
        else:
            event.accept()
