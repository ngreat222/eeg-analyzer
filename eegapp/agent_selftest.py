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
    calls = [
        ("list_project", {}),
        ("inspect_recording_quality", {"target": record.id}),
        ("inspect_project", {"kind":"step", "target":"陷波"}),
        ("edit_step", {"target":"陷波","params":'{"频率Hz":50}',"title":"将陷波改为 50 Hz"}),
        ("inspect_project", {"kind":"step", "target":"高低通"}),
        ("edit_step", {"target":"高低通","params":'{"低通Hz":32}',"title":"调整低通"}),
    ]
    responses = []
    def scripted(request, **kwargs):
        body = json.loads(request.data)
        assert "list_project" in [t["function"]["name"] for t in body["tools"]]
        msgs = body["messages"]
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
        return "质量检测、选择性写回、撤销、五项参数磁盘复核、助手打开分段和滤波参数页"
    finally:
        window.close()
