"""项目：一组被试 + 一套预处理设置。所有分析都从项目里已预处理的分段数据（Epochs）出发。

项目保存在一个文件夹里：
    project.json            被试列表、预处理设置
    epochs/<被试>-epo.fif   每个被试预处理后的分段数据
"""
import json
import os
import re
from dataclasses import asdict, dataclass, field

import mne
import numpy as np

from . import reading, single

STATUS_RAW = "待预处理"
STATUS_DONE = "已预处理"


@dataclass
class Pipeline:
    event_map: str = ""
    montage: str = "自动识别"          # 自动识别 / 标准模板名 / 不设置
    reref: str = "不改变"              # 不改变 / 平均参考 / 指定通道
    reref_channels: str = ""
    notch: float = 50.0               # 0 = 不做
    harmonics: bool = False
    l_freq: float = 0.1               # 0 = 不做
    h_freq: float = 30.0              # 0 = 不做
    resample: float = 0.0             # 0 = 不改变
    interpolate: bool = True
    ica: bool = True
    ica_n: str = ""                   # 空 = 全部；整数或 0–1 小数
    ica_method: str = "fastica"
    ica_auto_eog: bool = True
    events: list = field(default_factory=list)   # 空 = 全部事件
    tmin: float = -0.2
    tmax: float = 0.8
    baseline: bool = True
    bl0: float = -0.2
    bl1: float = 0.0
    reject_uv: float = 150.0          # 0 = 不剔除


@dataclass
class Subject:
    name: str
    source: str = ""                  # 原始文件路径（导入的分段数据为空）
    status: str = STATUS_RAW
    epochs_file: str = ""
    locs: str = ""                    # 该被试专用的电极位置文件（可选）
    rename_from_locs: bool = False
    bads: list = field(default_factory=list)
    ica_exclude: list = field(default_factory=list)
    ica_manual: bool = False          # True = 排除成分由用户手动指定
    n_trials: dict = field(default_factory=dict)
    note: str = ""


class Project:
    def __init__(self, folder, name=None):
        self.folder = os.path.abspath(folder)
        self.name = name or os.path.basename(self.folder)
        self.subjects = []
        self.pipeline = Pipeline()
        self._cache = {}

    # ------------------------------------------------------------ 保存与读取
    @property
    def json_path(self):
        return os.path.join(self.folder, "project.json")

    @property
    def epochs_dir(self):
        return os.path.join(self.folder, "epochs")

    def save(self):
        os.makedirs(self.epochs_dir, exist_ok=True)
        data = dict(name=self.name, pipeline=asdict(self.pipeline),
                    subjects=[asdict(s) for s in self.subjects], version=2)
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, folder):
        p = cls(folder)
        with open(p.json_path, encoding="utf-8") as f:
            data = json.load(f)
        p.name = data.get("name", p.name)
        p.pipeline = Pipeline(**{k: v for k, v in data.get("pipeline", {}).items()
                                 if k in Pipeline.__dataclass_fields__})
        p.subjects = [Subject(**{k: v for k, v in s.items() if k in Subject.__dataclass_fields__})
                      for s in data.get("subjects", [])]
        return p

    # ------------------------------------------------------------ 被试
    def unique_name(self, base):
        base = re.sub(r"[\\/:*?\"<>|\s]+", "_", base).strip("_") or "sub"
        names = {s.name for s in self.subjects}
        if base not in names:
            return base
        i = 2
        while f"{base}_{i}" in names:
            i += 1
        return f"{base}_{i}"

    def get(self, name):
        for s in self.subjects:
            if s.name == name:
                return s
        raise KeyError(name)

    def remove(self, name):
        self.subjects = [s for s in self.subjects if s.name != name]
        self._cache.pop(name, None)

    def done_subjects(self):
        return [s for s in self.subjects if s.status == STATUS_DONE and s.epochs_file]

    def epochs(self, name):
        """读取（并缓存）一个被试的分段数据。"""
        if name not in self._cache:
            s = self.get(name)
            path = s.epochs_file if os.path.isabs(s.epochs_file) else os.path.join(self.folder, s.epochs_file)
            self._cache[name] = mne.read_epochs(path, preload=True, verbose=False)
        return self._cache[name]

    def store_epochs(self, subj, epochs):
        os.makedirs(self.epochs_dir, exist_ok=True)
        rel = os.path.join("epochs", f"{subj.name}-epo.fif")
        epochs.save(os.path.join(self.folder, rel), overwrite=True, verbose=False)
        subj.epochs_file = rel
        subj.status = STATUS_DONE
        subj.n_trials = {k: int(len(epochs[k])) for k in epochs.event_id}
        self._cache[subj.name] = epochs

    def conditions(self):
        """所有已预处理被试共有的条件。"""
        sets = [set(self.epochs(s.name).event_id) for s in self.done_subjects()]
        if not sets:
            return []
        common = set.intersection(*sets)
        first = list(self.epochs(self.done_subjects()[0].name).event_id)
        return [c for c in first if c in common]

    def label_columns(self):
        """可用于解码 / RSA 的标签：条件本身，加上分段数据 metadata 里的列。"""
        cols = ["条件"]
        done = self.done_subjects()
        if done:
            md = self.epochs(done[0].name).metadata
            if md is not None:
                cols += [c for c in md.columns if md[c].nunique() >= 2]
        return cols

    def summary(self):
        done = self.done_subjects()
        info = dict(n=len(self.subjects), done=len(done), sfreq=None, n_ch=None, conds=[])
        if done:
            ep = self.epochs(done[0].name)
            info.update(sfreq=ep.info["sfreq"], n_ch=len(mne.pick_types(ep.info, eeg=True)),
                        conds=self.conditions(), tmin=ep.tmin, tmax=ep.tmax)
        return info


