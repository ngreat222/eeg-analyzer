"""Exercise the actual chat worker, EEG tool and preview/apply UI with scripted model replies."""
import io
import json
import os
import time
from copy import copy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


def _check_transport(workbench):
    from ssl import SSLCertVerificationError, SSLEOFError
    from urllib.error import HTTPError, URLError

    calls, shown, progress = [], [], []
    def model(request, **kwargs):
        calls.append(request.data)
        if len(calls) == 1:
            message = {"role":"assistant", "content":None, "tool_calls":[
                {"id":"show", "type":"function", "function":{"name":"show_parameters", "arguments":'{"target":"ERP"}'}}]}
        elif len(calls) == 2:
            raise URLError(SSLEOFError("UNEXPECTED_EOF_WHILE_READING"))
        else:
            message = {"role":"assistant", "content":"已显示。"}
        return io.BytesIO(json.dumps({"choices":[{"message":message}]}).encode())

    def show(target):
        shown.append(target)
        return {"已打开":target}

    state = {"project":"test", "tables":{}, "flow":[], "show_parameters":show}
    with patch.object(workbench, "urlopen", model):
        answer, _, trace = workbench.discuss(state, [], [], "打开 ERP 参数", "", progress=progress.append)
    assert answer == "已显示。" and shown == ["ERP"] and len(trace) == 1
    assert len(calls) == 3 and calls[1] == calls[2]
    assert sum(event["phase"] == "retry" for event in progress) == 1

    # A local model can truncate a structured answer after a real action.
    # Preserve the original receipt and never replay that action.
    inspect = {"action":"inspect"}
    results = iter(['{"action":"run_record_workflow","analysis":"ERP"}',
                    json.dumps({"action":"work_window", "arguments":inspect}), '{"action":'])
    opened, inspected = [], []
    def local_model(request, **kwargs):
        schema = json.loads(request.data)["response_format"]["json_schema"]["schema"]
        if opened:
            branch = next(b for b in schema["oneOf"] if b["properties"]["action"]["const"] == "work_window")
            assert branch["properties"]["arguments"]["type"] == "object" and "arguments" in branch["required"]
        return io.BytesIO(json.dumps({"choices":[{"message":{"role":"assistant","content":next(results)}}]}).encode())

    def window_call(op, analysis):
        opened.append((op, analysis))
        return {"流程":[], "记录":"样例", "下一步":"预处理"}

    def inspect_window(args):
        inspected.append(args)
        return {"页面":"预处理", "当前记录":"样例"}

    with patch.object(workbench, "urlopen", local_model), TestCase().assertRaises(workbench.ModelRequestError) as caught:
        local_state = dict(state, workflow_call=window_call, work_window=inspect_window, window_context={"页面":"预处理"})
        workbench.discuss(local_state, [], [], "请把这条记录从预处理做到 ERP 并导出报告。",
                          "", "自定义兼容服务", "test", "http://127.0.0.1:18974/v1")
    assert opened == [("begin", "ERP")] and inspected == [inspect]
    assert [entry[0] for entry in caught.exception.trace] == ["run_record_workflow", "work_window"]

    pages = {"事件":"事件与分段", "项目":"项目总览"}
    assert workbench.requested_page("帮我打开事件与分段页面", pages) == "事件"
    assert workbench.requested_page("打开项目总览并导入文件", pages) is None
    def wrong_page(request, **kwargs):
        fields = json.loads(request.data)["response_format"]["json_schema"]["schema"]["properties"]
        assert fields["target"]["enum"] == ["事件"]
        msg = {"role":"assistant","content":'{"action":"open","target":"项目"}'}
        return io.BytesIO(json.dumps({"choices":[{"message":msg}]}).encode())

    with patch.object(workbench, "urlopen", wrong_page), TestCase().assertRaisesRegex(workbench.ModelRequestError, "没有识别"):
        workbench.discuss(dict(state, work_window=lambda _: None, window_context={"可打开页面":pages}),
                          [], [], "帮我打开事件与分段页面", "", "自定义兼容服务", "test", "http://localhost/v1")

    for error, expected in (
        (URLError(SSLCertVerificationError("certificate verify failed")), "证书"),
        (HTTPError("https://example.invalid", 401, "Unauthorized", {}, None), "401"),
    ):
        with patch.object(workbench, "urlopen", side_effect=error) as send:
            try:
                workbench.discuss(state, [], [], "测试", "")
            except workbench.ModelRequestError as exc:
                assert expected in str(exc) and send.call_count == 1
            else:
                raise AssertionError("无效证书或认证失败应中止请求")

    retry = []
    def cancel_on_retry(event):
        if event["phase"] == "retry":
            retry.append(event)
    with patch.object(workbench, "urlopen", side_effect=ConnectionResetError()) as send:
        try:
            workbench.discuss(state, [], [], "测试", "", progress=cancel_on_retry, cancelled=lambda: bool(retry))
        except workbench.ModelRequestError as exc:
            assert "已停止" in str(exc) and send.call_count == 1
        else:
            raise AssertionError("停止重试后仍发出了模型请求")


