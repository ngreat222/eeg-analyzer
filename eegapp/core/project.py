"""项目：一组被试 + 一套预处理设置。所有分析都从项目里的分段数据（Epochs）出发。

一个被试可以有多条记录（例如“文本”“图像”两个条件各录一次）。流程分三步，互不依赖：
    预处理   连续数据 → 干净的连续数据（不需要事件）
    事件     每条记录一张事件表，可以随时导入、添加、改名
    分段     干净数据 + 事件（或固定长度）→ 被试的分段数据，供各项分析使用

项目保存在一个文件夹里：
    project.json              被试、记录、设置
    clean/<记录>-raw.fif      预处理后的连续数据
    events/<记录>.csv         事件表
    epochs/<被试>-epo.fif     分段数据
"""
import json
import os
import re
from dataclasses import asdict, dataclass, field

import mne
import numpy as np

from . import events as ev
from . import reading, single

REC_RAW = "待预处理"
REC_DONE = "已预处理"
STATUS_RAW = "未分段"
STATUS_DONE = "可分析"
EPOCH_EVENTS = "按事件"
EPOCH_FIXED = "固定长度"


@dataclass
class Pipeline:
    # 通道与电极
    montage: str = "自动识别"          # 自动识别 / 标准模板名 / 不设置
    reref: str = "不改变"              # 不改变 / 平均参考 / 指定通道
    reref_channels: str = ""
    # 滤波
    notch: float = 50.0               # 0 = 不做
    harmonics: bool = False
    l_freq: float = 0.1               # 0 = 不做
    h_freq: float = 30.0              # 0 = 不做
    resample: float = 0.0             # 0 = 不改变
    # 坏导与 ICA
    interpolate: bool = True
    ica: bool = True
    ica_n: str = ""                   # 空 = 全部；整数或 0–1 小数
    ica_method: str = "fastica"
    ica_auto_eog: bool = True         # 没有眼电通道时用前额电极代替
    # 分段
    epoch_mode: str = EPOCH_EVENTS
    events: list = field(default_factory=list)   # 空 = 全部事件（BAD_ 开头的除外）
    tmin: float = -0.2
    tmax: float = 0.8
    baseline: bool = True
    bl0: float = -0.2
    bl1: float = 0.0
    fixed_len: float = 2.0
    fixed_overlap: float = 0.0
    reject_uv: float = 150.0          # 0 = 不剔除
    # 事件表格的导入方式（批量用）
    event_import: dict = field(default_factory=dict)


@dataclass
class Recording:
    id: str
    source: str
    condition: str = ""
    status: str = REC_RAW
    non_eeg: list = None              # None = 还没确认，按自动建议
    bads: list = field(default_factory=list)
    ica_exclude: list = field(default_factory=list)
    ica_manual: bool = False          # True = 排除成分由用户手动指定
    clean_file: str = ""
    note: str = ""

    @property
    def label(self):
        return self.condition or os.path.basename(self.source)