# ====================================================================== 导入
def add_raw_files(project, paths):
    added = []
    for p in paths:
        stem = os.path.basename(p).split(".")[0]
        s = Subject(name=project.unique_name(stem), source=os.path.normpath(p))
        project.subjects.append(s)
        added.append(s)
    return added


def add_epochs_files(project, paths, log=print):
    added = []
    for p in paths:
        ep = mne.read_epochs(p, preload=True, verbose=False)
        stem = re.sub(r"[-_]epo$", "", os.path.basename(p).split(".")[0])
        s = Subject(name=project.unique_name(stem), source="", note=f"导入：{p}")
        project.subjects.append(s)
        project.store_epochs(s, ep)
        added.append(s)
        log(f"导入分段数据 {s.name}：{len(ep)} 个试次")
    return added


def _make_info(n_ch, sfreq, ch_names):
    names = list(ch_names) if ch_names else []
    if names and len(names) != n_ch:
        raise ValueError(f"填写了 {len(names)} 个通道名，但数据有 {n_ch} 个通道")
    if not names:
        names = [f"EEG{i + 1:03d}" for i in range(n_ch)]
    info = mne.create_info(names, sfreq, "eeg")
    try:
        std = mne.channels.make_standard_montage("standard_1005")
        low = {c.lower() for c in std.ch_names}
        if sum(n.lower() in low for n in names) >= 0.6 * n_ch:
            info.set_montage(std, on_missing="ignore", match_case=False)
    except Exception:  # noqa: BLE001
        pass
    return info


def _epochs_array(data_uv, info, labels, tmin, label_names=None, metadata=None):
    """data_uv: [试次, 通道, 时间]（µV）；labels: 每个试次的条件名。"""
    conds = list(dict.fromkeys(labels))
    event_id = {c: i + 1 for i, c in enumerate(conds)}
    n = data_uv.shape[0]
    events = np.c_[np.arange(n) * (data_uv.shape[2] + 10), np.zeros(n, int), [event_id[x] for x in labels]]
    return mne.EpochsArray(data_uv * 1e-6, info, events=events, event_id=event_id, tmin=tmin,
                           metadata=metadata, baseline=None, verbose=False)