def _check_event_entry(window, folder, sample):
    from PySide6.QtCore import Qt
    from .core import events, project as pj, workbench
    from .ui import project_bridge as bridge

    old_project = window.project
    project = pj.Project(str(Path(folder) / "event-entry"))
    target, source = [pj.add_raw_files(project, [sample], subject=n)[0] for n in ("目标", "来源")]
    original = events.make([.25], ["原有"])
    original["实验标签"] = "既有"
    project.save_events(target, original)
    project.save_events(source, events.make([1000], ["超出目标记录"]))
    project.save()
    window.set_project(project, quiet=True)
    state = {"project":project.name, "local_records":{r.id:(s,r) for s,r in project.recordings()},
             "read_events":lambda rid: bridge.event_table(project, project.get_rec(rid)[1])}
    def preview(rows, mode="append"):
        return workbench.run_tool(state, [], "preview_record_events", {"target":target.id,"mode":mode,"rows":rows})
    try:
        before = state["read_events"](target.id)
        manifest = Path(project.json_path).read_bytes()
        rows = [{"事件":"看图","时间":"128 samples","时长":"250毫秒"},
                {"description":"按键", "onset":"01:02.5"}]
        panel = window.assistant
        _, replace = preview(rows, "replace")
        panel.add_proposals([replace])
        panel.pending.setCurrentRow(0)
        panel.dismiss()
        msg, append = preview(rows)
        assert msg["候选行数"] == 3 and len(replace["after"]["rows"]) == 2
        assert state["read_events"](target.id) == before and Path(project.json_path).read_bytes() == manifest
        assert append["after"]["rows"] == [[.25, 0., "原有", "既有"], [1., .25, "看图", ""], [62.5, 0., "按键", ""]]
        for invalid in ({"name":"a","onset":"一点左右"}, {"name":"a","onset":-1},
                        {"name":"a","onset":"238s","duration":"1s"}, {"name":"a","onset":1,"时间":2}):
            try:
                preview([invalid])
            except ValueError:
                pass
            else:
                raise AssertionError(f"无效事件未被拒绝：{invalid}")
        try:
            workbench.run_tool(state, [], "preview_event_transfer", {"source":source.id,"target":target.id})
        except ValueError as exc:
            assert "超出目标记录范围" in str(exc)
        else:
            raise AssertionError("允许迁移超出目标记录时长的事件")
        panel.add_proposals([append])
        panel.pending.item(0).setCheckState(Qt.Checked)
        with patch.object(panel, "warn", side_effect=AssertionError):
            panel.accept()
        saved = pj.Project.load(project.folder)
        assert bridge.event_table(saved, saved.get_rec(target.id)[1]) == append["after"]
    finally:
        window.set_project(old_project, quiet=True)