@dataclass
class Subject:
    name: str
    recordings: list = field(default_factory=list)
    status: str = STATUS_RAW
    epochs_file: str = ""
    locs: str = ""                    # 该被试专用的电极位置文件（可选）
    rename_from_locs: bool = False
    n_trials: dict = field(default_factory=dict)
    note: str = ""

    @property
    def source(self):
        return self.recordings[0].source if self.recordings else ""


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

    def path(self, rel):
        return rel if os.path.isabs(rel) else os.path.join(self.folder, rel)

    def save(self):
        os.makedirs(self.epochs_dir, exist_ok=True)
        subs = []
        for s in self.subjects:
            d = asdict(s)
            d["recordings"] = [asdict(r) for r in s.recordings]
            subs.append(d)
        data = dict(name=self.name, pipeline=asdict(self.pipeline), subjects=subs, version=3)
        tmp = self.json_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.json_path)

    @classmethod
    def load(cls, folder):
        p = cls(folder)
        with open(p.json_path, encoding="utf-8") as f:
            data = json.load(f)
        p.name = data.get("name", p.name)
        p.pipeline = Pipeline(**{k: v for k, v in data.get("pipeline", {}).items()
                                 if k in Pipeline.__dataclass_fields__})
        for d in data.get("subjects", []):
            recs = [Recording(**{k: v for k, v in r.items() if k in Recording.__dataclass_fields__})
                    for r in d.get("recordings", [])]
            if not recs and d.get("source"):          # 旧版项目：一个被试一个文件
                recs = [Recording(id=d["name"], source=d["source"], bads=d.get("bads", []),
                                  ica_exclude=d.get("ica_exclude", []), ica_manual=d.get("ica_manual", False))]
            s = Subject(**{k: v for k, v in d.items() if k in Subject.__dataclass_fields__ and k != "recordings"})
            s.recordings = recs
            if s.epochs_file:
                s.status = STATUS_DONE
            p.subjects.append(s)
        return p

    # ------------------------------------------------------------ 被试与记录
    def unique_name(self, base):
        base = _safe(base) or "sub"
        names = {s.name for s in self.subjects}
        if base not in names:
            return base
        i = 2
        while f"{base}_{i}" in names:
            i += 1
        return f"{base}_{i}"

    def unique_rec_id(self, base):
        base = _safe(base) or "rec"
        ids = {r.id for _, r in self.recordings()}
        if base not in ids:
            return base
        i = 2
        while f"{base}_{i}" in ids:
            i += 1
        return f"{base}_{i}"

    def get(self, name):
        for s in self.subjects:
            if s.name == name:
                return s
        raise KeyError(name)

    def find(self, name):
        return next((s for s in self.subjects if s.name == name), None)

    def recordings(self):
        return [(s, r) for s in self.subjects for r in s.recordings]

    def get_rec(self, rec_id):
        for s, r in self.recordings():
            if r.id == rec_id:
                return s, r
        raise KeyError(rec_id)

    def remove(self, name):
        self.subjects = [s for s in self.subjects if s.name != name]
        self._cache.pop(name, None)

    def remove_rec(self, rec_id):
        for s in self.subjects:
            s.recordings = [r for r in s.recordings if r.id != rec_id]
        self.subjects = [s for s in self.subjects if s.recordings or s.epochs_file]

    def done_subjects(self):
        return [s for s in self.subjects if s.epochs_file]

    # ------------------------------------------------------------ 事件
    def events_path(self, rec):
        return os.path.join(self.folder, "events", f"{rec.id}.csv")

    def events(self, rec, log=print):
        """读取一条记录的事件表。第一次读取时，从数据自带的标记里取出来存好。"""
        path = self.events_path(rec)
        if os.path.exists(path):
            return ev.load(path)
        try:
            raw, _ = reading.read_raw_any(rec.source, log=log)
            df = ev.from_raw(raw)
        except Exception as e:  # noqa: BLE001
            log(f"{rec.id}：读取数据自带的事件失败：{e}")
            return ev.empty()
        ev.save(df, path)
        if len(df):
            log(f"{rec.id}：数据自带 {len(df)} 个事件")
        return df

    def save_events(self, rec, df):
        ev.save(df, self.events_path(rec))

    # ------------------------------------------------------------ 分段数据
    def epochs(self, name):
        """读取（并缓存）一个被试的分段数据。"""
        if name not in self._cache:
            s = self.get(name)
            self._cache[name] = read_epochs(self.path(s.epochs_file))
        return self._cache[name]

    def store_epochs(self, subj, epochs):
        os.makedirs(self.epochs_dir, exist_ok=True)
        rel = os.path.join("epochs", f"{subj.name}-epo.fif")
        save_epochs(epochs, os.path.join(self.folder, rel))
        subj.epochs_file = rel
        subj.status = STATUS_DONE
        subj.n_trials = {k: int(len(epochs[k])) for k in epochs.event_id}
        self._cache[subj.name] = epochs

    def conditions(self):
        """所有已分段被试共有的条件。"""
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
                cols += [c for c in md.columns if c != "条件" and md[c].nunique() >= 2]
        return cols

    def summary(self):
        done = self.done_subjects()
        recs = self.recordings()
        info = dict(n=len(self.subjects), done=len(done), sfreq=None, n_ch=None, conds=[],
                    n_rec=len(recs), rec_done=sum(r.status == REC_DONE for _, r in recs))
        if done:
            ep = self.epochs(done[0].name)
            info.update(sfreq=ep.info["sfreq"], n_ch=len(mne.pick_types(ep.info, eeg=True)),
                        conds=self.conditions(), tmin=ep.tmin, tmax=ep.tmax)
        return info


