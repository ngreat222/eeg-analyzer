"""在 Qt 主线程调度当前记录；每个计算结束并显示结果后才向模型回报。"""
from copy import copy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QVBoxLayout)

from ..core import project as pj
from . import project_bridge as bridge
from .common import log
from .events_page import _epoch_result
from .report_page import export_report
from .widgets import fig_png_bytes


def current_record(host):
    page = host.stack.currentWidget()
    if hasattr(page, "recs"):
        return page.recs.currentData()
    if page is host.pages["项目"]:
        rows = page._selected()
        if len(rows) == 1 and rows[0][1]:
            return rows[0][1].id
    records = host.project.recordings()
    return records[0][1].id if len(records) == 1 else None


class RecordWorkflow:
    stages = ("预处理", "事件", "分析", "报告")

    def __init__(self, panel, rid):
        self.panel = panel
        self.host = panel.host
        self.project = panel.bound
        self.rid = rid
        self.index = 0
        self.analysis = None
        self.scope = None
        self.items = []
        self.cancelled = False
        self.paused_request = None
        self.review_dialog = None

    def cancel(self):
        self.cancelled = True
        if self.review_dialog:
            self.review_dialog.reject()
        self.resume()

    def resume(self):
        if self.paused_request:
            args, self.paused_request = self.paused_request, None
            self.dispatch(*args)

    def dispatch(self, op, data, reply):
        try:
            if self.cancelled or self.host.project is not self.project:
                raise ValueError("任务已停止。")
            if op == "begin":
                reply.put(self.begin(data))
            elif op == "review":
                reply.put(self.review(data))
            elif op == "figure":
                if not self.items:
                    raise ValueError("该步还没有图。")
                figure = next((fig for title, fig in self.items[-1][1].figures if title == data), None)
                if figure is None:
                    raise ValueError("找不到本步的这张结果图。")
                reply.put(fig_png_bytes(figure))
            elif self.panel.workflow_pause.isChecked():
                self.paused_request = (op, data, reply)
            else:
                self.advance(reply)
        except Exception as exc:
            if self.analysis and not self.host.runner.busy:
                self.host.pages[self.analysis].record_scope = None
            reply.put(exc)

    def begin(self, analysis):
        if self.analysis:
            raise ValueError("这条记录已开始处理。")
        if self.host.runner.busy:
            raise ValueError("工作台正在运行另一项计算。")
        if not self.rid:
            ids = [rec.id for _, rec in self.project.recordings()]
            if not ids:
                raise ValueError("当前项目没有连续记录。")
            self.rid, ok = QInputDialog.getItem(self.panel, "选择当前记录", "这次处理哪条记录？", ids, 0, False)
            if not ok:
                raise ValueError("已取消选择记录。")
        methods = list(bridge.ANALYSIS)
        if analysis not in methods:
            analysis, ok = QInputDialog.getItem(self.panel, "选择分析", "本次报告包含哪项分析？", methods, 0, False)
            if not ok:
                raise ValueError("已取消选择分析。")
        self.analysis = analysis
        self.host.work_action.setChecked(True)
        self.navigate("预处理")
        self.panel.workflow_bar.show()
        self.panel.say("工作窗口", f"已打开 {self.rid}。本次运行预处理、分段、{analysis}，最后导出报告。")
        return {"记录": self.rid, "分析": analysis, "下一步": "预处理", "流程": bridge.steps(self.host)}

    def navigate(self, name):
        page = self.host.pages[name]
        if self.host.stack.currentWidget() is not page or (
                hasattr(page, "recs") and page.recs.currentData() != self.rid):
            self.host.goto(name, self.rid if hasattr(page, "recs") else None)
        return page

    def advance(self, reply):
        if self.index >= len(self.stages) or self.host.runner.busy:
            raise ValueError("工作台当前无法启动这一步。")
        name = self.stages[self.index]
        subject, rec = self.project.get_rec(self.rid)
        self.panel.say("工作窗口", f"{rec.id} · 正在{['预处理', '分段', '运行' + self.analysis, '导出报告'][self.index]}")
        if name == "预处理":
            page = self.navigate(name)
            task = page.process_one()
        elif name == "事件":
            page = self.navigate(name)
            page._save_pipe()
            self.scope = self.project
            rel = None
            if len(subject.recordings) > 1:
                # 单记录分析用独立分段文件，保留被试已合并的分段和缓存。
                self.scope = copy(self.project)
                self.scope._cache = {}
                subject = copy(subject)
                subject.recordings = [rec]
                self.scope.subjects = [subject]
                token = sha256(rec.id.encode()).hexdigest()[:24]
                rel = f"epochs/records/{token}-epo.fif"
            scope = self.scope
            def epoch():
                ep = pj.epoch_subject(scope, subject, log=log, relative_path=rel)
                return _epoch_result(scope, subject, ep)
            def display(res):
                self.project.save()
                if res.figures:
                    title, fig = res.figures[0]
                    page._set_fig(fig, title)
            task = page.run(f"分段 {rec.id}", epoch, display)
        elif name == "分析":
            page = self.host.pages[self.analysis]
            page.record_scope = self.scope
            self.navigate(self.analysis)
            page.on_show()
            page.scope.setCurrentIndex(page.scope.findData(subject.name))
            task = page.go()
        else:
            page = self.navigate(name)
            root = Path(self.project.folder) / "报告"
            root.mkdir(exist_ok=True)
            items = list(self.items)
            def export():
                with TemporaryDirectory(prefix=".biap-", dir=root) as staging:
                    folder = Path(staging) / "report"
                    html = Path(export_report(folder, rec.id, items))
                    if not html.is_file() or html.stat().st_size == 0:
                        raise ValueError("报告文件未生成。")
                    out = root / f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
                    folder.rename(out)
                    return str(out / html.name)
            page.show_items(items)
            task = page.run("导出本条记录的报告", export, lambda _: page.show_items(items))
        if not task:
            if name == "分析":
                page.record_scope = None
            raise ValueError("当前步骤未启动，请核对工作窗口中的选项。")
        task.observed = True
        def settled(res, error):
            try:
                if name == "分析":
                    page.record_scope = None
                if error or self.cancelled or self.host.project is not self.project:
                    raise ValueError(error or "任务已停止。")
                if name == "报告":
                    path = Path(res).resolve()
                    if not path.is_file() or path.stat().st_size == 0:
                        raise ValueError("报告文件未生成。")
                    result = {"已导出": str(path), "记录": rec.id}
                    self.panel.chat.action_button("打开本次报告", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
                else:
                    self.items.append((datetime.now(), res))
                    result = {"记录": rec.id, "结果": res.title, "指标": res.metrics,
                              "图表": [title for title, _ in res.figures], "说明": res.notes}
                    if name == "事件":
                        epochs = self.scope.epochs(subject.name)
                        result["分段通道"] = epochs.ch_names
                        result["事件条件"] = list(epochs.event_id)
                self.index += 1
                result["下一步"] = self.stages[self.index] if self.index < len(self.stages) else "完成"
                self.panel.say("工作窗口", str(result.get("已导出") or res.title))
                reply.put(result)
            except Exception as exc:
                reply.put(exc)
        task.signals.settled.connect(settled)

    def review(self, proposals):
        dialog = QDialog(self.panel)
        self.review_dialog = dialog
        dialog.setWindowTitle("运行已暂停 · 预览参数修改")
        layout = QVBoxLayout(dialog)
        heading = QLabel(f"{self.rid} · 勾选要采用的修改，再继续运行。参数保存到当前项目，本次只处理这条记录。")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        choices = QListWidget()
        layout.addWidget(choices)
        for p in proposals:
            lines = [p["title"]]
            lines += [f'{p["name"]} · {k}：{p["before"]["params"][k]} → {p["after"]["params"][k]}'
                      for k in bridge.requested_fields(p)]
            stage = "分段" if p["name"] == "事件分段" else "分析" if p["name"] in bridge.ANALYSIS else "预处理"
            lines.append(f"影响范围：{stage}及之后的步骤。")
            item = QListWidgetItem("\n".join(lines), choices)
            item.setCheckState(Qt.Unchecked)
        buttons = QDialogButtonBox()
        buttons.addButton("采用勾选项并继续", QDialogButtonBox.AcceptRole)
        keep = buttons.addButton("保留原值并继续", QDialogButtonBox.ActionRole)
        keep.clicked.connect(lambda: dialog.done(2))
        buttons.addButton("停止任务", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(570, 300)
        decision = dialog.exec()
        self.review_dialog = None
        if not decision or self.cancelled:
            self.cancelled = True
            raise ValueError("用户已停止任务。")
        selected = [p for i, p in enumerate(proposals)
                    if decision == 1 and choices.item(i).checkState() == Qt.Checked]
        if selected:
            self.panel.write_project(selected)
            earliest = min(1 if p["name"] == "事件分段" else 2 if p["name"] in bridge.ANALYSIS else 0 for p in selected)
            self.index = min(self.index, earliest)
            del self.items[self.index:]
            msg = bridge.parameter_receipt(self.project.json_path, selected)
        else:
            msg = "保留原值，继续运行。"
        self.panel.say("当前项目", msg)
        return {"用户决定": msg, "流程": bridge.steps(self.host), "下一步": self.stages[self.index]}