def import_arrays_by_condition(project, conditions, dim_order, sfreq, tmin, ch_names=None, log=print):
    """手册第 3 章格式：每个条件一组文件（每个被试一个），被试按列表顺序对应。

    conditions: [(条件名, [文件...], 变量名)]
    """
    from .arrays import load_condition
    n_sub = {len(f) for _, f, _ in conditions}
    if len(n_sub) != 1:
        raise ValueError("各条件的文件数量不同，被试无法一一对应")
    added = []
    for i in range(n_sub.pop()):
        parts, labels, meta = [], [], None
        for name, files, key in conditions:
            d, m = load_condition(files[i], key, dim_order)
            meta = meta or m
            parts.append(d)
            labels += [name] * d.shape[0]
        data = np.concatenate(parts)
        sf, t0 = (meta["sfreq"], meta["tmin"]) if meta else (sfreq, tmin)
        info = _make_info(data.shape[1], sf, (meta or {}).get("ch_names") or ch_names)
        ep = _epochs_array(data, info, labels, t0)
        stem = os.path.basename(conditions[0][1][i]).split(".")[0]
        stem = re.sub(r"_(first|rep|" + "|".join(re.escape(c[0]) for c in conditions) + r")$", "", stem,
                      flags=re.I)
        s = Subject(name=project.unique_name(stem), note="导入数组（条件分文件）")
        project.subjects.append(s)
        project.store_epochs(s, ep)
        added.append(s)
        log(f"导入 {s.name}：{len(ep)} 个试次")
    return added


def import_arrays_with_labels(project, files, key, dim_order, label_sets, sfreq, tmin, crop=None,
                              ch_names=None, log=print):
    """手册第 4 章格式：每个被试一个数据文件，配一个或多个标签文件。

    label_sets: [(标签名, [每个被试的标签文件], 列号从 0 起)]；第一组标签同时作为“条件”。
    """
    import pandas as pd

    from .arrays import load_condition, load_labels
    if not label_sets:
        raise ValueError("请至少添加一组标签文件")
    for lname, lfiles, _ in label_sets:
        if len(lfiles) != len(files):
            raise ValueError(f"标签“{lname}”有 {len(lfiles)} 个文件，数据有 {len(files)} 个，数量要一致")
    added = []
    for i, f in enumerate(files):
        d, m = load_condition(f, key, dim_order)
        sf, t0 = (m["sfreq"], m["tmin"]) if m else (sfreq, tmin)
        cols = {}
        for lname, lfiles, col in label_sets:
            lab = load_labels(lfiles[i], col)
            if len(lab) != d.shape[0]:
                raise ValueError(f"{os.path.basename(f)}：数据 {d.shape[0]} 个试次，"
                                 f"标签“{lname}”有 {len(lab)} 个")
            cols[lname] = [_fmt_label(v) for v in lab]
        if crop:
            times = t0 + np.arange(d.shape[2]) / sf
            idx = np.where((times >= crop[0] - 1e-9) & (times <= crop[1] + 1e-9))[0]
            d = d[:, :, idx]
            t0 = times[idx[0]]
        info = _make_info(d.shape[1], sf, (m or {}).get("ch_names") or ch_names)
        first = label_sets[0][0]
        ep = _epochs_array(d, info, cols[first], t0, metadata=pd.DataFrame(cols))
        stem = os.path.basename(f).split(".")[0]
        s = Subject(name=project.unique_name(stem), note="导入数组（带标签）")
        project.subjects.append(s)
        project.store_epochs(s, ep)
        added.append(s)
        log(f"导入 {s.name}：{len(ep)} 个试次，标签 {', '.join(cols)}")
    return added


def _fmt_label(v):
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except (TypeError, ValueError):
        return str(v)


# ====================================================================== 预处理
def _auto_types(raw):
    changed = {}
    for ch, t in zip(raw.ch_names, raw.get_channel_types()):
        up = ch.upper()
        for key, typ in (("EOG", "eog"), ("ECG", "ecg"), ("EKG", "ecg"), ("EMG", "emg")):
            if key in up and t != typ:
                changed[ch] = typ
    if changed:
        raw.set_channel_types(changed, on_unit_change="ignore")
    return changed


