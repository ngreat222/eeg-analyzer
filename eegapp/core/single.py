"""第 1 章：单个被试的预处理与分析（对应手册单元 3–54）。

这里的函数只做计算和出图，不碰界面；出图函数都返回 matplotlib Figure（或列表）。
"""
import re

import mne
import numpy as np
from mne.preprocessing import ICA

STANDARD_MONTAGES = [
    "standard_1020", "standard_1005", "biosemi16", "biosemi32", "biosemi64",
    "biosemi128", "biosemi256", "easycap-M1", "easycap-M10", "EGI_256",
    "GSN-HydroCel-32", "GSN-HydroCel-64_1.0", "GSN-HydroCel-128", "GSN-HydroCel-256",
    "brainproducts-RNP-BA-128", "mgh60", "mgh70",
]

CHANNEL_TYPES = ["eeg", "eog", "ecg", "emg", "misc", "stim", "resp", "bio"]


# ---------------------------------------------------------------- 通用小工具
def parse_floats(text, allow_empty=True):
    """“0, 0.5, 1” → [0.0, 0.5, 1.0]"""
    text = (text or "").strip()
    if not text:
        if allow_empty:
            return []
        raise ValueError("这里需要填数字")
    return [float(x) for x in re.split(r"[,，\s]+", text) if x]


def parse_names(text):
    return [x.strip() for x in re.split(r"[,，;；\s]+", text or "") if x.strip()]


def parse_bands(text):
    """“Theta:4-8, Alpha:8-12” → {"Theta": (4, 8), "Alpha": (8, 12)}"""
    bands = {}
    for part in re.split(r"[,，;；\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.+?)[:：]\s*([\d.]+)\s*[-–~]\s*([\d.]+)$", part)
        if not m:
            raise ValueError(f"频段写法不对：“{part}”，应写成 名称:下限-上限，例如 Alpha:8-12")
        bands[m.group(1).strip()] = (float(m.group(2)), float(m.group(3)))
    return bands


def none_if_zero(x):
    return None if x is None or x == 0 else x


# ---------------------------------------------------------------- 通道与电极（单元 4–6、9）
def read_montage_file(path):
    return mne.channels.read_custom_montage(path)


def apply_channel_setup(raw, montage_mode, montage_value, rename_from_locs, ch_types,
                        reref_mode="不改变", reref_channels="", log=print):
    """电极定位、通道重命名、通道类型、重参考。直接修改 raw。

    ch_types：按通道顺序排列的类型列表（与界面表格逐行对应，所以重命名后也能对上）。
    """
    montage = None
    if montage_mode == "standard":
        montage = mne.channels.make_standard_montage(montage_value)
    elif montage_mode == "file":
        montage = read_montage_file(montage_value)
        if rename_from_locs:
            # 手册单元 4：用位置文件里的通道名依次替换数据里的通道名
            eeg_names = [ch for ch, t in zip(raw.ch_names, raw.get_channel_types()) if t == "eeg"]
            new_names = montage.ch_names
            n = min(len(eeg_names), len(new_names))
            mapping = {eeg_names[i]: new_names[i] for i in range(n) if eeg_names[i] != new_names[i]}
            if mapping:
                raw.rename_channels(mapping)
                log(f"按位置文件重命名 {len(mapping)} 个通道")

    if ch_types:
        cur = raw.get_channel_types()
        changed = {raw.ch_names[i]: t for i, t in enumerate(ch_types)
                   if i < len(cur) and cur[i] != t}
        if changed:
            raw.set_channel_types(changed, on_unit_change="ignore")
            log(f"修改通道类型：{changed}")

    if montage is not None:
        raw.set_montage(montage, on_missing="warn", match_case=False)
        missing = [ch for ch, t in zip(raw.ch_names, raw.get_channel_types())
                   if t == "eeg" and ch not in montage.ch_names]
        log(f"已设置电极位置（{montage_value}）"
            + (f"；位置文件里找不到：{', '.join(missing)}" if missing else ""))
    elif montage_mode == "none":
        raw.set_montage(None)

    if reref_mode == "平均参考":
        raw.set_eeg_reference("average", projection=False)
        log("已改为平均参考")
    elif reref_mode == "指定通道":
        chs = parse_names(reref_channels)
        if not chs:
            raise ValueError("请填写参考通道名")
        raw.set_eeg_reference(chs, projection=False)
        log(f"已改为参考：{chs}")
    return raw


