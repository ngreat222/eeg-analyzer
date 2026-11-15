"""工作窗口中可拖入 AI 助手的项目素材。"""
from pathlib import Path
from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QFileDialog, QMessageBox, QLabel
from PySide6.QtCore import Qt
from .widgets import WrappedLabel
from .assistant_panel import RefList, Studio, button
from . import project_bridge as bridge


class ProjectSources(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host=host
        lay=QVBoxLayout(self)
        self.title=WrappedLabel()
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.list=RefList()
        self.list.setMinimumWidth(185)
        lay.addWidget(self.list)
        lay.addWidget(button("加入常用文件…",self.pick))
        self.list.itemDoubleClicked.connect(self.open_item)
        self.list.setToolTip("双击加入消息，或拖到下方输入框。")

    def fill(self):
        p=self.host.project
        self.list.clear()
        if not p:
            self.title.setText("请先打开项目")
            return
        self.title.setText(p.name)
        def add(text,kind,name):
            Studio.item(self.list,text,("project",p.folder,kind,str(name)))
        add("整条分析流程","flow","整条流程")
        for step in bridge.steps(self.host):
            add("    "+step["name"],"step",step["name"])
        paths=set(p.assistant_data.get("files",[]))
        paths.update(str(f) for f in Path(p.folder).iterdir() if f.is_file() and f.name not in {"project.json","project.json.lock"} and not f.name.startswith("."))
        for subj,rec in p.recordings():
            add(bridge.event_name(rec),"events",rec.id)
            add("记录 · "+rec.label,"record",rec.id)
        for subj in p.subjects:
            if subj.epochs_file: add("分段 · "+subj.name,"epochs",subj.name)
        for name in p.assistant_data.get("tables",{}): add(name,"table",name)
        for path in sorted(paths): add(Path(path).name,"file",path)

    def open_item(self,item):
        self.host.ai_action.setChecked(True)
        if self.host.assistant: self.host.assistant.attach(item.data(Qt.UserRole))

    def pick(self):
        p=self.host.project
        if not p:return
        paths,_=QFileDialog.getOpenFileNames(self,"加入当前项目的常用文件",p.folder,
            "所有文件 (*)")
        if paths:
            old=p.assistant_data
            p.assistant_data={**old,"files":list(dict.fromkeys(old.get("files",[])+paths))}
            try:p.save()
            except Exception as exc:
                p.assistant_data=old
                QMessageBox.warning(self,"保存常用文件",str(exc))
            self.fill()
