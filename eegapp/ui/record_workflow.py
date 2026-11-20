"""在 Qt 主线程调度当前记录；每个计算结束并显示结果后才向模型回报。"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSpinBox, QTabWidget, QVBoxLayout)

from ..core import project as pj, workbench
from . import project_bridge as bridge
from .common import log
from .events_page import _epoch_result
from .report_page import export_new_report
from .widgets import CheckList, fig_png_bytes


def current_record(host):
    page = host.stack.currentWidget()
    if hasattr(page, "recs"):
        return page.recs.currentData()
    if hasattr(page, "selected_record_id") and not page.record_scope:
        selected = page.selected_record_id()
        if selected:
            return selected
        subject = host.project.find(page.scope.currentData()) if page.scope.currentData() else None
        if subject and len(subject.recordings) == 1:
            return subject.recordings[0].id
        return None
    if page is host.pages["项目"]:
        rows = page._selected()
        if len(rows) == 1 and rows[0][1]:
            return rows[0][1].id
    records = host.project.recordings()
    return records[0][1].id if len(records) == 1 else None


class RecordWorkflow:
    stages = ("预处理", "事件", "分析", "报告")

    def __init__(self, panel, rid, goal=""):
        self.panel = panel
        self.host = panel.host
        self.project = panel.bound
        self.rid = rid
        self.goal = goal
        self.ref_records = set()
        for kind, name in panel.refs:
            if kind == "table":
                if name in panel.event_refs:
                    self.ref_records.add(panel.event_refs[name])
                elif name.startswith("记录摘要 · "):
                    self.ref_records.add(name.removeprefix("记录摘要 · "))
                elif name.startswith("分段摘要 · "):
                    owner = self.project.find(name.removeprefix("分段摘要 · "))
                    if owner:
                        self.ref_records.update(rec.id for rec in owner.recordings)
            elif kind == "attachment":
                info = panel.files.get(name, {})
                if info.get("ica"):
                    self.ref_records.add(info["ica"]["record"])
                elif info.get("path"):
                    path = Path(info["path"])
                    self.ref_records.update(rec.id for _, rec in self.project.recordings() if Path(rec.source) == path)
        self.index = 0
        self.analysis = None
        self.scope = None
        self.items = []
        self.cancelled = False
        self.paused_request = None
        self.review_dialog = None
        self.last_result = {}
        self.decisions = []
        self.completed_inputs = None
        self.failed_step = ""
        self.failed_inputs = None
        self.analysis_visit = None

    def record_analysis(self):
        if self.scope is not None and self.index >= 2:
            return self.analysis, self.scope, self.rid
        return None

    def flow(self):
        return bridge.steps(self.host, record_analysis=self.record_analysis())

    def input_state(self, include_next=False):
        """Inputs behind completed results. A missing events table may be supplied before epoching."""
        subject, rec = self.project.get_rec(self.rid)
        pipeline = asdict(self.project.pipeline)
        pipeline.pop("event_import", None)
        record = asdict(rec)
        record.pop("events_file", None)
        paths = [rec.source]
        if rec.clean_file:
            paths.append(self.project.path(rec.clean_file))
        if self.index >= 2 or include_next:
            paths.append(self.project.events_path(rec))
        if self.scope is not None and self.index >= 2:
            paths.append(self.scope.path(self.scope.get(subject.name).epochs_file))
        files = []
        for path in paths:
            path = Path(path)
            try:
                stat = path.stat()
                files.append((str(path), stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                files.append((str(path), None, None))
        analysis_settings = {}
        if self.index >= 3:
            page = self.host.pages[self.analysis]
            for key, widget in vars(page).items():
                if isinstance(widget, CheckList):
                    analysis_settings[key] = widget.checked()
                elif isinstance(widget, (QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox)) and key not in ("group_picker", "scope"):
                    analysis_settings[key] = bridge.read_control(widget)
        flow = [step for step in self.flow() if step["name"] not in bridge.ANALYSIS or
                (self.index >= 3 or include_next and self.index == 2) and step["name"] == self.analysis]
        return pipeline, flow, record, subject.locs, subject.rename_from_locs, files, analysis_settings

    def remember(self, result):
        result["流程"] = self.flow()
        self.last_result = result
        self.completed_inputs = self.input_state()
        self.failed_step = ""
        self.failed_inputs = None
        return result

    def stage_failed(self, error):
        self.failed_step = str(error)
        self.failed_inputs = self.input_state(include_next=True)
        stage = self.analysis if self.stages[self.index] == "分析" else self.stages[self.index]
        self.panel.say("工作窗口", f"{self.rid} · {stage}未完成。{self.failed_step}")

    def continuation(self):
        if self.cancelled or self.host.project is not self.project:
            raise ValueError("上次任务已取消或项目已切换。请说明要在当前项目开始的任务。")
        if self.index == len(self.stages):
            raise ValueError("上次任务已经导出报告：" + str(self.last_result.get("已导出", "")))
        current = current_record(self.host)
        page = self.host.stack.currentWidget()
        if current is None and page is self.host.pages[self.analysis]:
            subject = self.project.get_rec(self.rid)[0]
            if page.scope.currentData() == subject.name and (len(subject.recordings) == 1 or
                    self.analysis_visit == self.host.navigation_visit):
                current = self.rid
        if current != self.rid:
            raise ValueError(f"上次处理的是 {self.rid}。请在工作窗口选中这条记录再继续，或明确开始处理另一条记录。")
        if Path(self.project.json_path).read_bytes() != self.project._saved_bytes or self.completed_inputs != self.input_state():
            raise ValueError("记录数据或分析参数已变化，请核对后明确开始新任务。")
        unresolved = self.failed_step if self.failed_step and self.failed_inputs == self.input_state(include_next=True) else ""
        return {"记录":self.rid, "分析":self.analysis, "原始要求":self.goal, "已完成":list(self.stages[:self.index]),
                "下一步":self.stages[self.index], "上步结果":self.last_result, "已确认":list(self.decisions),
                "待解决的错误":unresolved, "流程":self.flow()}

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
            elif op == "single":
                self.run_single(data, reply)
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
                if op == "advance" and not self.cancelled and self.host.project is self.project and self.index < len(self.stages):
                    self.stage_failed(exc)
            reply.put(exc)

    def choose_record(self):
        ids = [rec.id for _, rec in self.project.recordings()]
        if not ids:
            raise ValueError("当前项目没有连续记录。")
        pattern = "|".join(re.escape(rid) for rid in sorted(ids, key=len, reverse=True))
        mentioned = {value.casefold() for value in re.findall(r"(?<![a-zA-Z0-9_])(?:" + pattern + r")(?![a-zA-Z0-9_])", self.goal, re.I)}
        named = [rid for rid in ids if rid.casefold() in mentioned]
        current = current_record(self.host)
        refs = [rid for rid in ids if rid in self.ref_records]
        window_target = bool(re.search(r"工作(?:窗口|区|台)(?:里|中)?(?:当前)?(?:选中|打开)(?:的)?(?:这条)?记录", self.goal))
        ref_target = bool(re.search(r"(?:拖[入进来]+|附上|引用|附加)(?:的)?(?:那条|这条)?记录", self.goal))
        if len(named) == 1:
            rid = named[0]
            name = re.escape(rid)
            indirect = re.search(r"(?:不要|别|跳过|排除|不用)[^，,。；;]{0,12}" + name +
                                 "|" + name + r"\s*(?:的(?:事件表|参数|摘要)|作为(?:参考|对照))", self.goal, re.I)
            if not indirect and (not window_target or rid == current) and (not ref_target or refs == [rid]):
                return rid
        if not named:
            if window_target and not ref_target and current and self.rid == current:
                return current
            if ref_target and not window_target and len(refs) == 1:
                return refs[0]
            if len(ids) == 1:
                return ids[0]
            if current and self.rid == current and all(rid == current for rid in refs):
                return current
        details = [f"工作窗口选中：{current or '未选中单条记录'}"]
        if refs:
            details.append("对话引用：" + "、".join(refs))
        options = list(dict.fromkeys([*named, *([current] if current else []), *refs, *ids]))
        choice, ok = QInputDialog.getItem(self.panel, "选择处理的记录",
                                          "\n".join(details + ["这次要在哪条记录上运行？"]), options, 0, False)
        if not ok:
            self.cancelled = True
            raise ValueError("已取消选择记录，流程尚未启动。")
        return choice

    def single_analysis_page(self, step):
        current_page = self.host.stack.currentWidget()
        whole_group = bool(re.search(r"全组|全部被试|所有被试", self.goal))
        explicit_record = bool(self.ref_records) or bool(re.search(r"记录|这条", self.goal)) or any(
            record.id in self.goal for _, record in self.project.recordings())
        named_subjects = [s for s in self.project.subjects if s.name in self.goal]
        use_record = (explicit_record or self.rid and not named_subjects) and self.project.recordings() and not whole_group
        previous_scope = current_page.scope.currentData() if hasattr(current_page, "scope") else None
        page = self.host.pages[step]
        if use_record:
            self.rid = self.choose_record()
            _, rec = self.project.get_rec(self.rid)
            scope, _, path = pj.record_epoch_scope(self.project, rec)
            if not path or not Path(scope.path(path)).is_file():
                raise ValueError(f"{self.rid} 还没有可用的独立分段。可以只运行分段，或打开已有分段数据。")
            self.navigate(step)
            if not page.select_record_scope(self.rid):
                raise ValueError("这条记录的分段已经变化，请重新检查当前记录。")
            return page, self.rid
        if not self.project.done_subjects():
            raise ValueError("还没有可用分段，请选择已有分段数据或先单独运行分段。")
        self.navigate(step)
        from ..core.analysis import GROUP
        wanted = GROUP if whole_group and len(self.project.done_subjects()) > 1 else (
            named_subjects[0].name if len(named_subjects) == 1 else previous_scope)
        if wanted is not None:
            i = page.scope.findData(wanted)
            if i < 0:
                raise ValueError("当前分析页没有所选范围的已分段数据。")
            page.scope.setCurrentIndex(i)
        return page, page.scope.currentText()

    def review_single_settings(self, step, settings, target):
        if not settings:
            return
        name = "事件分段" if step == "分段" else step
        analysis_scope = None
        if step in bridge.ANALYSIS and self.rid and target == self.rid:
            _, rec = self.project.get_rec(self.rid)
            scoped, _, _ = pj.record_epoch_scope(self.project, rec)
            analysis_scope = (step, scoped, self.rid)
        state = bridge.session_state(self.host, record_analysis=analysis_scope)
        _, proposal = workbench.run_tool(state, [("step",name)], "edit_step",
            {"target":name, "params":settings, "title":f"本次{step}的参数"})
        selected = self.choose_proposals([proposal], f"{target} · 勾选要保存并用于本步的参数。")
        if selected:
            self.panel.write_project(selected, record_analysis=analysis_scope)
            self.panel.say("当前项目", bridge.parameter_receipt(self.project.json_path, selected))

    def run_single(self, data, reply):
        if self.host.runner.busy:
            raise ValueError("工作窗口正在计算，请等当前计算结束。")
        self.host.work_action.setChecked(True)
        step = data["step"] if isinstance(data, dict) else data
        settings = data.get("settings") if isinstance(data, dict) else None
        if step in bridge.ANALYSIS:
            page, target = self.single_analysis_page(step)
            self.review_single_settings(step, settings, target)
            button = getattr(page, "run_btn", None)
            if button is not None and not button.isEnabled():
                raise ValueError(button.toolTip() or "当前分析页还没有可用的条件。")
            if step == "解码" and len(page.classes.checked()) < 2:
                raise ValueError("当前范围至少需要两个已选类别，才能运行解码。")
            task = page.go()
        elif step == "报告导出":
            page = self.navigate("报告")
            items = list(page.displayed_items)
            if not items:
                raise ValueError("报告页还没有结果可供导出。")
            target = page.record_name or self.project.name
            root = Path(self.project.folder) / "报告"
            root.mkdir(exist_ok=True)
            task = page.run("只导出当前报告", lambda: export_new_report(root, target, items),
                            lambda path: page.remember_export(self.project, path))
        else:
            self.rid = target = self.choose_record()
            self.navigate("事件" if step == "分段" else "预处理")
            self.review_single_settings(step, settings, target)
            source = None
            if re.search(r"(?:用|从|对|把|基于|输入.{0,3})(?:当前|这条|该)?原始(?:记录|数据|信号|文件)?", self.goal):
                source = "raw"
            elif re.search(r"(?:已有|当前|上步|上一步|刚才的?)处理结果", self.goal):
                source = "current"
            if step == "分段":
                page = self.navigate("事件")
                task = page.epoch_record(source=source, interactive=False)
            elif step == "预处理":
                page = self.navigate("预处理")
                task = page.process_one()
            elif step in pj.SINGLE_STEPS:
                page = self.navigate("预处理")
                task = page.process_step(step, source=source)
            else:
                raise ValueError(f"工作台没有「{step}」这个步骤。")
        if not task:
            raise ValueError(f"「{step}」未启动，请核对当前工作页的输入。")
        task.observed = True
        self.panel.say("工作窗口", f"{target} · 正在单独运行{step}")
        def settled(result, error):
            if error or self.cancelled or self.host.project is not self.project:
                reply.put(ValueError(error or "任务已停止或项目已切换。"))
                return
            if step == "报告导出":
                path = Path(result).resolve()
                if not path.is_file() or not path.stat().st_size:
                    reply.put(ValueError("报告文件未生成。"))
                    return
                receipt = {"已导出":str(path), "结果":str(path)}
                self.panel.chat.action_button("打开本次报告", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
            else:
                receipt = {"结果":result.title, "图表":[title for title, _ in result.figures], "指标":result.metrics}
            self.panel.say("工作窗口", f"{target} · {step}已完成")
            reply.put(dict(receipt, 步骤=step, 范围=target))
        task.signals.settled.connect(settled)

    def begin(self, analysis):
        if self.analysis:
            raise ValueError("这条记录已开始处理。")
        if self.host.runner.busy:
            raise ValueError("工作台正在运行另一项计算。")
        self.rid = self.choose_record()
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
        return self.remember({"记录": self.rid, "分析": analysis, "下一步": "预处理"})

    def navigate(self, name):
        page = self.host.pages[name]
        if self.host.stack.currentWidget() is not page or (
                hasattr(page, "recs") and page.recs.currentData() != self.rid):
            self.host.goto(name, self.rid if hasattr(page, "recs") else None)
        return page

    def show_record_analysis(self):
        page = self.host.pages[self.analysis]
        page.record_scope = self.scope
        try:
            if self.host.stack.currentWidget() is page:
                page.on_show()
            else:
                self.navigate(self.analysis)
            subject = self.project.get_rec(self.rid)[0]
            page.scope.setCurrentIndex(page.scope.findData(subject.name))
            self.analysis_visit = self.host.navigation_visit
        finally:
            page.record_scope = None
        return page

    def saved_inputs_by_stage(self):
        """Track durable inputs only. Browsing another page or changing selection is not a data edit."""
        subject, record = self.project.get_rec(self.rid)
        pipeline = asdict(self.project.pipeline)
        epoch_keys = {attr for _, attr, _ in bridge.PRE["事件分段"]} | {"events","events_explicit"}
        epoching = {key:pipeline.pop(key) for key in epoch_keys}
        pipeline.pop("event_import", None)
        path = Path(self.project.events_path(record))
        stamp = path.stat() if path.is_file() else None
        analysis = deepcopy(self.project.assistant_data.get("analysis", {}).get(self.analysis, {}))
        analysis.pop("scope", None)
        raw = asdict(record)
        for derived in ("status","events_file","condition","note"):
            raw.pop(derived, None)
        return ((pipeline, raw, subject.locs, subject.rename_from_locs),
                (epoching, record.condition, record.events_file, stamp and (stamp.st_size, stamp.st_mtime_ns)),
                analysis)

    def invalidate_from(self, earliest):
        self.index = min(self.index, earliest)
        del self.items[self.index:]
        if self.index < 2:
            self.scope = None
        if earliest < 2:
            self.last_result = {}
        self.failed_step = ""
        self.failed_inputs = None
        self.completed_inputs = self.input_state()

    def operate_window(self, workspace, args, reply):
        """Keep explicit GUI operations and the current record's execution checkpoint in sync."""
        action = args.get("action")
        if self.cancelled or self.host.project is not self.project:
            raise ValueError("本次任务已停止或项目已切换。")
        if action == "press":
            entry = workspace.handles.get(str(args.get("target", "")))
            if entry and entry[0] == workspace.location() and entry[5] is workspace.surface():
                location, widget, title, op, _, page = entry
                stage = {"处理这条记录":"预处理","分段这个被试":"事件","导出报告":"报告"}.get(title)
                if widget is getattr(page, "run_btn", None):
                    stage = location[0]
                if stage and op == "press":
                    if not widget.isEnabled() or not widget.isVisibleTo(page):
                        raise ValueError("该按钮当前不可用，请重新读取工作窗口。")
                    expected = self.stages[self.index]
                    page_name = self.analysis if expected == "分析" else expected
                    if stage != page_name or (hasattr(page, "recs") and page.recs.currentData() != self.rid):
                        raise ValueError(f"当前任务处理 {self.rid}，下一步是{page_name}。要另行运行，请先结束当前记录任务。")
                    reply.put({"执行当前步骤":True, "记录":self.rid, "下一步":expected})
                    return
                if op == "press" and ("批量处理全部" in title or "批量分段全部" in title):
                    raise ValueError("批量运行涉及其他记录。请先结束单记录任务，再从工作窗口启动批量操作。")
        if action == "open" and workspace.surface() is self.host.stack.currentWidget():
            from .main_window import NAV_TEXT
            key = next((k for k in ("预处理","事件") if args.get("target") in (k, NAV_TEXT.get(k))), None)
            if key:
                self.navigate(key)
            elif self.record_analysis() and args.get("target") in (self.analysis, NAV_TEXT.get(self.analysis)):
                self.show_record_analysis()
        if action in ("inspect","view","open","viewer_inspect","viewer_view","viewer_seek","select"):
            workspace.perform(args, reply)
            return
        before = self.saved_inputs_by_stage()
        workflow = self
        class WindowReply:
            def put(self, result):
                if isinstance(result, dict) and workflow.host.project is workflow.project:
                    try:
                        after = workflow.saved_inputs_by_stage()
                        changed = next((i for i in range(len(after)) if before[i] != after[i]), None)
                        if changed is not None:
                            workflow.invalidate_from(changed)
                            if workflow.record_analysis() and workflow.host.stack.currentWidget() is workflow.host.pages[workflow.analysis]:
                                workflow.show_record_analysis()
                                if "控件" in result:
                                    result = workspace.snapshot()
                            notice = f"{workflow.rid} · {workflow.stages[changed]}设置已写回项目"
                            workflow.decisions.append({"决定":notice})
                            result.update(已确认修改=notice, 流程=workflow.flow(), 下一步=workflow.stages[workflow.index])
                    except Exception as exc:
                        result = exc
                reply.put(result)
        workspace.perform(args, WindowReply())

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
            if not self.host.finish_mark_editors([rec.id]):
                raise ValueError("波形标记尚未保存。请核对当前记录后再继续分段。")
            page._save_pipe()
            def epoch():
                self.scope, scoped_subject, ep = pj.epoch_record(self.project, rec, source="current", log=log)
                return _epoch_result(self.scope, scoped_subject, ep)
            def display(res):
                self.project.save()
                if res.figures:
                    title, fig = res.figures[0]
                    page._set_fig(fig, title)
            task = page.run(f"分段 {rec.id}", epoch, display)
        elif name == "分析":
            page = self.show_record_analysis()
            page.record_scope = self.scope
            task = page.go()
        else:
            page = self.navigate(name)
            root = Path(self.project.folder) / "报告"
            root.mkdir(exist_ok=True)
            items = list(self.items)
            page.show_items(items, rec.id)
            def exported(path):
                page.remember_export(self.project, path)
                page.show_items(items, rec.id)
            task = page.run("导出本条记录的报告", lambda: export_new_report(root, rec.id, items), exported)
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
                        result["事件条件"] = [name for name, count in self.scope.condition_counts(subject.name)[subject.name].items() if count]
                        result["分段时间范围秒"] = [float(epochs.tmin), float(epochs.tmax)]
                        result["分段采样率Hz"] = float(epochs.info["sfreq"])
                        if self.project.pipeline.epoch_mode == pj.EPOCH_FIXED:
                            result["时间零点"] = "固定长度分段的开头。选择段内基线时，请在预览中说明所用区间及含义。"
                self.index += 1
                result["下一步"] = self.stages[self.index] if self.index < len(self.stages) else "完成"
                self.panel.say("工作窗口", str(result.get("已导出") or res.title))
                reply.put(self.remember(result))
            except Exception as exc:
                if not self.cancelled and self.host.project is self.project and error:
                    try:
                        self.stage_failed(error)
                    except (KeyError, OSError):
                        self.completed_inputs = None
                reply.put(exc)
        task.signals.settled.connect(settled)

    def choose_proposals(self, proposals, caption):
        from .assistant_panel import display, table_view
        dialog = QDialog(self.panel)
        self.review_dialog = dialog
        dialog.setWindowTitle("运行已暂停 · 预览参数修改")
        layout = QVBoxLayout(dialog)
        heading = QLabel(caption)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        choices = QListWidget()
        layout.addWidget(choices)
        for p in proposals:
            lines = [p["title"]]
            if p["kind"] == "step":
                lines += [f'{p["name"]} · {k}：{p["before"]["params"][k]} → {p["after"]["params"][k]}'
                          for k in bridge.requested_fields(p)]
            elif p["kind"] == "ica":
                lines += [p["name"], "标记为排除" if p["after"] else "标记为保留", p["reason"]]
            else:
                lines.append(p["name"])
            item = QListWidgetItem("\n".join(lines), choices)
            item.setCheckState(Qt.Unchecked)
        if any(p["kind"] != "step" for p in proposals):
            details = QTabWidget()
            layout.addWidget(details)
            def show_change(index):
                while details.count():
                    old = details.widget(0)
                    details.removeTab(0)
                    old.deleteLater()
                p = proposals[index]
                for label, key in (("当前","before"),("建议","after")):
                    view = table_view(p[key]) if p["kind"] == "table" else self.panel.text_view(display(p[key]))
                    details.addTab(view, label)
                if p.get("code"):
                    details.addTab(self.panel.text_view(p["code"]), "操作说明")
            choices.currentRowChanged.connect(show_change)
            choices.setCurrentRow(0)
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
        return [p for i, p in enumerate(proposals) if decision == 1 and choices.item(i).checkState() == Qt.Checked]

    def review(self, proposals):
        selected = self.choose_proposals(proposals,
            f"{self.rid} · 勾选要采用的修改，再继续运行。参数保存到当前项目，本次只处理这条记录。")
        changed = None
        if selected:
            before_inputs = self.saved_inputs_by_stage()
            self.panel.write_project(selected, record_analysis=self.record_analysis())
            after_inputs = self.saved_inputs_by_stage()
            changed = next((i for i in range(len(after_inputs)) if before_inputs[i] != after_inputs[i]), None)
            if changed is not None:
                self.invalidate_from(changed)
                if self.record_analysis() and self.host.stack.currentWidget() is self.host.pages[self.analysis]:
                    self.show_record_analysis()
            msg = bridge.parameter_receipt(self.project.json_path, selected) or "已保存：" + "、".join(p["title"] for p in selected)
        else:
            msg = "保留原值。"
        self.panel.say("当前项目", msg)
        self.decisions.append({"决定":msg, "字段":[[p["name"], field] for p in proposals if p["kind"] == "step"
                                               for field in bridge.requested_fields(p)]})
        self.completed_inputs = self.input_state()
        return {"用户决定":msg, "已采用对象":[p["name"] for p in selected], "已确认修改":bool(selected and changed is not None),
                "流程":self.flow(), "下一步":self.stages[self.index]}