def save_epochs(epochs, path):
    """保存分段数据。MNE 1.12 读不回非 ASCII 的事件名，所以事件名先转成 %XX 编码再存。"""
    from urllib.parse import quote
    orig = dict(epochs.event_id)
    epochs.event_id = {quote(k, safe="/ ()[]-_.,+=@!~"): v for k, v in orig.items()}
    try:
        epochs.save(path, overwrite=True, verbose=False)
    finally:
        epochs.event_id = orig


def read_epochs(path):
    from urllib.parse import unquote
    ep = mne.read_epochs(path, preload=True, verbose=False)
    if any("%" in k for k in ep.event_id):
        ep.event_id = {unquote(k): v for k, v in ep.event_id.items()}
    return ep


def _safe(text):
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", str(text)).strip("_")


# ====================================================================== 导入
def add_raw_files(project, paths, subject=None, condition=""):
    """每个文件一条记录。subject 为空时，每个文件单独成为一个被试（名字取文件名）。"""
    added = []
    for p in paths:
        stem = file_stem(p)
        name = subject or stem
        s = project.find(name)
        if s is None:
            s = Subject(name=project.unique_name(name))
            project.subjects.append(s)
        rid = project.unique_rec_id(f"{s.name}_{condition}" if condition else (s.name if not s.recordings else stem))
        r = Recording(id=rid, source=os.path.normpath(p), condition=condition)
        s.recordings.append(r)
        s.status, s.epochs_file = STATUS_RAW, ""
        added.append(r)
    return added


SKIP_DIR_NAMES = {"eeg", "脑电", "data", "数据", "raw", "原始数据"}


def scan_folder(root):
    """扫描文件夹里的脑电文件，按目录结构猜被试和条件。

    root/被试/条件/…/文件  →  被试、条件；root/被试/文件 → 只有被试。
    同名但多一段扩展名的文件（如 X.md.edf 与 X.edf）通常是附属数据，默认不勾选。
    返回 [dict(path, subject, condition, use)]。
    """
    exts = reading.RAW_EXTS
    found = []
    for d, _, files in os.walk(root):
        for f in files:
            low = f.lower()
            if any(low.endswith(e) for e in exts) and not low.endswith((".fdt", ".vmrk", ".eeg")):
                found.append(os.path.join(d, f))
    found.sort()
    by_dir = {}
    for p in found:
        by_dir.setdefault(os.path.dirname(p), []).append(os.path.basename(p))
    rows = []
    for p in found:
        rel = os.path.relpath(p, root).split(os.sep)
        parts = rel[:-1]
        name = os.path.basename(p)
        subject = parts[0] if parts else file_stem(name)
        cond_parts = [x for x in parts[1:] if x.lower() not in SKIP_DIR_NAMES]
        ext = os.path.splitext(name)[1].lower()
        sidecar = any(o != name and o.lower().endswith(ext) and name.startswith(os.path.splitext(o)[0] + ".")
                      for o in by_dir[os.path.dirname(p)])
        rows.append(dict(path=p, subject=subject, condition="/".join(cond_parts), use=not sidecar))
    return rows


def file_stem(name):
    name = os.path.basename(name)
    if name.lower().endswith(".gz"):
        name = name[:-3]
    return os.path.splitext(name)[0]


