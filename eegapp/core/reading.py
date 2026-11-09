"""读取原始脑电文件。逻辑来自「脑电数据读取/EEG_Handbook_原版代码.ipynb」第一个单元。"""
import re
from pathlib import Path

import mne
import numpy as np

# 文件对话框里列出的格式（MNE 的 read_raw 能自动识别）
RAW_FILE_FILTER = (
    "脑电文件 (*.edf *.bdf *.gdf *.vhdr *.set *.cnt *.fif *.fif.gz *.mff *.eeg *.vmrk "
    "*.data *.nxe *.lay *.dat *.snirf *.xdf *.mef *.edf+ *.bdf+);;所有文件 (*)"
)


def parse_event_map(text):
    """把 “1=rt, 2=square” 这样的文本转成 {"1": "rt", "2": "square"}。"""
    mapping = {}
    for part in re.split(r"[,，;；\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        if "=" not in part and ":" not in part and "：" not in part:
            raise ValueError(f"事件码对照写法不对：“{part}”，应写成 码=名称，例如 1=rt")
        code, name = re.split(r"[=:：]", part, maxsplit=1)
        mapping[code.strip()] = name.strip()
    return mapping


def unify_event_name(desc, event_map):
    """BrainVision 的 "Comment/square"、"Stimulus/S  2" → "square"、"2"，再按对照表换成事件名。"""
    name = desc.split("/")[-1].strip()
    name = re.sub(r"^[SR]\s*(\d+)$", r"\1", name)
    return event_map.get(name, name)


def read_raw_any(path, event_map=None, log=print):
    """读取任意 MNE 支持格式的连续脑电，返回已载入内存的 Raw。"""
    event_map = event_map or {}
    p = Path(str(path).strip().strip('"')).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"找不到数据：{p}")
    if p.suffix.lower() in (".eeg", ".vmrk") and p.with_suffix(".vhdr").exists():
        p = p.with_suffix(".vhdr")

    raw = None
    # 国内设备导出的 EDF/BDF 常把中文注释存成 GBK，utf8 解不开时依次换编码重试
    for enc in ("utf8", "gbk", "latin1"):
        try:
            kwargs = {} if enc == "utf8" else {"encoding": enc}
            raw = mne.io.read_raw(p, preload=False, **kwargs)
            break
        except TypeError:
            # 该格式不接受 encoding 参数
            if enc == "utf8":
                raise
            raw = mne.io.read_raw(p, preload=False)
            break
        except Exception as e:  # noqa: BLE001
            if "encoding" not in str(e).lower() and "decode" not in str(e).lower() or enc == "latin1":
                raise
            log(f"注释用 {enc} 解码失败，换下一种编码重试")

    raw.load_data()

    # 没有 annotations、但带触发通道（如 BDF 的 Status）时，从触发通道提取事件
    if len(raw.annotations) == 0 and "stim" in raw.get_channel_types():
        try:
            events = mne.find_events(raw, shortest_event=1, verbose=False)
            if len(events):
                raw.set_annotations(mne.annotations_from_events(
                    events, raw.info["sfreq"], orig_time=raw.info["meas_date"]))
                log(f"从触发通道提取到 {len(events)} 个事件")
        except Exception as e:  # noqa: BLE001
            log(f"触发通道提取事件失败：{e}")
        stim = [ch for ch, t in zip(raw.ch_names, raw.get_channel_types()) if t == "stim"]
        if stim:
            raw.drop_channels(stim)

    if len(raw.annotations):
        raw.annotations.rename(
            {d: unify_event_name(d, event_map) for d in set(raw.annotations.description)})
    return raw, p


def describe_raw(raw, path=None):
    """读取后显示的摘要文字（HTML）。"""
    types = raw.get_channel_types()
    type_count = dict(zip(*np.unique(types, return_counts=True))) if types else {}
    lines = []
    if path is not None:
        lines.append(f"<b>文件</b>：{path}")
    lines.append(f"<b>通道数</b>：{len(raw.ch_names)}　"
                 + "　".join(f"{k}: {v}" for k, v in type_count.items()))
    lines.append(f"<b>采样率</b>：{raw.info['sfreq']:g} Hz　<b>时长</b>：{raw.times[-1]:.1f} s"
                 f"　<b>采样点</b>：{raw.n_times}")
    hp, lp = raw.info["highpass"], raw.info["lowpass"]
    lines.append(f"<b>已有滤波</b>：{hp:g}–{lp:g} Hz")
    lines.append(f"<b>电极位置</b>：{'已设置' if raw.get_montage() is not None else '未设置'}")
    lines.append(f"<b>坏导</b>：{', '.join(raw.info['bads']) or '无'}")
    if len(raw.annotations):
        d, c = np.unique(raw.annotations.description, return_counts=True)
        ev = "，".join(f"{a}×{b}" for a, b in zip(d, c))
        lines.append(f"<b>事件</b>：{ev}")
    else:
        lines.append("<b>事件</b>：无")
    names = ", ".join(raw.ch_names[:80]) + (" …" if len(raw.ch_names) > 80 else "")
    lines.append(f"<b>通道名</b>：{names}")
    return "<br>".join(lines)
