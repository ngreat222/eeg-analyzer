"""第 3、4 章的数据读取：多被试的分段数据（-epo.fif）或手册用的数组文件（.mat/.h5/.npy/.npz）。

统一返回形状 [试次, 通道, 时间] 的 float 数组。
"""
import os

import numpy as np

GROUP_FILE_FILTER = ("分段/数组文件 (*-epo.fif *_epo.fif *.fif *-ave.fif *.mat *.h5 *.hdf5 *.npy *.npz);;"
                     "所有文件 (*)")
LABEL_FILE_FILTER = "标签文件 (*.txt *.csv *.tsv *.npy *.mat);;所有文件 (*)"

DIM_ORDERS = {
    "通道 × 时间 × 试次（手册第 3 章 .mat 格式）": "ch_times_trials",
    "试次 × 通道 × 时间（手册第 4 章 / MNE 格式）": "trials_ch_times",
    "通道 × 时间（已经平均过的 ERP）": "ch_times",
}


def _ext(path):
    p = path.lower()
    if p.endswith((".fif", ".fif.gz")):
        return "fif"
    return os.path.splitext(p)[1].lstrip(".")


def _read_mat(path):
    import scipy.io as sio
    try:
        d = sio.loadmat(path)
        return {k: v for k, v in d.items() if not k.startswith("__") and isinstance(v, np.ndarray)}
    except NotImplementedError:
        # MATLAB v7.3 文件就是 HDF5，维度顺序和 MATLAB 里相反
        import h5py
        out = {}
        with h5py.File(path, "r") as f:
            for k, v in f.items():
                if isinstance(v, h5py.Dataset):
                    out[k] = np.array(v).T
        return out


def _array_keys(path):
    ext = _ext(path)
    if ext == "mat":
        return [k for k, v in _read_mat(path).items() if v.ndim >= 2]
    if ext in ("h5", "hdf5"):
        import h5py
        with h5py.File(path, "r") as f:
            return [k for k, v in f.items() if isinstance(v, h5py.Dataset) and v.ndim >= 2]
    if ext == "npz":
        with np.load(path) as z:
            return [k for k in z.files if z[k].ndim >= 2]
    if ext == "npy":
        return ["(整个数组)"]
    return []


def is_mne_file(path):
    return _ext(path) == "fif"


def list_keys(path):
    """分段文件返回其中的事件名；数组文件返回其中的变量名。"""
    if is_mne_file(path):
        import mne
        if "-ave" in path.lower() or "_ave" in path.lower():
            return [e.comment or f"evoked{i}" for i, e in enumerate(mne.read_evokeds(path, verbose=False))]
        ep = mne.read_epochs(path, preload=False, verbose=False)
        return list(ep.event_id.keys())
    return _array_keys(path)


def _read_array(path, key):
    ext = _ext(path)
    if ext == "mat":
        d = _read_mat(path)
        if key not in d:
            raise KeyError(f"{os.path.basename(path)} 里没有变量 “{key}”，现有：{list(d)}")
        return np.asarray(d[key], dtype=float)
    if ext in ("h5", "hdf5"):
        import h5py
        with h5py.File(path, "r") as f:
            if key not in f:
                raise KeyError(f"{os.path.basename(path)} 里没有 “{key}”，现有：{list(f.keys())}")
            return np.array(f[key], dtype=float)
    if ext == "npz":
        with np.load(path) as z:
            return np.asarray(z[key], dtype=float)
    if ext == "npy":
        return np.asarray(np.load(path), dtype=float)
    raise ValueError(f"不支持的文件类型：{path}")


def load_condition(path, key, dim_order="ch_times_trials", to_uv=True, picks=None):
    """读取一个被试一个条件的数据，返回 (data[试次,通道,时间], meta)。

    meta 对 MNE 文件包含 sfreq / tmin / ch_names，对数组文件为 None（需要用户填写）。
    """
    if is_mne_file(path):
        import mne
        low = path.lower()
        if "-ave" in low or "_ave" in low:
            evs = mne.read_evokeds(path, verbose=False)
            names = [e.comment or f"evoked{i}" for i, e in enumerate(evs)]
            evk = evs[names.index(key)] if key in names else evs[0]
            evk = evk.copy().pick(picks or "eeg")
            data = evk.data[None]
            meta = dict(sfreq=evk.info["sfreq"], tmin=evk.times[0], ch_names=evk.ch_names)
        else:
            ep = mne.read_epochs(path, preload=True, verbose=False)
            if key and key in ep.event_id:
                ep = ep[key]
            ep = ep.pick(picks or "eeg")
            data = ep.get_data()
            meta = dict(sfreq=ep.info["sfreq"], tmin=ep.tmin, ch_names=ep.ch_names)
        if to_uv:
            data = data * 1e6
        return data, meta

    arr = _read_array(path, key)
    if dim_order == "ch_times_trials":
        if arr.ndim == 2:
            arr = arr[:, :, None]
        data = np.transpose(arr, (2, 0, 1))
    elif dim_order == "trials_ch_times":
        if arr.ndim == 2:
            arr = arr[None]
        data = arr
    elif dim_order == "ch_times":
        data = arr[None] if arr.ndim == 2 else arr.mean(axis=-1)[None]
    else:
        raise ValueError(dim_order)
    return data, None


def load_labels(path, column=1, key=None):
    """读取一个被试的试次标签。手册第 4 章的 txt 第 2 列（column=1）是 0–15 的类别。"""
    ext = _ext(path)
    if ext in ("txt", "csv", "tsv", "dat"):
        delim = "," if ext == "csv" else None
        arr = np.loadtxt(path, delimiter=delim, ndmin=2)
        if column >= arr.shape[1]:
            raise ValueError(f"{os.path.basename(path)} 只有 {arr.shape[1]} 列，没有第 {column + 1} 列")
        return arr[:, column]
    if ext == "npy":
        arr = np.load(path)
        return arr if arr.ndim == 1 else arr[:, column]
    if ext == "mat":
        d = _read_mat(path)
        k = key or next(iter(d))
        arr = np.asarray(d[k]).squeeze()
        return arr if arr.ndim == 1 else arr[:, column]
    raise ValueError(f"不支持的标签文件：{path}")


def labels_from_epochs(path):
    """-epo.fif 文件：用事件码当标签，返回 (data µV, labels, meta, 事件名对照)。"""
    import mne
    ep = mne.read_epochs(path, preload=True, verbose=False).pick("eeg")
    inv = {v: k for k, v in ep.event_id.items()}
    codes = ep.events[:, 2]
    return ep.get_data() * 1e6, codes, dict(sfreq=ep.info["sfreq"], tmin=ep.tmin,
                                             ch_names=ep.ch_names), inv


def resolve_channels(spec, ch_names, n_channels):
    """通道写法：名字（Cz, Pz）或手册里的从 1 开始的序号（50）。返回下标列表。"""
    from .single import parse_names
    items = parse_names(spec)
    if not items:
        raise ValueError("请填写要分析的通道")
    idx = []
    for it in items:
        if it.isdigit():
            i = int(it) - 1
            if not 0 <= i < n_channels:
                raise ValueError(f"通道序号 {it} 超出范围（共 {n_channels} 个通道）")
            idx.append(i)
        else:
            if not ch_names:
                raise ValueError(f"数组文件没有通道名，请用序号（从 1 开始），不能写 “{it}”")
            low = [c.lower() for c in ch_names]
            if it.lower() not in low:
                raise ValueError(f"找不到通道 “{it}”")
            idx.append(low.index(it.lower()))
    return idx
