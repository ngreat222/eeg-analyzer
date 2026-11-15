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
        return "零拖入、读取项目、示例 EEG 质量检测、6 次工具调用、选中写回、撤销"
    finally:
        window.close()