def plot_sensors(raw):
    return raw.plot_sensors(ch_type="eeg", show_names=True, show=False)


# ---------------------------------------------------------------- 查看（单元 7、8、10）
def plot_psd(inst, fmin=0.0, fmax=np.inf, average=True, picks="eeg"):
    fmax = min(fmax, inst.info["sfreq"] / 2)
    spec = inst.compute_psd(fmin=fmin, fmax=fmax, picks=picks)
    return spec.plot(average=average, show=False, picks=picks, exclude="bads")


def plot_psd_topo(raw, fmin=0.0, fmax=np.inf):
    fmax = min(fmax, raw.info["sfreq"] / 2)
    return raw.compute_psd(fmin=fmin, fmax=fmax).plot_topo(show=False)


# ---------------------------------------------------------------- 滤波（单元 11–14）
def apply_filters(raw, notch=None, harmonics=False, l_freq=None, h_freq=None,
                  method="fir", resample=None, log=print):
    nyq = raw.info["sfreq"] / 2
    if notch:
        freqs = np.arange(notch, nyq, notch) if harmonics else [notch]
        freqs = [f for f in freqs if f < nyq]
        if freqs:
            raw.notch_filter(freqs=freqs, method=method if method == "iir" else "fir")
            log(f"陷波滤波：{', '.join(f'{f:g}' for f in freqs)} Hz")
    if l_freq or h_freq:
        if h_freq and h_freq >= nyq:
            raise ValueError(f"低通截止频率 {h_freq} Hz 必须小于奈奎斯特频率 {nyq:g} Hz")
        raw.filter(l_freq=l_freq, h_freq=h_freq, method=method)
        log(f"带通滤波：{l_freq or '—'} – {h_freq or '—'} Hz（{method}）")
    if resample and resample != raw.info["sfreq"]:
        raw.resample(resample)
        log(f"重采样到 {resample:g} Hz")
    return raw


# ---------------------------------------------------------------- 坏导（单元 15–16）
def set_bads(raw, bads, log=print):
    raw.info["bads"] = list(bads)
    log(f"坏导：{', '.join(bads) if bads else '无'}")


def interpolate_bads(raw, log=print):
    if not raw.info["bads"]:
        raise ValueError("还没有标记坏导")
    if raw.get_montage() is None:
        raise ValueError("插值坏导需要电极位置，请先在「通道与电极」里设置")
    bads = list(raw.info["bads"])
    raw.interpolate_bads(reset_bads=True)
    log(f"已插值坏导：{', '.join(bads)}")
    return raw


# ---------------------------------------------------------------- ICA（单元 17–22）
def fit_ica(raw, n_components=None, method="fastica", hp=1.0, random_state=97, log=print):
    fit_params = None
    if method == "picard":
        fit_params = dict(ortho=False, extended=True)
    ica = ICA(n_components=n_components, method=method, max_iter="auto",
              random_state=random_state, fit_params=fit_params)
    raw_for_ica = raw.copy().filter(l_freq=hp, h_freq=None) if hp else raw.copy()
    ica.fit(raw_for_ica, picks="eeg")
    log(f"ICA 拟合完成：{ica.n_components_} 个成分（{method}，拟合前高通 {hp} Hz）")
    return ica, raw_for_ica


def find_eog_components(ica, raw, log=print):
    eog = [ch for ch, t in zip(raw.ch_names, raw.get_channel_types()) if t == "eog"]
    if not eog:
        raise ValueError("数据里没有 EOG 类型的通道。可以在「通道与电极」把眼电通道的类型改成 eog，"
                         "或者直接在地形图里人工判断。")
    idx, scores = ica.find_bads_eog(raw, ch_name=eog)
    log(f"根据 {', '.join(eog)} 建议排除的成分：{idx or '无'}")
    return idx


