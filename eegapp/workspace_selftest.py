"""Drive BIAP through the assistant using a scripted, in-process model endpoint."""
import io
import json
import time
from pathlib import Path
from unittest.mock import patch


def _window(action, target="", **fields):
    return "work_window", dict(action=action, target=target, **fields)


def single_steps(app, folder):
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QPushButton
    from . import compat
    from .core import project as pj
    from .ui.main_window import MainWindow

    project = pj.Project(str(Path(folder) / "独立处理"))
    sample = compat.resource_path("sample_data", "eeglab_data.set")
    record, = pj.add_raw_files(project, [sample], subject="S1", condition="图像")
    other, = pj.add_raw_files(project, [sample], subject="S1", condition="文本")
    owner = project.subjects[0]
    owner.locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
    owner.rename_from_locs = True
    project.pipeline.resample = 100
    project.pipeline.reject_uv = 0
    project.save()
    window = MainWindow(settings=QSettings(str(Path(folder) / "single.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.show()
    window.set_project(project, quiet=True)

    def wait():
        deadline = time.monotonic() + 90
        while window.runner.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        app.processEvents()
        assert not window.runner.busy

    try:
        window.goto("预处理", record.id)
        pre = window.pages["预处理"]
        pre.step_picker.setCurrentText("重采样")
        with patch.object(pj, "fit_ica", side_effect=AssertionError("意外运行 ICA")), \
                patch.object(pj, "preprocess_recording", side_effect=AssertionError("意外执行完整预处理")):
            pre.run_step_btn.click()
            wait()
            assert record.status == pj.REC_PARTIAL and [s["step"] for s in record.step_history] == ["重采样"]
            assert not record.record_epochs_file and not owner.epochs_file and not other.clean_file
            window.goto("事件", record.id)
            events = window.pages["事件"]
            single = next(b for b in events.findChildren(QPushButton) if b.text() == "只分段当前记录")
            assert events.epoch_input.currentData() == "current"
            single.click()
            wait()
            scope, subject, rel = pj.record_epoch_scope(project, record)
            assert rel and scope.epochs(subject.name).info["sfreq"] == 100
            window.goto("事件", other.id)
            assert events.epoch_input.currentData() == "raw"
            single.click()
            wait()
            other_scope, other_subject, other_rel = pj.record_epoch_scope(project, other)
            assert other_rel and rel != other_rel and not other.clean_file and not owner.epochs_file
            assert other_scope.epochs(other_subject.name).info["sfreq"] == 128
        restored = pj.Project.load(project.folder)
        window.set_project(restored, quiet=True)
        window.goto("ERP")
        erp = window.pages["ERP"]
        assert erp.select_record_scope(other.id) and all("图像" not in c for c in erp.conds.names())
        assert erp.select_record_scope(record.id) and all("文本" not in c for c in erp.conds.names())
        erp.test.setCurrentText("不做检验")
        erp.run_btn.click()
        wait()
        result = window.reports[-1][1]
        assert result.kind == "ERP" and result.params["记录"] == record.id
        assert not list(Path(project.folder).rglob("*.html"))
        return "两条同被试记录分别从单步重采样结果和原始数据分段，重开后仅分析所选记录，无自动导出"
    finally:
        window.close()


def check_preprocess_views(app, folder, screenshot=None):
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QLabel
    from . import compat
    from .core import project as pj
    from .ui.main_window import MainWindow
    from .ui.widgets import FigureCard

    project = pj.Project(str(Path(folder) / "EEGLAB 预处理示例"))
    sample = compat.resource_path("sample_data", "eeglab_data.set")
    records = [pj.add_raw_files(project, [sample], subject=f"EEGLAB_连续记录_预处理示例_2026.09.{day}_01")[0]
               for day in ("14", "15")]
    for subject, rec in project.recordings():
        subject.locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
        subject.rename_from_locs = True
        rec.ica_manual, rec.ica_exclude = True, [1]
    project.pipeline.ica, project.pipeline.ica_n = True, "6"
    project.save()
    window = MainWindow(settings=QSettings(str(Path(folder) / "preprocess.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.resize(1500, 860)
    window.show()
    window.set_project(project, quiet=True)
    window.goto("预处理", records[0].id)
    page = window.pages["预处理"]

    def wait():
        deadline = time.monotonic() + 90
        while window.runner.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        app.processEvents()
        assert not window.runner.busy, "预处理任务超时"

    def text(widget):
        return " ".join(label.text() for label in widget.findChildren(QLabel))

    try:
        with patch("eegapp.ui.common.show_error") as error:
            window.ai_action.setChecked(True)
            app.processEvents()
            assert records[0].source in page.recs.toolTip()
            assert page.recs.width() > page.recs.fontMetrics().horizontalAdvance(records[0].id) + 40
            page.preview()
            wait()
            assert page.work_tabs.currentIndex() == 1 and len(page.view.findChildren(FigureCard)) >= 2
            page.process_one()
            assert page.work_tabs.currentIndex() == 0 and page.steps.isVisibleTo(page)
            wait()
            assert page.steps.status.text() == "已处理" and page.work_tabs.currentIndex() == 0
            assert "已去除 IC-001" in text(page.steps) and "IC-001" in text(page.result_view)
            assert len(page.result_view.findChildren(FigureCard)) == 1
            if screenshot:
                window.ai_action.setChecked(False)
                app.processEvents()
                assert window.grab().save(str(screenshot))
            page.work_tabs.setCurrentIndex(1)
            window.goto("事件")
            window.goto("预处理")
            assert page.work_tabs.currentIndex() == 1 and len(page.view.findChildren(FigureCard)) >= 2
            page.recs.setCurrentIndex(1)
            assert page.work_tabs.currentIndex() == 0
            assert not page.work_tabs.isTabEnabled(1) and not page.work_tabs.isTabEnabled(2)
            restored = pj.Project.load(project.folder)
            restored.pipeline.ica = False
            window.set_project(restored, quiet=True)
            window.goto("预处理", records[0].id)
            page.work_tabs.setCurrentIndex(2)
            wait()
            assert records[0].id in text(page.result_view) and "IC-001" in text(page.result_view)
            assert len(page.result_view.findChildren(FigureCard)) == 1
            page.process_all()
            wait()
            assert page.work_tabs.currentIndex() == 0 and "批量预处理" in text(page.batch_view)
            page.recs.setCurrentIndex(1)
            page.work_tabs.setCurrentIndex(2)
            wait()
            assert records[1].id in text(page.result_view) and "关闭 ICA" in text(page.result_view)
            assert "IC-001" not in text(page.result_view)
            error.assert_not_called()
        return "两条示例记录的流程、检查图、已存结果和批量汇总可切换，重开后还原实际 ICA 去除清单"
    finally:
        window.close()


def check_saved_controls(app, folder):
    from PySide6.QtCore import QSettings, Qt
    from . import compat
    from .core import events, project as pj
    from .ui.main_window import MainWindow

    project = pj.make_demo_project(str(Path(folder) / "控件保存"), n_subs=1, n_per_cond=2, log=lambda _: None)
    record = pj.add_raw_files(project, [compat.resource_path("sample_data", "eeglab_data.set")], subject="事件示例")[0]
    project.save_events(record, events.make([.5, 1], ["刺激甲", "刺激乙"]))
    project.save()
    manifest = Path(project.json_path)
    initial = manifest.read_bytes()

    def window_for(p):
        win = MainWindow(settings=QSettings(str(Path(folder) / "保存测试.ini"), QSettings.IniFormat))
        win.setAttribute(Qt.WA_DontShowOnScreen)
        win.set_project(p, quiet=True)
        return win

    win = window_for(project)
    try:
        for name in ("预处理", "事件", "ERP", "时频", "解码", "RSA", "ERP"):
            win.goto(name)
        assert manifest.read_bytes() == initial, "仅浏览页面改写了项目"
        erp = win.pages["ERP"]
        names = erp.conds.names()
        assert len(names) >= 2, names
        for i in range(erp.conds.count()):
            erp.conds.item(i).setCheckState(Qt.Checked if i == 1 else Qt.Unchecked)
        erp.ch.setText("Cz")
        erp.m0.setValue(.275)
        erp.alpha.setValue(.025)
        win.goto("解码")
        decoding = win.pages["解码"]
        for i in range(decoding.classes.count()):
            decoding.classes.item(i).setCheckState(Qt.Unchecked)
        win.goto("时频")
        win.pages["时频"].decim.setValue(3)
        win.goto("RSA")
        win.pages["RSA"].kind.setCurrentIndex(1)
        win.goto("事件", record.id)
        event_page = win.pages["事件"]
        for i in range(event_page.ev_list.count()):
            event_page.ev_list.item(i).setCheckState(Qt.Unchecked)
        event_page.tmax.setValue(.65)
        event_page.rej.setValue(200)
        win.goto("ERP")
        assert erp.conds.checked() == [names[1]] and erp.alpha.value() == .025
        saved = pj.Project.load(project.folder)
        assert saved.pipeline.events_explicit and not saved.pipeline.events and saved.pipeline.reject_uv == 200
        assert saved.assistant_data["analysis"]["ERP"]["通道"] == "Cz"
    finally:
        win.close()
    reopened = window_for(pj.Project.load(project.folder))
    try:
        reopened.goto("事件", record.id)
        ep = reopened.pages["事件"]
        assert ep.ev_list.checked() == [] and ep.ev_list.count() == 2 and ep.tmax.value() == .65
        try:
            pj.epoch_subject(reopened.project, reopened.project.get_rec(record.id)[0], log=lambda _: None)
        except ValueError as exc:
            assert "尚未勾选" in str(exc)
        else:
            raise AssertionError("空选择被用于全部事件")
        ep.ev_list.item(1).setCheckState(Qt.Checked)
        assert pj.Project.load(project.folder).pipeline.events == [ep.ev_list.names()[1]]
        reopened.goto("ERP")
        erp = reopened.pages["ERP"]
        assert (erp.ch.text(), erp.m0.value(), erp.alpha.value(), erp.conds.checked()) == ("Cz", .275, .025, [names[1]])
        reopened.goto("时频")
        assert reopened.pages["时频"].decim.value() == 3
        reopened.goto("解码")
        assert reopened.pages["解码"].classes.count() and reopened.pages["解码"].classes.checked() == []
        reopened.goto("RSA")
        assert reopened.pages["RSA"].kind.currentIndex() == 1
        return "手动编辑、明确空选和跨页切换可在新窗口恢复，仅浏览未改写项目清单"
    finally:
        reopened.close()


def _scenario(records):
    source, target, other = records
    snapshot = yield _window("inspect")
    pages = ("项目", "预处理", "事件", "ERP", "时频", "解码", "RSA", "报告")
    assert set(snapshot["可打开页面"]) == set(pages)
    for name in pages:
        snapshot = yield _window("open", name)
        assert snapshot["页面"] == name, snapshot
        if name == "事件":
            rows = next(c for c in snapshot["控件"] if c["操作"] == "select" and c.get("行"))
            snapshot = yield _window("select", rows["id"], items=["1"])
            assert any(c["名称"] == rows["名称"] and c["当前值"] == [1] for c in snapshot["控件"])
    for name, selected in (("ERP", "First"), ("时频", "Repetition")):
        snapshot = yield _window("open", name, value="数据")

        def conditions(view):
            return next(c for c in view["控件"] if set(c.get("选项", [])) == {"First", "Repetition"})

        field = conditions(snapshot)
        if name == "ERP":
            # The first preview is declined in the real confirmation window.
            refusal = yield _window("set", field["id"], items=[selected])
            assert "未采用" in refusal["状态"] and refusal["原值"] == field["当前值"], refusal
            snapshot = yield _window("inspect")
            assert conditions(snapshot)["当前值"] == field["当前值"]
            field = conditions(snapshot)
        snapshot = yield _window("set", field["id"], items=[selected])
        assert conditions(snapshot)["当前值"] == [selected]
        run = next(c for c in snapshot["控件"] if c["名称"].replace(" ", "") == f"运行{name}分析")
        receipt = yield _window("press", run["id"])
        assert receipt["状态"] == "已完成" and receipt["指标"] and receipt["图表"], receipt
    for rec in (target, other):
        preview = yield "preview_event_transfer", {"source": source.id, "target": rec.id}
        assert (preview["来源"], preview["目标"], preview["原有行数"], preview["候选行数"]) == (source.id, rec.id, 0, 2)


def run(app, folder):
    from PySide6.QtCore import QSettings, Qt, QTimer
    from PySide6.QtWidgets import QMessageBox
    from . import compat
    from .core import events, project as pj, workbench
    from .ui.main_window import MainWindow

    project = pj.make_demo_project(str(Path(folder) / "work-window"), n_subs=1, n_per_cond=1, log=lambda _: None)
    sample = compat.resource_path("sample_data", "eeglab_data.set")
    records = [pj.add_raw_files(project, [sample], subject=name)[0] for name in ("来源", "目标", "其他")]
    source, target, other = records
    source_table = events.make([0.5, 1.5], ["到达", "选择"])
    for r in records:
        project.save_events(r, source_table if r is source else events.empty())
    project.save()

    window = MainWindow(settings=QSettings(str(Path(folder) / "work-window.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.show()
    window.set_project(project, quiet=True)
    window.ai_action.setChecked(True)
    panel = window.assistant
    scenario = _scenario(records)
    calls = []
    finished = []
    previews = []

    def model(request, **_):
        payload = json.loads(request.data)
        last = payload["messages"][-1]
        result = json.loads(last["content"]) if last["role"] == "tool" else None
        assert not result or "error" not in result, (calls[-1:], result)
        message = {"role": "assistant", "content": "操作完成，请核对事件预览。"}
        try:
            name, args = scenario.send(result)
        except StopIteration:
            finished.append(True)
        else:
            assert name in {tool["function"]["name"] for tool in payload["tools"]}, name
            calls.append((name, args))
            message.update(content=None, tool_calls=[{"id": f"ui-{len(calls)}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}])
        return io.BytesIO(json.dumps({"choices": [{"message": message}]}).encode())

    def review():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            if dialog.windowTitle() != "助手操作预览":
                dialog.reject()
                return
            button = "保留原状" if not previews else "采用并继续"
            previews.append(dialog.text())
            next(b for b in dialog.buttons() if b.text() == button).click()

    timer = QTimer()
    timer.timeout.connect(review)
    timer.start(25)
    try:
        with patch.object(panel, "connection", return_value=("test", "DeepSeek", "test", "http://localhost/biap-test")), \
                patch.object(workbench, "urlopen", model):
            panel.edit.setPlainText("看看工作窗口，按我选的条件分析，再给我两份事件表的修改预览。")
            panel.submit()
            deadline = time.monotonic() + 90
            while not panel.send.isEnabled() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
        assert finished and panel.send.isEnabled(), (calls[-1:], panel.chat.toPlainText()[-800:])
        assert len(previews) == 3 and "ERP" in previews[0] and "时频" in previews[-1], previews
        assert [r.kind for _, r in window.reports] == ["ERP", "时频"]
        assert len(panel.proposals) == 2 and all(project.events(r).empty for r in (target, other))
        panel.pending.item(0).setCheckState(Qt.Checked)
        panel.pending.item(1).setCheckState(Qt.Unchecked)
        with patch.object(panel, "warn", side_effect=AssertionError):
            panel.accept()
        restored = pj.Project.load(project.folder)
        for rec in (source, target, other):
            actual = restored.events(restored.get_rec(rec.id)[1])
            if rec is other:
                assert actual.empty, rec.id
            else:
                assert actual.to_dict("records") == source_table.to_dict("records"), rec.id
        assert len(panel.proposals) == 1
        _dialog_checks(app, window, other, project)
        return "8 页可导航，ERP 与时频按确认的条件完成计算，跨记录事件只写入勾选的目标，助手填写弹窗并经确认写回"
    finally:
        timer.stop()
        window.close()


def check_form_edits(app, folder):
    from queue import Queue
    from PySide6.QtCore import Qt, QSettings, QTimer
    from PySide6.QtWidgets import QDialog, QFileDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout
    from . import compat
    from .core import project as pj
    from .ui.main_window import MainWindow
    from .ui.preprocess_page import ChannelDialog
    from .ui.project_page import FolderImportDialog
    from .ui.workspace_controls import WorkWindow

    project = pj.Project(str(Path(folder) / "助手表单测试"))
    rec = pj.add_raw_files(project, [compat.resource_path("sample_data", "eeglab_data.set")])[0]
    project.save()
    window = MainWindow(settings=QSettings(str(Path(folder) / "forms.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.show()
    window.set_project(project, quiet=True)
    window.goto("预处理", rec.id)
    window.ai_action.setChecked(True)
    page = window.pages["预处理"]
    work = WorkWindow(window.assistant)

    def show(dialog):
        dialog.setModal(True)
        dialog.show()
        app.processEvents()

    def find(**fields):
        return next(c for c in work.snapshot()["控件"] if all(c.get(k) == v for k, v in fields.items()))

    def perform(control, **args):
        q = Queue()
        work.perform(dict(action=control["操作"], target=control["id"], **args), q)
        app.processEvents()
        result = q.get_nowait()
        assert q.empty() and not isinstance(result, Exception), result
        return result

    try:
        row = dict(path=rec.source, subject="S1", condition="条件甲", use=True)
        dialog = FolderImportDialog(page, str(Path(rec.source).parent), [row])
        show(dialog)
        assert not any(c.get("列名") == "文件" for c in work.snapshot()["控件"])
        with patch.object(work, "approve", return_value=False):
            result = perform(find(行号=1, 列名="条件"), value="条件乙")
            assert "未采用" in result["状态"] and dialog.rows()[0]["condition"] == "条件甲"
        with patch.object(work, "approve", return_value=True):
            perform(find(行号=1, 列名="条件"), value="条件乙")
            perform(find(行号=1, 列名="导入"), value="否")
            assert dialog.rows() == [dict(row, condition="条件乙", use=False)]
            receipt = perform(find(按钮作用="AcceptRole"))
            assert "对话框" not in receipt["窗口"]

            channel = ChannelDialog(page, ["E1", "E2"], ["eeg", "eeg"], {}, [], 1)
            show(channel)
            perform(find(行号=2, 列名="通道"), value="false")
            assert channel.non_eeg() == ["E2"]
            assert not any(c["名称"].endswith("· 内容") for c in work.snapshot()["控件"])
            channel.reject()

            checklist = QDialog(page)
            layout = QVBoxLayout(checklist)
            choices = QListWidget()
            layout.addWidget(choices)
            item = QListWidgetItem("复核", choices)
            item.setCheckState(Qt.Unchecked)
            show(checklist)
            perform(find(名称="复核 · 勾选"), value="true")
            assert item.checkState() == Qt.Checked
            checklist.reject()

            original = list(project.pipeline.order)
            changed = original[1:] + original[:1]
            errors, close_receipt = [], Queue()

            def reorder():
                try:
                    control = next(c for c in work.snapshot()["控件"] if c["名称"].endswith("· 调整顺序"))
                    perform(control, items=[str(i) for i in range(2, len(original)+1)] + ["1"])
                    work.perform(dict(action="press", target=find(按钮作用="AcceptRole")["id"]), close_receipt)
                except Exception as exc:
                    errors.append(exc)
                    app.activeModalWidget().reject()
            QTimer.singleShot(0, reorder)
            page.edit_order()
            app.processEvents()
            if errors:
                raise errors[0]
            assert close_receipt.get_nowait()["状态"] == "已关闭对话框" and close_receipt.empty()
            assert pj.Project.load(project.folder).pipeline.order == changed

        secret = QDialog(page)
        secret_input = QLineEdit(secret)
        secret_input.setEchoMode(QLineEdit.Password)
        assert not work.can_fill(secret, window)
        assert not work.can_fill(QFileDialog(page), window)
        assert not work.can_fill(window.assistant.review, window)
        _file_choices(app, window, work, Path(project.folder))
        return "表格、通道、顺序保存和文件多选/目录/导出预览通过"
    finally:
        active = app.activeModalWidget()
        if isinstance(active, QDialog):
            active.reject()
        window.close()


def _file_choices(app, window, work, folder):
    from types import SimpleNamespace
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QFileDialog, QPushButton
    from matplotlib.figure import Figure
    from .ui.widgets import FigureCard

    page = window.stack.currentWidget()
    first, second, output = (folder / name for name in ("选择 甲.txt", "选择 乙.txt", "导出图.png"))
    first.write_text("甲", encoding="utf8")
    second.write_text("乙", encoding="utf8")
    output.write_bytes(b"existing")
    picked, previews = [], []
    read = QPushButton("测试多选", page)
    read.clicked.connect(lambda: picked.extend(QFileDialog.getOpenFileNames(page, "打开文本", str(folder))[0]))
    single = QPushButton("测试单选", page)
    single.clicked.connect(lambda: picked.append(QFileDialog.getOpenFileName(page, "打开当前记录", str(folder))[0]))
    directory = QPushButton("测试目录", page)
    directory.clicked.connect(lambda: picked.append(QFileDialog.getExistingDirectory(page, "选择导出位置", str(folder))))
    figure = Figure()
    figure.subplots().plot([1, 2])
    card = FigureCard("测试图", figure)
    card.setParent(page)
    for w in (single, read, directory, card):
        w.show()

    def choose(button, paths, direction, refuse=False):
        ends, problems = [], []
        def find(snapshot, label):
            return next(c for c in snapshot["控件"] if c["名称"] == label)
        class Reply:
            def __init__(self, callback):
                self.callback = callback
            def put(self, result):
                def next_action():
                    try:
                        if isinstance(result, Exception):
                            raise result
                        self.callback(result)
                    except Exception as exc:
                        problems.append(exc)
                        if app.activeModalWidget():
                            app.activeModalWidget().reject()
                QTimer.singleShot(0, next_action)
        def send(snapshot, label, reply, **args):
            c = find(snapshot, label)
            work.perform(dict(action=c["操作"], target=c["id"], **args), Reply(reply))
        def opened(result):
            shot = result["窗口"]
            assert shot["操作方向"] == direction and len(shot["控件"]) == 3
            try:
                send(shot, "选择路径", ends.append, value=str(folder.parent / "未引用的别处.txt"))
            except ValueError as exc:
                assert "用户给出" in str(exc)
            else:
                raise AssertionError("接受了未给出的文件路径")
            send(shot, "选择路径", lambda s: send(s, "采用路径", finish_or_retry), items=list(map(str, paths)))
        def finish_or_retry(result):
            if "未采用" in result.get("状态", ""):
                assert refuse and output.read_bytes() == b"existing"
                send(work.snapshot(), "采用路径", ends.append)
            else:
                ends.append(result)

        refusals = [False] if refuse else []
        def approve(location, title, contents):
            if title != direction:
                return True
            assert all(str(p) in contents for p in paths)
            previews.append(contents)
            return refusals.pop() if refusals else True

        timer = QTimer()
        timer.setSingleShot(True)
        def timeout():
            problems.append(AssertionError("文件操作未正常回包"))
            if app.activeModalWidget():
                app.activeModalWidget().reject()
        timer.timeout.connect(timeout)
        timer.start(12000)
        try:
            with patch.object(work, "approve", side_effect=approve):
                send(work.snapshot(), button, opened)
                deadline = time.monotonic() + 3
                while not ends and not problems and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
        finally:
            timer.stop()
        if problems:
            raise problems[0]
        assert len(ends) == 1 and ends[0]["文件选择"]["路径"] == list(map(str, paths)), ends

    question = "打开 " + "、".join(map(str, (first, second))) + "，保存到 " + str(output)
    native = app.testAttribute(Qt.AA_DontUseNativeDialogs)
    try:
        with patch.object(work.panel, "worker", SimpleNamespace(question=question)):
            recording = Path(work.project.recordings()[0][1].source)
            choose("测试单选", [recording], "读取文件")
            choose("测试多选", [first, second], "读取文件")
            choose("测试目录", [folder], "选择写入目录")
            choose("保存图片", [output], "写入文件", refuse=True)
        assert list(map(Path, picked)) == [recording, first, second, folder]
        assert "将覆盖已有文件" in previews[-1] and output.read_bytes().startswith(b"\x89PNG")
        assert app.testAttribute(Qt.AA_DontUseNativeDialogs) == native
    finally:
        for w in (card, directory, read, single):
            w.hide()
            w.deleteLater()


def _dialog_checks(app, window, rec, project):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QMessageBox
    from .ui.workspace_controls import WorkWindow

    window.goto("事件", rec.id)
    workspace = WorkWindow(window.assistant)
    replies = []
    class Reply:
        def __init__(self, receive):
            self.receive = receive
            self.called = False
            replies.append(self)

        def put(self, result):
            assert not self.called, "同一工具请求回包两次"
            self.called = True
            QTimer.singleShot(0, lambda: self.receive(result))

    initial = workspace.snapshot()
    view = []
    failures = []
    def drive():
        try:
            workspace.perform(dict(action="open", target="事件", value="添加事件"), Reply(opened))
        except Exception as exc:
            failures.append(exc)

    def opened(snapshot):
        try:
            control = next(c for c in snapshot["控件"] if c["名称"] == "按固定间隔生成…")
            workspace.perform(dict(action="press", target=control["id"]), Reply(lambda result: advance(result["窗口"])))
        except Exception as exc:
            failures.append(exc)

    def inputs():
        for title, val in (("从", "500ms"), ("到", "2.5 s"), ("每隔", "1 s"), ("事件名", "测试标记")):
            snapshot = yield
            control = next(c for c in snapshot["控件"] if c["名称"] == title)
            yield dict(action="set", target=control["id"], value=val)
        snapshot = yield
        assert project.events(rec).empty
        ok = next(c for c in snapshot["控件"] if c.get("按钮作用") == "AcceptRole")
        refusal = yield dict(action="press", target=ok["id"])
        assert "未采用" in refusal["状态"] and project.events(rec).empty
        snapshot = yield dict(action="inspect")
        ok = next(c for c in snapshot["控件"] if c.get("按钮作用") == "AcceptRole")
        receipt = yield dict(action="press", target=ok["id"])
        assert "对话框" not in receipt["窗口"] and project.events(rec).onset.tolist() == [.5, 1.5, 2.5]
        assert project.events(rec).description.tolist() == ["测试标记"] * 3
        view.append(True)

    steps = inputs()
    next(steps)
    def advance(snapshot):
        try:
            action = steps.send(snapshot)
            if action is None:
                action = steps.send(snapshot)
            workspace.perform(action, Reply(advance))
        except StopIteration:
            pass
        except Exception as exc:
            failures.append(exc)
            modal = app.activeModalWidget()
            if isinstance(modal, QDialog):
                modal.reject()

    confirmations = []
    def decision(location, title, change):
        assert title == "按固定间隔生成事件" and all(v in change for v in ("0.5", "2.5", "1.0", "测试标记")), change
        confirmations.append(change)
        return len(confirmations) == 2

    foreign = QMessageBox(window)
    foreign.setText("用户独立打开")
    assert not workspace.can_fill(foreign, window)
    assert "对话框" not in initial
    with patch.object(workspace, "approve", side_effect=decision):
        drive()
        deadline = time.monotonic() + 15
        while not view and not failures and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
    if failures:
        raise failures[0]
    assert view and len(confirmations) == 2 and all(r.called for r in replies), len(replies)
