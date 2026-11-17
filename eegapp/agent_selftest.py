"""Exercise the actual chat worker, EEG tool and preview/apply UI with scripted model replies."""
import io
import json
import os
import time
from pathlib import Path
from unittest.mock import patch


def run(app, folder):
    from PySide6.QtCore import QSettings, Qt
    from . import compat
    from .core import project as pj, workbench
    from .ui.main_window import MainWindow

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
        calls[:] = [("run_record_workflow", {"analysis":"ERP"})] + [("continue_record_workflow", {})] * 4
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
        assert responses == [name for name, _ in calls], (panel.chat.toPlainText(), panel.trace[-2:])
        assert window.stack.currentWidget() is window.pages["报告"], panel.chat.toPlainText()
        report = Path(panel.trace[-1][1]["已导出"])
        assert report.stat().st_size > 0, panel.chat.toPlainText()
        summary = "质量检测、选择性写回、撤销、参数磁盘复核、可见的预处理→分段→单记录 ERP→HTML 导出"
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