def add_scanned(project, rows):
    added = []
    for r in rows:
        if r["use"]:
            added += add_raw_files(project, [r["path"]], subject=r["subject"] or None, condition=r["condition"])
    return added


def add_epochs_files(project, paths, log=print):
    added = []
    for p in paths:
        ep = read_epochs(p)
        stem = re.sub(r"[-_]epo$", "", os.path.basename(p).split(".")[0])
        s = Subject(name=project.unique_name(stem), note=f"导入：{p}")
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

# ====================================================================== 预处理（连续数据，不需要事件）
_STD = None
FRONTAL = ("Fp1", "Fp2", "Fpz", "AF7", "AF8", "AF3", "AF4")


def _std_names():
    global _STD
    if _STD is None:
        _STD = {c.lower() for c in mne.channels.make_standard_montage("standard_1005").ch_names}
    return _STD


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


def suggest_non_eeg(raw):
    """找出不像脑电的通道（时间戳、计数器、电量、接触质量、标记等）。返回 {通道: 原因}。

    规则是通用的：大多数通道是标准电极名时，名字对不上的算非脑电；另外数值恒定或只有几个固定值的也算。
    """
    eeg = [c for c, t in zip(raw.ch_names, raw.get_channel_types()) if t == "eeg"]
    if not eeg:
        return {}
    std = _std_names()
    hits = [c for c in eeg if c.lower() in std]
    # 有一批通道是标准电极名时（8 个以上，或过半），名字对不上的就不是脑电
    by_name = len(hits) >= 8 or (len(hits) >= 4 and len(hits) >= 0.5 * len(eeg))
    seg = None
    if raw.preload:
        stop = min(raw.n_times, int(60 * raw.info["sfreq"]))
        seg = raw.get_data(picks=eeg, stop=stop)
    out = {}
    for i, c in enumerate(eeg):
        if by_name and c.lower() not in std:
            out[c] = "不是标准电极名"
        elif seg is not None:
            x = seg[i]
            if np.ptp(x) == 0:
                out[c] = "数值不变"
            elif np.unique(np.round(x[:5000] * 1e6, 3)).size <= 5:
                out[c] = "只有几个固定数值"
    return out


def open_raw(subj, rec, pipe, log=print, hint_out=None):
    """读取一条记录，并完成通道设置（去掉非脑电通道、电极位置、坏导标记）。返回 (raw, 去掉的通道)。

    hint_out 传入字典时，会填入自动识别的非脑电通道 {通道: 原因}。
    """
    raw, _ = reading.read_raw_any(rec.source, log=log)
    if subj.locs:
        single.apply_channel_setup(raw, "file", subj.locs, subj.rename_from_locs, None, log=log)
    ch = _auto_types(raw)
    if ch:
        log(f"按通道名设置类型：{ch}")
    hint = suggest_non_eeg(raw)
    if hint_out is not None:
        hint_out.update(hint)
    non = rec.non_eeg if rec.non_eeg is not None else list(hint)
    non = [c for c in non if c in raw.ch_names]
    if non:
        raw.drop_channels(non)
        log(f"去掉 {len(non)} 个非脑电通道，剩 {len(raw.ch_names)} 个")
    if not subj.locs:
        if pipe.montage == "自动识别":
            _auto_montage(raw, log)
        elif pipe.montage != "不设置":
            raw.set_montage(mne.channels.make_standard_montage(pipe.montage), on_missing="warn",
                            match_case=False)
    raw.info["bads"] = [b for b in rec.bads if b in raw.ch_names]
    return raw, non


def _auto_montage(raw, log):
    std = mne.channels.make_standard_montage("standard_1005")
    eeg = [c for c, t in zip(raw.ch_names, raw.get_channel_types()) if t == "eeg"]
    hit = sum(c.lower() in _std_names() for c in eeg)
    if eeg and hit >= 0.6 * len(eeg):
        raw.set_montage(std, on_missing="ignore", match_case=False)
        log(f"自动匹配标准 10-05 电极位置：{hit}/{len(eeg)} 个通道")
        return True
    log("通道名不是标准 10-20 名称，未设置电极位置（地形图、插值需要电极位置）")
    return False


