"""Operate BIAP's registered MNE waveform and ICA editors on the Qt thread."""
from itertools import count
from math import isfinite

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtWidgets import QApplication


class ViewerControls:
    def __init__(self, host, project, confirm):
        self.host = host
        self.project = project
        self.confirm = confirm
        self.ids = {}
        self.sequence = count(1)

    def list(self):
        editors = self.host._mark_editors
        self.ids = {key: win for key, win in self.ids.items() if win in editors}
        for win in editors:
            if win not in self.ids.values() and hasattr(win, "mne"):
                self.ids[f"wave-{next(self.sequence)}"] = win
        return [{"id": key, "窗口": win.windowTitle(), "记录": editors[win]["record"]}
                for key, win in self.ids.items()]

    def window(self, target):
        win = self.ids.get(target)
        if (win not in self.host._mark_editors or self.host.project is not self.project
                or self.host.runner.busy or QApplication.activeModalWidget() is not None):
            raise ValueError("波形窗口已关闭、正在运行或有待确认的对话框，请重新读取工作窗口。")
        return win

    @staticmethod
    def mode(win):
        if win.mne.instance_type == "ica":
            return "ICA 成分"
        if win.windowTitle().endswith("关闭窗口后保存"):
            return "事件"
        return "坏导和坏段"

    def describe(self, win, offset=0):
        data = win.mne
        mode = self.mode(win)
        result = {"记录": self.host._mark_editors[win]["record"], "窗口": win.windowTitle(), "标记类型": mode,
                  "当前可见时间范围秒": [float(t) for t in data.viewbox.viewRange()[0]],
                  "首个通道偏移": int(data.ch_start), "通道总数": len(data.ch_order)}
        if mode != "事件":
            names = [str(tr.ch_name) for tr in data.traces]
            if mode == "ICA 成分":
                names = [name for name in names if name in data.ica._ica_names]
            result.update(可标记名称=names, 已标记=list(data.info["bads"]))
        if mode != "ICA 成分":
            raw = data.inst
            ann = raw.annotations
            rows = range(offset, min(len(ann), offset + 40))
            result.update(记录时长秒=float(raw.n_times / raw.info["sfreq"]), 标记总数=len(ann),
                          区间=[{"起点秒":float(ann.onset[i] - raw.first_time), "时长秒":float(ann.duration[i]),
                               "名称":str(ann.description[i])} for i in rows])
        return result

    def perform(self, args):
        target, action = str(args.get("target", "")), args["action"]
        win = self.window(target)
        record = self.host._mark_editors[win]["record"]
        mode = self.mode(win)
        if action == "viewer_seek":
            state = win.mne
            if "start" not in args and "offset" not in args:
                raise ValueError("请给出要查看的起点秒数或通道偏移。")
            start = float(args.get("start", state.t_start))
            offset = args.get("offset", int(state.ch_start))
            if not 0 <= start <= state.xmax or type(offset) is not int or not 0 <= offset < len(state.ch_order):
                raise ValueError("浏览位置超出波形范围。")
            if "offset" in args and (state.butterfly or state.fig_selection):
                raise ValueError("请先在波形窗口切换到按通道排列的视图。")
            if "start" in args:
                xmin = min(start, max(0, state.xmax - state.duration))
                state.plt.setXRange(xmin, xmin + state.duration, padding=0)
            if "offset" in args:
                win.vscroll(offset - int(state.ch_start))
            return self.describe(win)
        if action in ("viewer_inspect", "viewer_view"):
            if win.isMinimized():
                win.showNormal()
            win.raise_()
            if action == "viewer_inspect":
                return self.describe(win, max(0, int(args.get("offset", 0))))
            image = win.grab()
            if image.width() > 1440:
                image = image.scaledToWidth(1440, Qt.SmoothTransformation)
            data = QByteArray()
            buffer = QBuffer(data)
            if not buffer.open(QIODevice.WriteOnly) or not image.save(buffer, "PNG"):
                raise ValueError("无法读取当前波形窗口的图像。")
            return bytes(data)

        label = str(args.get("value", "")).strip()
        if action == "viewer_mark":
            marked = args.get("marked")
            if mode == "事件" or label not in self.describe(win).get("可标记名称", []) or type(marked) is not bool:
                raise ValueError("请按 viewer_inspect 列出的可标记名称及布尔值操作。")
            verb = ("排除" if marked else "保留") if mode == "ICA 成分" else ("标记为坏导" if marked else "取消坏导标记")
            if (label in win.mne.info["bads"]) == marked:
                return {"状态": "此图窗已有该标记状态", "图窗": self.describe(win)}
            change = f"{verb} {label}。关闭波形窗口后保存到项目。"
        elif action == "viewer_interval":
            if mode == "ICA 成分":
                raise ValueError("ICA 波形窗口支持按成分名标记。")
            raw = win.mne.inst
            start, end = float(args["start"]), float(args["end"])
            if not (isfinite(start) and isfinite(end) and 0 <= start < end <= raw.n_times / raw.info["sfreq"]):
                raise ValueError("起止时间必须是记录范围内的秒数，且终点大于起点。")
            if not label or len(label) > 256:
                raise ValueError("请给出 1 至 256 个字的标记名称。")
            if mode == "坏导和坏段" and not label.startswith("BAD_qc_"):
                label = "BAD_qc_" + label
            change = f"添加{mode if mode == '事件' else '坏段'}「{label}」，从 {start:g} 秒到 {end:g} 秒。关闭波形窗口后保存。"
        elif action == "viewer_close":
            change = "关闭此波形窗口，并保存图中当前的标记。"
        else:
            raise ValueError("未知的波形窗口操作。")

        if not self.confirm(f"{record} · {mode}\n{change}"):
            return {"状态":"用户未采用，图窗保持原状", "记录":record}
        self.window(target)
        if action == "viewer_close":
            commit = self.host._mark_editors[win]
            if not win.close() or not commit["saved"]:
                raise ValueError("波形窗口未能确认保存，请检查 BIAP 的提示。")
            return {"状态":"窗口已关闭，标记已提交", "记录":record}
        if action == "viewer_mark":
            if (label in win.mne.info["bads"]) != marked:
                trace = next((tr for tr in win.mne.traces if tr.ch_name == label), None)
                if trace is None:
                    raise ValueError("当前可见通道已变化，请重新读取波形窗口。")
                # Channel-axis mouse clicks use the same trace method.
                trace.toggle_bad()
                if (label in win.mne.info["bads"]) != marked:
                    raise ValueError("波形窗口未确认标记。")
        else:
            self.add_interval(win, start, end, label)
        return {"状态":"已在图中标记，关闭该窗口时保存", "图窗":self.describe(win)}

    @staticmethod
    def add_interval(win, start, end, label):
        data = win.mne
        raw = data.inst
        onset, duration = raw.first_time + start, end - start
        ann = raw.annotations
        if any(abs(a-onset) < 1e-9 and abs(d-duration) < 1e-9 and name == label
               for a, d, name in zip(ann.onset, ann.duration, ann.description)):
            return
        if not data.annotation_mode:
            win._toggle_annotation_fig()
        dock = data.fig_annotation
        if dock.description_cmbx.findText(label) < 0:
            dock._add_description(label)
        else:
            dock.description_cmbx.setCurrentText(label)
        # Use the exact sample-relative times. Pixel coordinates would quantize
        # both edges of a drag, especially on a zoomed-out recording.
        region = win._add_region(start, duration, label)
        try:
            ann.append(onset, duration, label)
        except Exception:
            win._remove_region(region, from_annot=False)
            raise
        if not (data.t_start <= start < data.t_start + data.duration):
            xmin = min(start, max(0, data.xmax - data.duration))
            data.plt.setXRange(xmin, xmin + data.duration, padding=0)
        win._update_regions_visible()