def _auto_montage(raw, log):
    std = mne.channels.make_standard_montage("standard_1005")
    low = {c.lower() for c in std.ch_names}
    eeg = [c for c, t in zip(raw.ch_names, raw.get_channel_types()) if t == "eeg"]
    hit = sum(c.lower() in low for c in eeg)
    if eeg and hit >= 0.6 * len(eeg):
        raw.set_montage(std, on_missing="ignore", match_case=False)
        log(f"自动匹配标准 10-05 电极位置：{hit}/{len(eeg)} 个通道")
        return True
    log("通道名不是标准 10-20 名称，未设置电极位置（地形图、插值需要电极位置）")
    return False


def load_raw(subj, pipe, log=print):
    """读取原始数据并完成通道设置（电极、类型、重参考）。"""
    raw, _ = reading.read_raw_any(subj.source, reading.parse_event_map(pipe.event_map), log=log)
    if subj.locs:
        single.apply_channel_setup(raw, "file", subj.locs, subj.rename_from_locs, None, log=log)
    ch = _auto_types(raw)
    if ch:
        log(f"按通道名设置类型：{ch}")
    if not subj.locs:
        if pipe.montage == "自动识别":
            _auto_montage(raw, log)
        elif pipe.montage != "不设置":
            raw.set_montage(mne.channels.make_standard_montage(pipe.montage), on_missing="warn",
                            match_case=False)
    raw.info["bads"] = [b for b in subj.bads if b in raw.ch_names]
    return raw


def filter_raw(raw, pipe, log=print):
    single.apply_filters(raw, single.none_if_zero(pipe.notch), pipe.harmonics,
                         single.none_if_zero(pipe.l_freq), single.none_if_zero(pipe.h_freq),
                         "fir", single.none_if_zero(pipe.resample), log=log)
    if pipe.interpolate and raw.info["bads"] and raw.get_montage() is not None:
        single.interpolate_bads(raw, log=log)
    if pipe.reref == "平均参考":
        raw.set_eeg_reference("average", projection=False, verbose=False)
        log("平均参考")
    elif pipe.reref == "指定通道" and pipe.reref_channels.strip():
        raw.set_eeg_reference(single.parse_names(pipe.reref_channels), projection=False, verbose=False)
        log(f"参考：{pipe.reref_channels}")
    return raw


def ica_n(pipe):
    t = (pipe.ica_n or "").strip()
    if not t:
        return None
    return float(t) if "." in t else int(t)


def fit_ica(raw, pipe, log=print):
    return single.fit_ica(raw, ica_n(pipe), pipe.ica_method, 1.0, 97, log=log)


def auto_exclude(ica, raw, log=print):
    try:
        return single.find_eog_components(ica, raw, log=log)
    except ValueError as e:
        log(f"未自动识别眼电成分：{e}")
        return []


def make_epochs(raw, pipe, log=print):
    bl = (pipe.bl0, pipe.bl1) if pipe.baseline else None
    names = pipe.events or [n for n, _ in single.event_table(raw)]
    return single.make_epochs(raw, names, pipe.tmin, pipe.tmax, bl, single.none_if_zero(pipe.reject_uv),
                              None, True, log=log)


def run_pipeline(project, subj, log=print, raw=None, ica=None):
    """完整预处理一个被试并保存分段数据。raw/ica 可传入界面里已经检查过的对象。"""
    pipe = project.pipeline
    if raw is None:
        raw = load_raw(subj, pipe, log=log)
        filter_raw(raw, pipe, log=log)
    if pipe.ica:
        if ica is None:
            ica, _ = fit_ica(raw, pipe, log=log)
        exclude = subj.ica_exclude if subj.ica_manual else (
            auto_exclude(ica, raw, log=log) if pipe.ica_auto_eog else [])
        subj.ica_exclude = list(map(int, exclude))
        if exclude:
            ica.exclude = list(exclude)
            ica.apply(raw, verbose=False)
            log(f"ICA 去除成分 {list(exclude)}")
    ep = make_epochs(raw, pipe, log=log)
    project.store_epochs(subj, ep)
    return ep