def filter_raw(raw, pipe, log=print, interpolate=True):
    if not pipe.l_freq:
        # 不做高通时至少去掉直流偏置，否则振幅剔除会把所有试次都剔掉
        picks = mne.pick_types(raw.info, eeg=True, eog=True, exclude=[])
        raw._data[picks] -= np.median(raw._data[picks], axis=1, keepdims=True)
        log("未做高通滤波，已去掉各通道的直流偏置")
    single.apply_filters(raw, single.none_if_zero(pipe.notch), pipe.harmonics,
                         single.none_if_zero(pipe.l_freq), single.none_if_zero(pipe.h_freq),
                         "fir", single.none_if_zero(pipe.resample), log=log)
    if interpolate and pipe.interpolate and raw.info["bads"]:
        if raw.get_montage() is None:
            log("没有电极位置，坏导只标记、不插值")
        else:
            single.interpolate_bads(raw, log=log)
    if pipe.reref == "平均参考":
        raw.set_eeg_reference("average", projection=False, verbose=False)
        log("平均参考")
    elif pipe.reref == "指定通道" and pipe.reref_channels.strip():
        raw.set_eeg_reference(single.parse_names(pipe.reref_channels), projection=False, verbose=False)
        log(f"参考：{pipe.reref_channels}")
    return raw