def ica_review(app, folder):
    from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QDialog, QLabel
    from . import compat
    from .core import project as pj, workbench
    from .ui.main_window import MainWindow

    folder = Path(folder)
    project = pj.Project(str(folder / "ica-review"))
    sample = compat.resource_path("sample_data", "eeglab_data.set")
    target, other = [pj.add_raw_files(project, [sample], subject=n)[0] for n in ("目标", "其他")]
    subject = project.get_rec(target.id)[0]
    subject.locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
    subject.rename_from_locs = True
    project.pipeline = pj.Pipeline(ica_n="3", ica_auto_eog=False, notch=0)
    project.save()
    window = MainWindow(settings=QSettings(str(folder / "ica-review.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)

    def wait_until(done):
        deadline = time.monotonic() + 90
        while not done() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert done(), "等待 ICA 界面回执超时"

    def disk_records():
        data = json.loads(Path(project.json_path).read_bytes())
        return {r["id"]: r for s in data["subjects"] for r in s["recordings"]}

    try:
        window.show()
        window.set_project(project, quiet=True)
        window.goto("预处理", target.id)
        page = window.pages["预处理"]
        page.fit_ica()
        wait_until(lambda: not window.runner.busy)
        assert page.check_fit(page.fit_token)[1] is target and page.ica.n_components_ == 3
        with patch("eegapp.ui.preprocess_page.QInputDialog.getText", return_value=("0, 1", True)):
            page.discuss_ica()
        wait_until(lambda: not window.runner.busy)
        panel = window.assistant
        names = sorted(panel.files, key=lambda name: panel.files[name]["ica"]["component"])
        assert [panel.files[n]["ica"]["component"] for n in names] == [0, 1]
        assert not target.ica_manual and not page.marked_components()
        def confirm_empty():
            dialog = app.activeModalWidget()
            commit = dialog.findChild(QAbstractButton, "saveIcaMarks")
            if commit and commit.isEnabled():
                commit.click()
            else:
                dialog.reject()
        QTimer.singleShot(0, confirm_empty)
        panel.show_images()
        assert disk_records()[target.id]["ica_manual"] and disk_records()[target.id]["ica_exclude"] == []
        original = Path(project.json_path).read_bytes()
        original_other = disk_records()[other.id]
        turns = []

        def model(request, **_):
            body = json.loads(request.data)
            turns.append(len(turns) + 1)
            message = {"role": "assistant", "content": "请核对 IC-000 和 IC-001 的波形与地形。"}
            if len(turns) == 1:
                parts = body["messages"][-1]["content"]
                assert sum(p["type"] == "image_url" for p in parts) == 2
                assert "review_ica_components" in {t["function"]["name"] for t in body["tools"]}
            elif len(turns) == 2:
                assert body["tool_choice"]["function"]["name"] == "review_ica_components"
                assert body["messages"][-2]["content"] == message["content"]
                changes = [dict(target=n, exclude=True, reason=f"复核波形与头皮图的测试建议 {i}")
                           for i, n in enumerate(names)]
                message.update(content=None, tool_calls=[{"id":"review-marks", "type":"function",
                    "function":{"name":"review_ica_components", "arguments":json.dumps({"changes":changes})}}])
            else:
                assert len(turns) == 3
                assert json.loads(body["messages"][-1]["content"])["候选成分"] == [0, 1]
                message["content"] = "两项建议已送至预览。"
            return io.BytesIO(json.dumps({"choices":[{"message":message}]}).encode())

        with patch.object(panel, "connection", return_value=("test", "DeepSeek", "vision-test", "http://localhost/test")), \
                patch.object(workbench, "urlopen", model):
            panel.edit.setPlainText("看看这两张成分图，把排除建议放进预览。")
            panel.send.click()
            wait_until(panel.send.isEnabled)
        assert turns == [1, 2, 3] and [p["component"] for p in panel.proposals] == [0, 1], panel.chat.toPlainText()
        assert Path(project.json_path).read_bytes() == original
        panel.open_review()
        panel.pending.item(1).setCheckState(Qt.Checked)
        with patch.object(panel, "warn", side_effect=AssertionError):
            panel.accept()
        saved = disk_records()
        assert saved[target.id]["ica_exclude"] == [1] and saved[target.id]["ica_manual"]
        assert saved[target.id]["reviewed_ica"] == pj.ica_identity(page.ica) and saved[other.id] == original_other
        assert [p["component"] for p in panel.proposals] == [0] and page.marked_components() == {1}
        assert "已重新读取 project.json" in panel.history[-1]["content"] and "IC-001" in panel.history[-1]["content"]

        def review_image(index, *, save=False, click_image=True, stale=False):
            errors, visited = [], []
            def edit_dialog():
                dialog = app.activeModalWidget()
                try:
                    assert isinstance(dialog, QDialog)
                    picker = dialog.findChild(QComboBox)
                    picker.setCurrentText(names[index])
                    mark = dialog.findChild(QAbstractButton, "icaExclude")
                    image = dialog.findChild(QLabel, "icaImage")
                    commit = dialog.findChild(QAbstractButton, "saveIcaMarks")
                    assert mark and image and commit and image.pixmap()
                    if stale:
                        assert not mark.isEnabled() and not commit.isEnabled()
                        QTest.mouseClick(image, Qt.LeftButton)
                        assert not commit.isEnabled()
                        visited.append(True)
                        return
                    initial = mark.isChecked()
                    assert initial == (index in page.marked_components())
                    if click_image:
                        QTest.mouseClick(image, Qt.LeftButton)
                    else:
                        QTest.mouseClick(mark, Qt.LeftButton, pos=QPoint(8, mark.height() // 2))
                    assert mark.isChecked() != initial and disk_records()[target.id]["ica_exclude"] == sorted(page.marked_components())
                    if save:
                        commit.click()
                    visited.append(True)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    if isinstance(dialog, QDialog) and dialog.isVisible():
                        dialog.reject()
            QTimer.singleShot(0, edit_dialog)
            panel.image_button.click()
            if errors:
                raise errors[0]
            assert visited

        before_cancel = Path(project.json_path).read_bytes()
        review_image(0, save=False, click_image=True)
        assert Path(project.json_path).read_bytes() == before_cancel and page.marked_components() == {1}
        review_image(0, save=True, click_image=False)
        assert disk_records()[target.id]["ica_exclude"] == [0, 1] and page.marked_components() == {0, 1}
        review_image(1, save=True, click_image=True)
        assert disk_records()[target.id]["ica_exclude"] == [0] and page.marked_components() == {0}
        assert disk_records()[other.id] == original_other
        window.ai_action.setChecked(False)
        window.goto("事件", target.id)
        window.goto("预处理", target.id)
        assert page.excl.text() == "0" and page.ica.exclude == [0]
        assert any("已标记排除 IC-000" in l.text() for l in page.steps.findChildren(QLabel))
        window.ai_action.setChecked(True)
        review_image(0, save=True)
        clean_marks = disk_records()[target.id]
        assert (clean_marks["ica_manual"] and clean_marks["ica_exclude"] == []
                and clean_marks["reviewed_ica"] == pj.ica_identity(page.ica) and page.excl.text() == "无")
        assert any("已确认保留所有成分" in l.text() for l in page.steps.findChildren(QLabel))
        window.goto("预处理", other.id)
        before_stale = Path(project.json_path).read_bytes()
        review_image(0, stale=True)
        panel.pending.item(0).setCheckState(Qt.Checked)
        with patch.object(panel, "warn") as warn:
            panel.accept()
        assert warn.call_count == 1 and "失效" in str(warn.call_args.args[0])
        assert Path(project.json_path).read_bytes() == before_stale and disk_records()[other.id] == original_other
        window.close()
        window = MainWindow(settings=QSettings(str(folder / "ica-review.ini"), QSettings.IniFormat))
        window.setAttribute(Qt.WA_DontShowOnScreen)
        window.show()
        restored = pj.Project.load(project.folder)
        window.set_project(restored, quiet=True)
        window.goto("预处理", target.id)
        page = window.pages["预处理"]
        assert page.excl.text() == "无" and restored.get_rec(target.id)[1].ica_manual
        assert any("已确认保留所有成分" in l.text() for l in page.steps.findChildren(QLabel))
        page.fit_ica()
        wait_until(lambda: not window.runner.busy)
        assert page.marked_components() == set() and page.fit_identity == clean_marks["reviewed_ica"]
        page.process_one()
        wait_until(lambda: not window.runner.busy)
        processed = disk_records()[target.id]
        assert processed["processed_ica"] == {"mode":"manual", "exclude":[]} and processed["status"] == pj.REC_DONE
        assert disk_records()[other.id] == original_other
    finally:
        window.close()
    return "真实成分图送入脚本模型，文字建议转为两项预览并选择性写回；图片点击与勾选支持保存或取消，另一条记录完好"


def tfr_parameter_recovery(app, folder):
    """Recover from a negative baseline on fixed epochs through the visible chat workflow."""
    from PySide6.QtCore import QSettings, QTimer, Qt
    from PySide6.QtWidgets import QDialogButtonBox, QListWidget
    from . import compat
    from .core import project as pj, workbench
    from .ui import project_bridge as bridge
    from .ui.main_window import MainWindow

    project = pj.Project(str(Path(folder) / "时频参数修正"))
    rec = pj.add_raw_files(project, [compat.resource_path("sample_data", "eeglab_data.set")])[0]
    project.subjects[0].locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
    project.subjects[0].rename_from_locs = True
    other = pj.add_raw_files(project, [rec.source], subject="另一条记录")[0]
    project.pipeline.ica = False
    project.pipeline.reject_uv = 0
    project.pipeline.epoch_mode = pj.EPOCH_FIXED
    project.pipeline.fixed_len = 2
    project.assistant_data["analysis"] = {"时频":{"do_stat":False}}
    project.save()
    window = MainWindow(settings=QSettings(str(Path(folder) / "tfr-recovery.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.show()
    window.set_project(project, quiet=True)
    window.goto("时频")
    page = window.pages["时频"]
    page.f1.setValue(20)
    page.decim.setValue(1)
    window.goto("预处理", rec.id)
    window.ai_action.setChecked(True)
    panel = window.assistant
    panel.provider = "DeepSeek"

    offered, receipts, previews, errors = [], [], [], []
    completed_files = {}
    def edit(params):
        return "edit_step", {"target":"时频", "params":json.dumps(params, ensure_ascii=False), "title":"复核时频设置"}

    advance = ("continue_record_workflow", {})
    inspect = ("work_window", {"action":"inspect"})
    sequence = [("run_record_workflow", {"analysis":"时频"}), edit({"最高频率Hz":18}),
                advance, advance, advance, ("stop_record_workflow", {"reason":"先核对分段的时间范围"}),
                inspect, ("work_window", {"action":"open", "target":"时频", "value":"频率与时间"}),
                edit({"未知基线参数":[0, .2]}), edit({"baseline":[0, .2], "win":[0, 1.75]}),
                inspect, ("work_window", {"action":"press"}), advance]

    def model(request, **_):
        body = json.loads(request.data)
        available = {spec["function"]["name"] for spec in body["tools"]}
        i = len(offered)
        if i and body["messages"][-1]["role"] == "tool":
            receipts.append(json.loads(body["messages"][-1]["content"]))
        if i == 5:
            assert "时间段 -0.4–-0.1 s 超出数据范围（0.00–1.99 s）" in receipts[-1]["error"], receipts[-1]
            for rel in (rec.clean_file, project.subjects[0].epochs_file):
                path = Path(project.path(rel))
                completed_files[path] = path.stat().st_mtime_ns
        if i == 6:
            assert body["messages"][-1]["role"] == "user" and "待解决的错误" in body["messages"][-1]["content"]
            assert "continue_record_workflow" not in available
        if i in (2, 6, 7, 10):
            window_schema = next(spec["function"]["parameters"] for spec in body["tools"]
                                 if spec["function"]["name"] == "work_window")
            assert {"inspect", "open", "view", "set", "press", "select"} <= set(window_schema["properties"]["action"]["enum"])
        if i in (7, 8, 11):
            result = receipts[-1]
            assert "error" not in result, result
            visible = result.get("窗口", result)
            assert visible["项目"] == project.name
            if i == 7:
                assert visible["页面"] == "预处理" and visible["当前记录"] == rec.id
            else:
                assert visible["页面"] == "时频"
                baseline_fields = [c for c in visible["控件"] if c["操作"] == "set" and "基线" in c["名称"]]
                expected = [-.4, -.1] if i == 8 else [0., .2]
                assert sorted(c["当前值"] for c in baseline_fields) == expected, baseline_fields
        if i in (7, 8, 9):
            assert "continue_record_workflow" not in available, "只读操作不可清除尚未解决的分析错误"
        if i == 9:
            assert "未知基线参数" in receipts[-1]["error"], receipts[-1]
            params = receipts[-1]["可调步骤参数"]["时频"]
            assert params["基线起点秒"] == -.4 and params["显示起点秒"] == -.2
            assert params["条件"] == receipts[3]["事件条件"] == ["片段"]
        name, args = sequence[i]
        if i == 11:
            button = next(c for c in visible["控件"] if c["操作"] == "press" and c["名称"] == "运行时频分析")
            args = dict(args, target=button["id"])
        if i == 12:
            assert receipts[-1]["下一步"] == "报告" and receipts[-1]["记录"] == rec.id and receipts[-1]["图表"]
        assert name in available, (i, name, available, receipts[-1:])
        offered.append(name)
        message = {"role":"assistant", "content":None, "tool_calls":[{"id":f"tfr-{i}", "type":"function",
                   "function":{"name":name, "arguments":json.dumps(args)}}]}
        return io.BytesIO(json.dumps({"choices":[{"message":message}]}).encode())

    def decide():
        dialog = app.activeModalWidget()
        if not dialog or dialog.windowTitle() != "运行已暂停 · 预览参数修改":
            return
        try:
            items = dialog.findChild(QListWidget)
            assert items.count() == 1
            item = items.item(0)
            text = item.text()
            assert page.f1.value() == 20 and page.b0.value() == -.4 and page.w0.value() == -.2
            assert item.checkState() == Qt.Unchecked
            if not previews:
                assert "最高频率Hz" in text
                action = "保留原值并继续"
            else:
                assert len(previews) == 1 and "基线起点秒" in text and "显示终点秒" in text
                item.setCheckState(Qt.Checked)
                action = "采用勾选项并继续"
            previews.append(action)
            buttons = dialog.findChild(QDialogButtonBox).buttons()
            next(b for b in buttons if b.text() == action).click()
        except Exception as exc:
            errors.append(exc)
            dialog.reject()

    timer = QTimer()
    timer.timeout.connect(decide)
    timer.start(25)
    def send(question):
        panel.edit.setPlainText(question)
        panel.send.click()
        deadline = time.monotonic() + 120
        while not panel.send.isEnabled() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert panel.send.isEnabled(), "等待时频工作流超时"

    try:
        with patch.object(panel, "connection", return_value=("test", "DeepSeek", "test", "http://localhost/biap-test")), \
                patch.object(workbench, "urlopen", model):
            send("按当前记录从预处理做到时频，检查每一步并导出报告。")
            previous = panel.previous_workflow
            assert previous and previous.index == 2 and previous.failed_step and len(offered) == 6, panel.chat.toPlainText()
            window.goto("预处理", other.id)
            send("继续")
            assert len(offered) == 6 and "选中这条记录" in panel.chat.toPlainText() and not other.clean_file
            window.goto("预处理", rec.id)
            send("继续")
            assert panel.previous_workflow is previous, panel.chat.toPlainText()
        assert not errors, errors
        assert offered == [a for a, _ in sequence], (offered, panel.chat.toPlainText(), panel.trace[-2:])
        assert previews == ["保留原值并继续", "采用勾选项并继续"]
        restored = pj.Project.load(project.folder)
        saved = restored.assistant_data["analysis"]["时频"]
        parameters = bridge.saved_parameters(restored.json_path, "时频")["时频"]
        assert parameters["基线起点秒"] == page.b0.value() == 0 and parameters["基线终点秒"] == page.b1.value() == .2
        assert parameters["显示起点秒"] == page.w0.value() == 0 and parameters["显示终点秒"] == page.w1.value() == 1.75
        assert parameters["最高频率Hz"] == page.f1.value() == 20 and "未知基线参数" not in saved
        _, result = window.pages["报告"].displayed_items[-1]
        assert result.kind == "时频" and result.params["基线"] == "0.0–0.2 s（dB）" and result.params["条件"] == "片段"
        assert result.arrays["freqs"][-1] == 20 and tuple(result.arrays["times"][[0,-1]]) == (0, 1.75)
        assert len(completed_files) == 2 and all(path.stat().st_mtime_ns == t for path, t in completed_files.items())
        report = Path(panel.trace[-1][1]["已导出"])
        html = report.read_text(encoding="utf-8")
        assert "0.0–0.2 s（dB）" in html and rec.id in html and any((report.parent / "图").glob("*.png"))
        return "流程暂停时助手能读取当前记录、打开时频参数页，采用修正后可读回基线；沿用已完成分段并导出真实时频图及 HTML"
    finally:
        timer.stop()
        window.close()


def run(app, folder):
    from PySide6.QtCore import QSettings, Qt
    from . import compat
    from .core import project as pj, workbench
    from .ui.main_window import MainWindow
    from .ui.record_workflow import RecordWorkflow

    _check_transport(workbench)

    project = pj.Project(str(Path(folder) / "agent-project"))
    record = pj.add_raw_files(project, [compat.resource_path("sample_data", "eeglab_data.set")])[0]
    project.subjects[0].locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
    project.subjects[0].rename_from_locs = True
    project.pipeline.notch = 60.0
    project.save()
    window = MainWindow(settings=QSettings(os.path.join(folder, "agent.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.resize(1350, 800)
    window.show()
    window.set_project(project, quiet=True)
    window.ai_action.setChecked(True)
    _check_event_entry(window, folder, record.source)
    panel = window.assistant
    model = "自定义兼容服务"
    panel.provider = model
    panel.keys[(model, "http://localhost/biap-test")] = ""
    record_table = f"记录摘要 · {record.id}"
    sent_samples = []
    calls = [
        ("list_project", {}),
        ("inspect_recording_quality", {"target": record.id}),
        ("inspect_project", {"kind":"step", "target":"陷波"}),
        ("edit_step", {"target":"陷波","params":'{"频率Hz":50}',"title":"将陷波改为 50 Hz"}),
        ("inspect_project", {"kind":"step", "target":"高低通"}),
        ("edit_step", {"target":"高低通","params":'{"低通Hz":32}',"title":"调整低通"}),
        ("query_table", {"target": record_table,
                         "sql": "SELECT typeof(值), 值 FROM data WHERE 项目 = '采样点'"}),
    ]
    responses = []
    def scripted(request, **kwargs):
        body = json.loads(request.data)
        assert "list_project" in [t["function"]["name"] for t in body["tools"]]
        msgs = body["messages"]
        if not sent_samples and msgs[-1]["role"] == "user":
            prompt = msgs[-1]["content"]
            attachment = json.loads(prompt.split("\n\n随消息附上的内容：", 1)[1])
            sent_samples.append(dict(attachment["tables"][record_table]["sample"])["采样点"])
        if msgs[-1]["role"] == "tool":
            result = json.loads(msgs[-1]["content"])
            assert "error" not in result, result
            responses.append(result)
        i = len(responses)
        msg = {"role":"assistant","content":"检查完成，请预览修改。"}
        if i < len(calls):
            name, args = calls[i]
            msg.update(content=None, tool_calls=[{"id":f"test-{i}","type":"function",
                "function":{"name":name,"arguments":json.dumps(args)}}])
        return io.BytesIO(json.dumps({"choices":[{"message":msg}]}).encode())

    try:
        before = Path(project.json_path).read_bytes()
        panel.attach(("project", project.folder, "record", record.id))
        assert ("table", record_table) in panel.refs
        with patch.object(panel, "connection", return_value=("", model, "test", "http://localhost/biap-test")), \
                patch.object(workbench, "urlopen", scripted):
            panel.edit.setPlainText("检查记录质量，并给我滤波修改预览。")
            panel.send.click()
            deadline = time.monotonic() + 90
            while not panel.send.isEnabled() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            assert panel.send.isEnabled(), "助手没有完成请求"
        assert len(responses) == len(calls), panel.chat.toPlainText()
        assert sent_samples == [30504] and type(sent_samples[0]) is int
        assert responses[-1]["rows"] == [["integer", 30504]], responses[-1]
        assert record.id in responses[0]["连续记录"]
        assert responses[1]["参与检测的EEG通道数"] > 0
        assert len(panel.worker.activity.entries) == len(calls)
        assert len(panel.proposals) == 2 and Path(project.json_path).read_bytes() == before
        previous_lowpass = project.pipeline.h_freq
        panel.pending.item(0).setCheckState(Qt.Checked)
        panel.open_review()
        with patch.object(panel, "warn", side_effect=AssertionError):
            panel.accept()
        restored = pj.Project.load(project.folder)
        assert restored.pipeline.notch == 50 and restored.pipeline.h_freq == previous_lowpass
        assert panel.pending.count() == 1
        with patch.object(panel, "warn", side_effect=AssertionError):
            panel.revert()
        assert pj.Project.load(project.folder).pipeline.notch == 60
        changes = {"陷波": {"去除谐波": True}, "高低通": {"高通Hz": 1.0},
                   "重采样": {"目标采样率Hz": 250.0}, "重参考": {"方式": "平均参考"},
                   "事件分段": {"剔除阈值µV": 200.0}}
        panel.current_snapshot()
        selected = []
        for name, params in changes.items():
            _, prop = workbench.run_tool(panel.state, [("step", name)], "edit_step",
                                         dict(target=name, params=params, title=name))
            selected.append(prop)
        panel.add_proposals(selected)
        for i in range(1, panel.pending.count()):
            panel.pending.item(i).setCheckState(Qt.Checked)
        with patch.object(panel, "warn", side_effect=AssertionError):
            panel.accept()
        receipt = panel.history[-1]["content"]
        assert "已重新读取 project.json" in receipt and "不一致" not in receipt and "200" in receipt
        responses.clear()
        calls[:] = [("verify_saved_parameters", {"target": ""}),
                    ("show_parameters", {"target": "事件分段"}), ("show_parameters", {"target": "重采样"})]
        with patch.object(panel, "connection", return_value=("", model, "test", "http://localhost/biap-test")), \
                patch.object(workbench, "urlopen", scripted):
            panel.edit.setPlainText("核对保存结果，带我看看分段和重采样参数。")
            panel.send.click()
            deadline = time.monotonic() + 40
            while not panel.send.isEnabled() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
        assert len(responses) == 3, panel.chat.toPlainText()
        for name, params in changes.items():
            for label, value in params.items():
                assert responses[0]["已保存参数"][name][label] == value
        assert responses[1]["界面参数"]["剔除阈值µV"] == 200
        assert responses[2]["界面参数"]["目标采样率Hz"] == 250
        assert window.stack.currentWidget() is window.pages["预处理"]
        assert window.pages["预处理"].group_picker.currentText() == "滤波"
        calls[:] = [("run_record_workflow", {"analysis":"ERP"}), ("continue_record_workflow", {}),
                    ("stop_record_workflow", {"reason":"等待用户确认后续步骤"})] + [("continue_record_workflow", {})] * 3
        responses.clear()
        def native_workflow(request, **kwargs):
            body = json.loads(request.data)
            name, args = calls[len(responses)]
            assert name in [t["function"]["name"] for t in body["tools"]], body["messages"][-1]
            responses.append(name)
            message = {"role":"assistant", "content":None, "tool_calls":[{
                "id":f"run-{len(responses)}","type":"function","function":{"name":name,"arguments":json.dumps(args)}}]}
            return io.BytesIO(json.dumps({"choices":[{"message":message}]}).encode())
        with patch.object(panel, "connection", return_value=("test", "DeepSeek", "test", "http://localhost/biap-test")), \
                patch.object(workbench, "urlopen", native_workflow):
            panel.edit.setPlainText("把当前记录从预处理做到 ERP 分析并导出报告。")
            panel.send.click()
            deadline = time.monotonic() + 180
            while not panel.send.isEnabled() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            previous = panel.previous_workflow
            assert previous is not None and previous.index == 1 and len(previous.items) == 1, responses
            cancelled = copy(previous)
            cancelled.cancel()
            with TestCase().assertRaisesRegex(ValueError, "已取消"):
                cancelled.continuation()
            with patch.object(project.pipeline, "notch", 37.0), TestCase().assertRaisesRegex(ValueError, "参数已变化"):
                previous.continuation()
            with patch("eegapp.ui.record_workflow.current_record", return_value="其他记录"), TestCase().assertRaisesRegex(ValueError, "选中这条记录"):
                previous.continuation()
            clean_path = Path(project.path(project.get_rec(record.id)[1].clean_file))
            written = clean_path.stat().st_mtime_ns
            for index, stage, reason in [(0, "预处理", "尚未确认通道"), (1, "事件", "当前记录尚无事件")]:
                failed = copy(previous)
                failed.index = index
                failed.items = previous.items[:index]
                failed.completed_inputs = failed.input_state()
                failed.failed_step = reason
                failed.failed_inputs = failed.input_state(include_next=True)
                with patch.object(failed, "advance", side_effect=AssertionError("续跑准备阶段不能重新计算")):
                    point = failed.continuation()
                assert point["记录"] == record.id and point["待解决的错误"] == reason
                assert point["下一步"] == stage and point["已完成"] == list(failed.stages[:index])
                assert len(failed.items) == index and clean_path.stat().st_mtime_ns == written
            other = copy(record)
            other.id = record.id + "-video"
            rows = project.recordings() + [(project.subjects[0], other)]
            with patch.object(project, "recordings", return_value=rows), patch.object(panel, "refs", [("table", "事件表 · 另一条")]), \
                    patch.object(panel, "event_refs", {"事件表 · 另一条":other.id}), \
                    patch("eegapp.ui.record_workflow.current_record", return_value=record.id):
                with patch("eegapp.ui.record_workflow.QInputDialog.getItem", return_value=(other.id, True)) as choose:
                    assert RecordWorkflow(panel, record.id, "请处理这条记录").choose_record() == other.id
                    assert choose.call_count == 1 and set(choose.call_args.args[3]) == {record.id, other.id}
                    assert RecordWorkflow(panel, record.id, "参考 " + other.id + " 的事件表，处理这条记录").choose_record() == other.id
                    assert choose.call_count == 2
                with patch("eegapp.ui.record_workflow.QInputDialog.getItem", return_value=("", False)), \
                        TestCase().assertRaisesRegex(ValueError, "流程尚未启动"):
                    RecordWorkflow(panel, record.id, "请处理这条记录").begin("ERP")
                with patch("eegapp.ui.record_workflow.QInputDialog.getItem", side_effect=AssertionError):
                    assert RecordWorkflow(panel, record.id, "处理引用的记录").choose_record() == other.id
                    assert RecordWorkflow(panel, record.id, "处理工作窗口选中的记录").choose_record() == record.id
                    assert RecordWorkflow(panel, record.id, "请处理 " + record.id).choose_record() == record.id
                    assert RecordWorkflow(panel, record.id, "请处理 " + other.id).choose_record() == other.id
            panel.edit.setPlainText("继续吧")
            panel.send.click()
            while not panel.send.isEnabled() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            assert clean_path.stat().st_mtime_ns == written and panel.previous_workflow is previous
        assert responses == [name for name, _ in calls], (panel.chat.toPlainText(), panel.trace[-2:])
        assert window.stack.currentWidget() is window.pages["报告"], panel.chat.toPlainText()
        report = Path(panel.trace[-1][1]["已导出"])
        assert report.stat().st_size > 0, panel.chat.toPlainText()
        summary = "断线重连与取消、记录冲突选择、过期续跑检查、跨轮从分段继续、质量检测、选择性写回、撤销、参数磁盘复核、单记录 ERP 和 HTML 导出"
        endpoint = os.environ.get("BIAP_TEST_MODEL_URL")
        if endpoint:
            model_id = os.environ["BIAP_TEST_MODEL_ID"]
            panel.prefs = QSettings(os.path.join(folder, "live-model.ini"), QSettings.IniFormat)
            panel.prefs.setValue(model + "/url", endpoint)
            panel.prefs.setValue(model + "/model", model_id)
            panel.keys[(model, endpoint)] = os.environ.get("BIAP_TEST_MODEL_KEY", "")
            panel.reset_chat()
            panel.edit.setPlainText("请按当前参数把这条记录从预处理做到 ERP 分析并导出报告。")
            panel.send.click()
            deadline = time.monotonic() + 600
            while not panel.send.isEnabled() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.015)
            assert panel.send.isEnabled() and panel.trace, panel.chat.toPlainText()
            live_report = Path(panel.trace[-1][1]["已导出"])
            assert live_report != report and live_report.stat().st_size > 0
            assert list((live_report.parent / "图").glob("*.png"))
            summary += f"；真实模型 {model_id} 通过 {len(panel.trace)} 次操作另行导出报告"
        return summary
    finally:
        window.close()


def waveform_assistant(app, folder):
    from queue import Queue
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QMessageBox
    from . import compat
    from .core import project as pj
    from .ui.main_window import MainWindow
    from .ui.workspace_controls import WorkWindow

    root = Path(folder)
    project = pj.Project(str(root / "assistant-waveform"))
    rec = pj.add_raw_files(project, [compat.resource_path("sample_data", "eeglab_data.set")], subject="wave")[0]
    subject, _ = project.get_rec(rec.id)
    subject.locs = compat.resource_path("sample_data", "eeglab_chan32.locs")
    subject.rename_from_locs = True
    project.pipeline = pj.Pipeline(ica_n="6", ica_auto_eog=False, notch=0)
    project.save()
    window = MainWindow(settings=QSettings(str(root / "assistant-wave.ini"), QSettings.IniFormat))
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.show()
    window.set_project(project, quiet=True)
    window.goto("预处理", rec.id)
    window.ai_action.setChecked(True)
    page = window.pages["预处理"]
    workspace = WorkWindow(window.assistant)
    target = ""

    def operate(action, **params):
        reply = Queue()
        workspace.perform({"action":action, "target":target, **params}, reply)
        return reply.get_nowait()

    def window_id():
        app.processEvents()
        windows = operate("inspect")["子窗口"]
        assert len(windows) == 1 and windows[0]["记录"] == rec.id
        return windows[0]["id"]

    def reopen():
        return pj.Project.load(project.folder)

    try:
        with patch.object(QMessageBox, "information"), patch.object(QMessageBox, "warning") as warning:
            page.fit_ica()
            deadline = time.monotonic() + 120
            while window.runner.busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            assert page.ica is not None and not window.runner.busy
            page.browse_components()
            target = window_id()
            component = operate("viewer_inspect")["可标记名称"][1]
            assert operate("viewer_view").startswith(b"\x89PNG")
            with patch.object(workspace.viewers, "confirm", return_value=False):
                assert "用户未采用" in operate("viewer_mark", value=component, marked=True)["状态"]
            assert not operate("viewer_inspect")["已标记"]
            with patch.object(workspace.viewers, "confirm", return_value=True):
                operate("viewer_mark", value=component, marked=True)
                operate("viewer_close")
                assert reopen().get_rec(rec.id)[1].ica_exclude == [1]
                with TestCase().assertRaises(ValueError):
                    operate("viewer_inspect")
                page.browse()
                target = window_id()
                # Scroll through a genuine Qt channel viewport, then mark a visible name.
                browser = window._windows[-1]
                browser.change_nchan(step=8 - browser.mne.n_channels)
                shown = operate("viewer_seek", start=12.0, offset=8)
                assert shown["首个通道偏移"] == 8 and abs(shown["当前可见时间范围秒"][0] - 12) < 1e-5
                channel = shown["可标记名称"][0]
                operate("viewer_mark", value=channel, marked=True)
                for _ in range(2):
                    operate("viewer_interval", start=1.25, end=1.85, value="复核")
                operate("viewer_close")
                disk = reopen().get_rec(rec.id)[1]
                assert channel in disk.bads and disk.bad_segments == [[1.25, 1.85, "复核"]]
                window.goto("事件", rec.id)
                events = window.pages["事件"]
                with patch.object(events, "run", side_effect=lambda title, read, show:show(read())):
                    events.mark_on_wave()
                target = window_id()
                assert operate("viewer_inspect")["标记类型"] == "事件"
                operate("viewer_interval", start=2.375, end=2.875, value="助手事件")
                operate("viewer_close")
                saved = reopen()
                table = saved.events(saved.get_rec(rec.id)[1])
                rows = table.loc[table.description == "助手事件"]
                assert list(rows.onset) == [2.375] and list(rows.duration) == [.5]
                assert not warning.called, warning.call_args_list
        return "实际波形截图、滚动、排除 ICA、坏导、坏段、事件均经工作窗口操作；拒绝保持原状，保存后重新载入核对"
    finally:
        for browser in list(window._windows):
            browser.close()
        window.close()