# ====================================================================== 合成演示项目
STD32 = ["Fp1", "Fp2", "F7", "F3", "Fz", "F4", "F8", "FC5", "FC1", "FC2", "FC6", "T7", "C3", "Cz",
         "C4", "T8", "CP5", "CP1", "CP2", "CP6", "P7", "P3", "Pz", "P4", "P8", "PO3", "PO4", "O1",
         "Oz", "O2", "TP9", "TP10"]


def make_demo_project(folder, n_subs=10, n_per_cond=96, sfreq=250, seed=0, log=print):
    """生成合成演示项目（模拟信号，不是真实数据）。

    两个条件 First / Repetition：中央顶区 N400 样成分在 Repetition 减弱、枕区 alpha 刺激后下降；
    另有 8 类“朝向”标签（metadata），0.1–0.5 s 内可被解码，用于解码、RSA、IEM。
    """
    import pandas as pd
    rng = np.random.default_rng(seed)
    proj = Project(folder, name="合成演示项目")
    info = mne.create_info(STD32, sfreq, "eeg")
    mont = mne.channels.make_standard_montage("standard_1020")
    info.set_montage(mont)
    pos = np.array([info["chs"][i]["loc"][:3] for i in range(len(STD32))])
    t = np.arange(-0.5, 1.0 + 1e-9, 1 / sfreq)

    def spot(center, width=0.045):
        c = pos[STD32.index(center)]
        return np.exp(-np.sum((pos - c) ** 2, axis=1) / (2 * width ** 2))

    cz, oz, po = spot("Pz", 0.05) + 0.6 * spot("Cz", 0.05), spot("Oz", 0.05), spot("PO4", 0.06)
    for s in range(n_subs):
        w_ori = rng.normal(size=(len(STD32), 2))
        trials, labels, oris = [], [], []
        gain = 1 + 0.25 * rng.normal()
        for cond, n4, alpha_drop in (("First", -6.0, 0.6), ("Repetition", -3.0, 0.25)):
            for k in range(n_per_cond):
                ori = k % 8
                x = rng.normal(0, 5, (len(STD32), t.size))
                x += np.cumsum(rng.normal(0, 0.6, (len(STD32), t.size)), axis=1) * 0.15
                erp = (4 * np.exp(-((t - 0.12) / 0.03) ** 2) * po[:, None]
                       - 3 * np.exp(-((t - 0.17) / 0.03) ** 2) * oz[:, None]
                       + gain * n4 * np.exp(-((t - 0.40) / 0.08) ** 2) * cz[:, None])
                env = 1 - alpha_drop * ((t > 0.15) & (t < 0.8))
                alpha = 6 * env * np.sin(2 * np.pi * 10 * t + rng.uniform(0, 2 * np.pi))
                a = 2 * np.pi * ori / 8
                info_sig = 0.9 * (w_ori @ [np.cos(a), np.sin(a)])[:, None] * np.exp(-((t - 0.3) / 0.12) ** 2)
                trials.append(x + erp + alpha[None] * oz[:, None] + info_sig)
                labels.append(cond)
                oris.append(str(ori * 45))
        order = rng.permutation(len(trials))
        data = np.array(trials)[order]
        md = pd.DataFrame({"朝向": np.array(oris)[order]})
        ep = _epochs_array(data, info, list(np.array(labels)[order]), t[0], metadata=md)
        ep.apply_baseline((-0.2, 0), verbose=False)
        subj = Subject(name=f"sub{s + 1:02d}", note="合成演示数据")
        proj.subjects.append(subj)
        proj.store_epochs(subj, ep)
        log(f"合成演示项目：被试 {s + 1}/{n_subs}")
    proj.pipeline.tmin, proj.pipeline.tmax = -0.5, 1.0
    proj.save()
    with open(os.path.join(folder, "说明.txt"), "w", encoding="utf-8") as f:
        f.write("本项目是软件生成的模拟信号，只用于熟悉操作，不是真实被试数据，不能作为研究结果。\n")
    return proj