def suggest_bads(raw, win_sec=2.0):
    """根据信号本身建议坏导，返回 {通道: 原因}。

    把整段数据切成 2 秒的小窗，在每个小窗里和其他电极比较；长时间“几乎没有信号”或“噪声明显偏大”的算坏导，
    和所有电极都不相关的也算。只在某一段坏的电极也能找出来。
    """
    picks = mne.pick_types(raw.info, eeg=True, exclude=[])
    if len(picks) < 4:
        return {}
    names = np.array([raw.ch_names[i] for i in picks])
    step = max(1, int(raw.info["sfreq"] // 100))
    x = raw.get_data(picks=picks)[:, ::step]
    win = max(int(win_sec * raw.info["sfreq"] / step), 10)
    nw = x.shape[1] // win
    if nw < 3:
        return {}
    sd = x[:, :nw * win].reshape(len(picks), nw, win).std(-1)
    ls = np.log(sd + 1e-12)
    med = np.median(ls, axis=0)
    mad = np.median(np.abs(ls - med), axis=0) * 1.4826 + 1e-6
    z = (ls - med) / mad
    flat = (sd < 0.5e-6).mean(1)
    noisy = (z > 3).mean(1)
    ratio = np.exp(np.median(ls - med, axis=1))
    out = {}
    for i, n in enumerate(names):
        if flat[i] > 0.3:
            out[n] = f"{flat[i]:.0%} 的时间几乎没有信号"
        elif noisy[i] > 0.4 or (noisy[i] > 0.2 and ratio[i] > 3):
            out[n] = f"{noisy[i]:.0%} 的时间噪声明显偏大（约为其他电极的 {ratio[i]:.1f} 倍）"
    ok = flat <= 0.3
    if ok.sum() >= 4:
        c = np.corrcoef(x[ok])
        np.fill_diagonal(c, 0)
        best = np.nanmax(np.abs(c), axis=1)
        for n, b in zip(names[ok], best):
            if b < 0.4 and n not in out:
                out[n] = f"和其他电极都不相关（最大相关 {b:.2f}）"
    return {str(k): v for k, v in out.items()}


def drop_summary(epochs, top=5):
    """哪些通道导致分段被剔除最多：[(通道, 次数)]。"""
    from collections import Counter
    c = Counter(ch for log_ in epochs.drop_log for ch in log_ if ch not in ("IGNORED", "NO_DATA"))
    return c.most_common(top)


def ica_n(pipe):
    t = (pipe.ica_n or "").strip()
    if not t:
        return None
    return float(t) if "." in t else int(t)


def fit_ica(raw, pipe, log=print):
    return single.fit_ica(raw, ica_n(pipe), pipe.ica_method, 1.0, 97, log=log)


def auto_exclude(ica, raw, pipe=None, log=print):
    """建议去除的眼电成分。有眼电通道时用眼电通道；没有时用前额电极（Fp1/Fp2 等）代替。"""
    eog = [ch for ch, t in zip(raw.ch_names, raw.get_channel_types()) if t == "eog"]
    try:
        if eog:
            idx, _ = ica.find_bads_eog(raw, ch_name=eog, verbose=False)
            log(f"根据 {', '.join(eog)} 建议去除成分：{idx or '无'}")
            return [int(i) for i in idx]
        if pipe is not None and not pipe.ica_auto_eog:
            return []
        front = [c for c in FRONTAL if c in raw.ch_names and c not in raw.info["bads"]]
        if not front:
            log("没有眼电通道，也没有前额电极，未自动识别眼电成分，请在成分地形图里人工判断")
            return []
        idx, _ = ica.find_bads_eog(raw, ch_name=front, threshold=3.0, verbose=False)
        idx = [int(i) for i in idx][:3]
        log(f"没有眼电通道，用 {', '.join(front)} 代替，建议去除成分：{idx or '无'}")
        return idx
    except Exception as e:  # noqa: BLE001
        log(f"未自动识别眼电成分：{e}")
        return []


def clean_path(project, rec):
    return os.path.join("clean", f"{rec.id}-raw.fif")


def preprocess_recording(project, subj, rec, raw=None, ica=None, log=print):
    """预处理一条连续数据并保存。raw/ica 可传入界面里已经检查过的对象（raw 必须已滤波）。"""
    pipe = project.pipeline
    if raw is None:
        raw, _ = open_raw(subj, rec, pipe, log=log)
        filter_raw(raw, pipe, log=log)
    if pipe.ica:
        if ica is None:
            ica, _ = fit_ica(raw, pipe, log=log)
        exclude = rec.ica_exclude if rec.ica_manual else auto_exclude(ica, raw, pipe, log=log)
        rec.ica_exclude = [int(x) for x in exclude]
        if exclude:
            ica.exclude = list(rec.ica_exclude)
            ica.apply(raw, verbose=False)
            log(f"ICA 去除成分 {rec.ica_exclude}")
    raw.set_annotations(mne.Annotations([], [], []))       # 事件另外保存，干净数据里不带
    rel = clean_path(project, rec)
    os.makedirs(os.path.join(project.folder, "clean"), exist_ok=True)
    raw.save(project.path(rel), overwrite=True, fmt="single", verbose=False)
    rec.clean_file = rel
    rec.status = REC_DONE
    log(f"{rec.id}：预处理完成，{len(raw.ch_names)} 个通道，{raw.times[-1]:.0f} 秒")
    return raw


# ====================================================================== 分段
def _key(rec, name):
    return f"{rec.condition}/{name}" if rec.condition else str(name)


def epoch_subject(project, subj, log=print):
    """把一个被试所有预处理好的记录分段，合并成该被试的分段数据。

    有条件的记录，事件名前加条件，例如“文本/刺激出现”；分析时可以按“文本”或“刺激出现”选取。
    """
    import pandas as pd
    pipe = project.pipeline
    recs = [r for r in subj.recordings if r.status == REC_DONE and r.clean_file
            and os.path.exists(project.path(r.clean_file))]
    if not recs:
        raise ValueError(f"{subj.name} 还没有预处理好的记录，请先到「预处理」处理")
    raws = [(r, mne.io.read_raw_fif(project.path(r.clean_file), preload=True, verbose=False)) for r in recs]
    common = [c for c in raws[0][1].ch_names if all(c in x.ch_names for _, x in raws)]
    sf0 = raws[0][1].info["sfreq"]
    bads = sorted(set().union(*[set(x.info["bads"]) for _, x in raws]) & set(common))
    reject = dict(eeg=pipe.reject_uv * 1e-6) if pipe.reject_uv else None
    fixed = pipe.epoch_mode == EPOCH_FIXED

    tables = {r.id: project.events(r, log=log) for r, _ in raws}
    want = set(pipe.events)
    keys = []
    for r, _ in raws:
        if fixed:
            keys.append(r.condition or "片段")
        else:
            keys += [_key(r, n) for n in ev.names(tables[r.id]) if not want or n in want]
    code = {k: i + 1 for i, k in enumerate(dict.fromkeys(keys))}
    if not code:
        raise ValueError(f"{subj.name}：没有可用的事件。到「事件」页添加事件，或把分段方式改成“固定长度”")

    parts = []
    for r, raw in raws:
        raw.pick(common)
        if raw.info["sfreq"] != sf0:
            raw.resample(sf0)
        raw.info["bads"] = list(bads)
        df = tables[r.id]
        bad = df[df["description"].map(ev.is_bad)]
        raw.set_annotations(ev.to_annotations(bad) if len(bad) else mne.Annotations([], [], []))
        if fixed:
            k = r.condition or "片段"
            events = mne.make_fixed_length_events(raw, id=code[k], duration=pipe.fixed_len,
                                                  overlap=min(pipe.fixed_overlap, pipe.fixed_len * 0.9))
            md = pd.DataFrame({"记录条件": r.condition or "—", "事件": "片段", "记录": r.id}, index=range(len(events)))
            ep = mne.Epochs(raw, events, {k: code[k]}, tmin=0, tmax=pipe.fixed_len - 1 / sf0, baseline=None,
                            reject=reject, reject_by_annotation=True, preload=True, metadata=md, verbose=False)
        else:
            sel = df[~df["description"].map(ev.is_bad)]
            if want:
                sel = sel[sel["description"].isin(want)]
            if not len(sel):
                log(f"{r.id}：没有选中的事件，跳过")
                continue
            samp = raw.first_samp + np.round(sel["onset"].values * sf0).astype(int)
            inside = (samp >= raw.first_samp) & (samp < raw.first_samp + raw.n_times)
            sel, samp = sel[inside], samp[inside]
            ks = [_key(r, n) for n in sel["description"]]
            events = np.c_[samp, np.zeros(len(samp), int), [code[k] for k in ks]]
            md = pd.DataFrame({"记录条件": r.condition or "—", "事件": sel["description"].values, "记录": r.id})
            bl = (pipe.bl0, pipe.bl1) if pipe.baseline else None
            ep = mne.Epochs(raw, events, {k: code[k] for k in dict.fromkeys(ks)}, tmin=pipe.tmin, tmax=pipe.tmax,
                            baseline=bl, reject=reject, reject_by_annotation=True, preload=True, metadata=md,
                            event_repeated="drop", on_missing="ignore", verbose=False)
        n0 = sum(1 for d in ep.drop_log if "IGNORED" not in d)
        worst = drop_summary(ep, 3)
        log(f"{r.id}：保留 {len(ep)}/{n0} 段" +
            (f"；剔除最多的通道：{'、'.join(f'{c} {n} 次' for c, n in worst)}" if worst else ""))
        if len(ep):
            parts.append(ep)
    if not parts:
        raise ValueError(f"{subj.name}：所有分段都被剔除了。可以调高振幅剔除阈值，或检查预处理")
    ep = mne.concatenate_epochs(parts, verbose=False) if len(parts) > 1 else parts[0]
    project.store_epochs(subj, ep)
    log(f"{subj.name}：分段完成，" + "，".join(f"{k} {v}" for k, v in subj.n_trials.items()))
    return ep


def run_pipeline(project, subj, log=print):
    """完整处理一个被试：所有记录预处理，然后分段。"""
    for rec in subj.recordings:
        preprocess_recording(project, subj, rec, log=log)
    return epoch_subject(project, subj, log=log)


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