def apply_ica(ica, raw, exclude, log=print):
    ica.exclude = list(exclude)
    ica.apply(raw)
    log(f"已应用 ICA，去除成分：{list(exclude)}")
    return raw


# ---------------------------------------------------------------- 事件与分段（单元 24–34）
def event_table(raw):
    if not len(raw.annotations):
        return []
    d, c = np.unique(raw.annotations.description, return_counts=True)
    return list(zip(d.tolist(), c.tolist()))


def make_epochs(raw, event_names, tmin, tmax, baseline, reject_uv=None, flat_uv=None,
                reject_by_annotation=True, log=print):
    events, event_id = mne.events_from_annotations(raw, verbose=False)
    if event_names:
        event_id = {k: v for k, v in event_id.items() if k in event_names}
    if not event_id:
        raise ValueError("没有可用的事件。请先确认数据里有事件，并至少勾选一种。")
    reject = dict(eeg=reject_uv * 1e-6) if reject_uv else None
    flat = dict(eeg=flat_uv * 1e-6) if flat_uv else None
    epochs = mne.Epochs(raw, events, event_id=event_id, tmin=tmin, tmax=tmax,
                        baseline=baseline, preload=True, reject=reject, flat=flat,
                        reject_by_annotation=reject_by_annotation, verbose=False)
    counts = {k: len(epochs[k]) for k in event_id}
    total = int(sum(np.isin(events[:, 2], list(event_id.values()))))
    log(f"分段完成：保留 {len(epochs)}/{total} 个试次；" +
        "，".join(f"{k} {v} 个" for k, v in counts.items()))
    return epochs


def epochs_summary(epochs):
    lines = [f"<b>试次</b>：{len(epochs)}　<b>时间</b>：{epochs.tmin:g} 到 {epochs.tmax:g} s"
             f"　<b>基线</b>：{epochs.baseline}"]
    for k in epochs.event_id:
        lines.append(f"　· {k}：{len(epochs[k])} 个")
    dropped = sum(1 for d in epochs.drop_log if d)
    lines.append(f"<b>被剔除</b>：{dropped} 个")
    return "<br>".join(lines)


def plot_epochs_psd(epochs, fmin=0, fmax=np.inf):
    fmax = min(fmax, epochs.info["sfreq"] / 2)
    return epochs.compute_psd(fmin=fmin, fmax=fmax).plot(picks="eeg", show=False)


def plot_band_topomap(epochs, bands):
    return epochs.compute_psd().plot_topomap(bands=bands, vlim="joint", show=False)


def select_epochs(epochs, condition):
    return epochs if condition in (None, "", "全部条件") else epochs[condition]


# ---------------------------------------------------------------- ERP（单元 35–43）
def make_evoked(epochs, condition):
    ep = select_epochs(epochs, condition)
    evk = ep.average()
    evk.comment = condition or "全部条件"
    return evk


def plot_compare(epochs, conditions, picks, combine="mean"):
    evokeds = {c: epochs[c].average() for c in conditions}
    picks = picks or "eeg"
    figs = mne.viz.plot_compare_evokeds(evokeds, picks=picks, combine=combine, show=False)
    return figs


# ---------------------------------------------------------------- 时频（单元 44–50）
def compute_tfr(epochs, fmin, fmax, n_freqs, cycles_div, log_spacing=True, decim=1, log=print):
    if log_spacing:
        freqs = np.logspace(*np.log10([fmin, fmax]), num=n_freqs)
    else:
        freqs = np.linspace(fmin, fmax, n_freqs)
    n_cycles = freqs / cycles_div
    power, itc = epochs.compute_tfr("morlet", freqs=freqs, n_cycles=n_cycles, use_fft=True,
                                    return_itc=True, average=True, decim=decim)
    log(f"时频分析完成：{len(freqs)} 个频率（{fmin:g}–{fmax:g} Hz），n_cycles = freqs/{cycles_div:g}")
    return power, itc
