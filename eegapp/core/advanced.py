"""第 4 章：逐时间点解码、跨时间解码、RSA、IEM（手册第 4 章代码 3–22）。

手册用的是 NeuroRA 1.x；这里调用 NeuroRA 2.0，参数含义相同，个别参数名有变化
（iter → n_iter；eegRDM 去掉了 sub_opt；解码函数不再自带 smooth，改为算完后平滑）。
"""
from dataclasses import dataclass

import numpy as np
from matplotlib.figure import Figure
from scipy.ndimage import uniform_filter1d
from scipy.stats import ttest_ind

from .group import COLORS, _spines
from .stats import sem, warn_small_n


@dataclass
class DecodingData:
    data: np.ndarray        # [被试, 试次, 通道, 时间]
    labels: np.ndarray      # [被试, 试次]，0..n-1
    class_names: list
    sfreq: float
    tmin: float
    label_name: str = "标签"

    @property
    def n_classes(self):
        return len(self.class_names)

    def times(self):
        return self.tmin + np.arange(self.data.shape[-1]) / self.sfreq

    def summary(self):
        s, tr, ch, t = self.data.shape
        cnt = np.bincount(self.labels.astype(int).ravel(), minlength=self.n_classes)
        return (f"<b>被试</b>：{s}　<b>每人试次</b>：{tr}　<b>通道</b>：{ch}　<b>时间点</b>：{t}"
                f"（{self.tmin:g} 到 {self.times()[-1]:.3f} s，{self.sfreq:g} Hz）<br>"
                f"<b>{self.label_name}</b>：{self.n_classes} 类　"
                + "，".join(f"{n}×{c}" for n, c in zip(self.class_names, cnt)))


def _remap(label_list):
    allv = np.unique(np.concatenate([np.asarray(x).ravel() for x in label_list]))
    lut = {v: i for i, v in enumerate(allv)}
    names = [(f"{v:g}" if isinstance(v, (float, np.floating)) else str(v)) for v in allv]
    return [np.array([lut[v] for v in np.asarray(x).ravel()]) for x in label_list], names


def load_decoding(files, key, dim_order, label_files, label_col, sfreq, tmin, crop,
                  label_name="标签", log=print):
    from .arrays import labels_from_epochs, load_condition, load_labels
    datas, labs, meta, code_names = [], [], None, None
    for i, f in enumerate(files):
        if label_files:
            d, m = load_condition(f, key, dim_order)
            lab = load_labels(label_files[i], label_col)
        else:
            d, lab, m, code_names = labels_from_epochs(f)
        if m is not None and meta is None:
            meta = m
        if len(lab) != d.shape[0]:
            raise ValueError(f"第 {i + 1} 个被试：数据有 {d.shape[0]} 个试次，标签有 {len(lab)} 个")
        datas.append(d)
        labs.append(lab)
    if label_files and len(label_files) != len(files):
        raise ValueError(f"数据文件 {len(files)} 个，标签文件 {len(label_files)} 个，数量要一致")
    if meta is not None:
        sfreq, tmin = meta["sfreq"], meta["tmin"]
    if not sfreq:
        raise ValueError("数组文件需要填写采样率")
    shapes = {d.shape[1:] for d in datas}
    if len(shapes) != 1:
        raise ValueError(f"各被试的 通道×时间 形状不一致：{sorted(shapes)}")
    n_min = min(d.shape[0] for d in datas)
    if any(d.shape[0] != n_min for d in datas):
        log(f"各被试试次数不同，统一截取前 {n_min} 个试次")
    data = np.stack([d[:n_min] for d in datas]).astype(np.float64)
    labs, names = _remap([lab[:n_min] for lab in labs])
    if code_names:
        names = [code_names.get(int(float(n)), n) for n in names]
    times = tmin + np.arange(data.shape[-1]) / sfreq
    if crop:
        idx = np.where((times >= crop[0] - 1e-9) & (times <= crop[1] + 1e-9))[0]
        if idx.size == 0:
            raise ValueError(f"截取时间段 {crop} 不在数据范围内")
        data = data[..., idx]
        tmin = times[idx[0]]
    dd = DecodingData(data, np.stack(labs), names, float(sfreq), float(tmin), label_name)
    log("解码数据读取完成：" + dd.summary().replace("<br>", "；").replace("<b>", "").replace("</b>", ""))
    return dd


def window_times(dd, n_windows, time_win, time_step):
    return dd.tmin + (np.arange(n_windows) * time_step + time_win / 2) / dd.sfreq


def smooth(x, size=5):
    return uniform_filter1d(x, size=size, axis=-1, mode="nearest")


# ---------------------------------------------------------------- 解码
def tbyt_decoding(dd, navg, time_win, time_step, nfolds, nrepeats, normalization=False,
                  pca=False, n_jobs=1, do_smooth=True):
    from neurora.decoding import tbyt_decoding_kfold
    acc = tbyt_decoding_kfold(dd.data, dd.labels, n=dd.n_classes, navg=navg, time_win=time_win,
                              time_step=time_step, nfolds=nfolds, nrepeats=nrepeats,
                              normalization=normalization, pca=pca,
                              parallel=n_jobs > 1, n_jobs=n_jobs, backend="threading")
    acc = np.asarray(acc)
    return smooth(acc) if do_smooth else acc


def ct_decoding(dd, navg, time_win, time_step, nfolds, nrepeats, normalization=False,
                pca=False, n_jobs=1, do_smooth=True):
    from neurora.decoding import ct_decoding_kfold
    from scipy.ndimage import uniform_filter
    acc = ct_decoding_kfold(dd.data, dd.labels, n=dd.n_classes, navg=navg, time_win=time_win,
                            time_step=time_step, nfolds=nfolds, nrepeats=nrepeats,
                            normalization=normalization, pca=pca,
                            parallel=n_jobs > 1, n_jobs=n_jobs, backend="threading")
    acc = np.asarray(acc)
    return uniform_filter(acc, size=(1, 3, 3), mode="nearest") if do_smooth else acc


def stats_mask_1d(x, level, times, stats_time, p, clusterp, n_iter, use_cluster=True):
    """x: [被试, 时间]，单侧检验 x > level。只在 stats_time 区间内检验。"""
    from neurora.stuff import clusterbased_permutation_1d_1samp_1sided
    from scipy.stats import ttest_1samp
    mask = np.zeros(x.shape[1])
    idx = np.where((times >= stats_time[0]) & (times <= stats_time[1]))[0]
    if idx.size < 2:
        return mask
    if use_cluster:
        warn_small_n(x.shape[0], clusterp)
        mask[idx] = clusterbased_permutation_1d_1samp_1sided(
            x[:, idx], level=level, p_threshold=p, clusterp_threshold=clusterp, n_iter=n_iter)
    else:
        _, pv = ttest_1samp(x[:, idx], level, axis=0, alternative="greater")
        mask[idx] = pv < p
    return mask


def stats_mask_2d(x, level, times, stats_time, p, clusterp, n_iter):
    from neurora.stuff import clusterbased_permutation_2d_1samp_1sided
    mask = np.zeros(x.shape[1:])
    idx = np.where((times >= stats_time[0]) & (times <= stats_time[1]))[0]
    if idx.size < 2:
        return mask
    sub = x[:, idx][:, :, idx]
    warn_small_n(x.shape[0], clusterp)
    mask[np.ix_(idx, idx)] = clusterbased_permutation_2d_1samp_1sided(
        sub, level=level, p_threshold=p, clusterp_threshold=clusterp, n_iter=n_iter)
    return mask


def plot_timecourse(x, times, mask=None, chance=None, ylim=None, ylabel="解码正确率",
                    title="", color=COLORS[1]):
    """手册 plot_tbyt_decoding_acc / plot_tbytsim_withstats 的样式。"""
    fig = Figure(figsize=(8, 4.2), layout="constrained")
    ax = fig.subplots()
    avg, err = x.mean(0), sem(x, ddof=1) if x.shape[0] > 1 else np.zeros(x.shape[1])
    ax.fill_between(times, avg - err, avg + err, color=color, alpha=0.35, lw=0)
    ax.plot(times, avg, color=color, lw=1.8)
    if ylim:
        ax.set_ylim(*ylim)
    y0, y1 = ax.get_ylim()
    base = chance if chance is not None else 0
    if mask is not None:
        dt = times[1] - times[0] if len(times) > 1 else 0.01
        for k in np.where(mask != 0)[0]:
            ax.plot(times[k], y0 + 0.95 * (y1 - y0), "s", color=color, ms=3, alpha=0.8)
            ax.fill_between([times[k] - dt / 2, times[k] + dt / 2], base, avg[k] - err[k],
                            color=color, alpha=0.12, lw=0)
    ax.set_ylim(y0, y1)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.axvline(0, color="k", lw=0.8)
    if chance is not None:
        ax.axhline(chance, color="k", lw=0.8, ls="--")
        ax.text(times[-1], chance, " 机会水平", va="bottom", ha="right", fontsize=8)
    else:
        ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("时间 (s)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    return fig


def plot_ct(acc_mean, times, mask=None, clim=None, title=""):
    fig = Figure(figsize=(6.2, 5.2), layout="constrained")
    ax = fig.subplots()
    clim = clim or (np.percentile(acc_mean, 2), np.percentile(acc_mean, 98))
    im = ax.imshow(acc_mean, origin="lower", cmap="viridis", aspect="auto",
                   extent=[times[0], times[-1], times[0], times[-1]], vmin=clim[0], vmax=clim[1])
    if mask is not None and np.any(mask):
        ax.contour(times, times, mask, [0.5], colors="white", linewidths=1.5, linestyles="dashed")
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("解码正确率")
    ax.set_xlabel("测试时间 (s)")
    ax.set_ylabel("训练时间 (s)")
    ax.set_title(title)
    return fig


# ---------------------------------------------------------------- RSA
def condition_data(dd):
    """[被试, 试次, …] → [条件, 被试, 每条件试次, 通道, 时间]（取各被试各条件的最少试次）。"""
    n_sub = dd.data.shape[0]
    counts = [[int(np.sum(dd.labels[s] == c)) for c in range(dd.n_classes)] for s in range(n_sub)]
    n_min = int(np.min(counts))
    if n_min < 1:
        raise ValueError("有被试缺少某个类别的试次，无法计算 RDM")
    out = np.zeros((dd.n_classes, n_sub, n_min) + dd.data.shape[2:])
    for s in range(n_sub):
        for c in range(dd.n_classes):
            out[c, s] = dd.data[s, dd.labels[s] == c][:n_min]
    return out, n_min


def model_rdm(kind, n, path=None):
    i, j = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    diff = np.abs(i - j).astype(float)
    if kind == "环形距离（如朝向、位置）":
        half = n / 2
        return np.where(diff <= half, diff, n - diff) / half
    if kind == "线性距离":
        return diff / max(n - 1, 1)
    if kind == "类别是否相同（0/1）":
        return (diff > 0).astype(float)
    if kind == "从文件读取":
        if path.lower().endswith(".npy"):
            m = np.load(path)
        else:
            m = np.loadtxt(path, delimiter="," if path.lower().endswith(".csv") else None)
        if m.shape != (n, n):
            raise ValueError(f"模型 RDM 应为 {n}×{n}，文件里是 {m.shape}")
        return m
    raise ValueError(kind)


def rsa(dd, model, time_win, time_step, method="correlation"):
    from neurora.corr_cal_by_rdm import rdms_corr
    from neurora.rdm_cal import eegRDM
    cd, n_min = condition_data(dd)
    rdms = eegRDM(cd, chl_opt=0, time_opt=1, time_win=time_win, time_step=time_step, method=method)
    sims = rdms_corr(np.asarray(model)[None], rdms)  # [被试, 时间窗, 模型, (r, p)]
    r = np.asarray(sims)[..., 0, 0]
    return rdms, r, n_min


def plot_rdm(rdm, names, title="", percentile=True):
    fig = Figure(figsize=(5.6, 5), layout="constrained")
    ax = fig.subplots()
    m = np.asarray(rdm, float)
    if percentile:
        from scipy.stats import rankdata
        m = rankdata(m).reshape(m.shape) / m.size * 100
    im = ax.imshow(m, cmap="jet", vmin=0, vmax=100 if percentile else None)
    n = len(names)
    step = max(1, n // 16)
    ax.set_xticks(range(0, n, step), names[::step], rotation=90, fontsize=8)
    ax.set_yticks(range(0, n, step), names[::step], fontsize=8)
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("差异百分位" if percentile else "差异")
    ax.set_title(title)
    return fig


# ---------------------------------------------------------------- IEM
def iem(dd, time_win, nfolds, is_circular, num_perm, progress=None):
    from inverted_encoding import IEM, circ_diff, permutation
    n_sub, _, _, n_t = dd.data.shape
    n_w = n_t // time_win
    stim_max = int(dd.n_classes)
    # inverted_encoding 默认 9 个基函数、宽度 42*stim_max//360；类别少于 9 时宽度会变成 0，
    # 矩阵奇异。此时改用 stim_max 个基函数、宽度 1。16 类（手册）时仍用默认值。
    extra = {}
    if stim_max < 9:
        extra = dict(nchannels=stim_max, channel_sd=1)
    mae = np.zeros([n_sub, n_w])
    for t in range(n_w):
        for s in range(n_sub):
            x = dd.data[s, :, :, t * time_win:(t + 1) * time_win].mean(axis=2)
            y = dd.labels[s].astype(int)
            pred, _, _, _ = IEM(x, y, stim_max=stim_max, nfolds=nfolds, is_circular=is_circular, **extra)
            mae[s, t] = np.mean(np.abs(circ_diff(pred, y, stim_max)))
        if progress:
            progress(f"IEM：时间窗 {t + 1}/{n_w}")
    null = permutation(dd.labels[-1].astype(int), stim_max=stim_max, num_perm=num_perm,
                       is_circular=is_circular)
    times = dd.tmin + (np.arange(n_w) * time_win + time_win / 2) / dd.sfreq
    return times, mae, np.asarray(null)


def iem_stats(mae, null, p=0.05):
    """与手册 plot_iem_results 相同：每个时间窗把被试 MAE 和置换分布做单侧独立样本 t 检验。"""
    ps = np.array([ttest_ind(mae[:, t], null, alternative="less")[1] for t in range(mae.shape[1])])
    return (ps < p).astype(float), ps


def plot_iem(times, mae, null, mask, ylim=None, title=""):
    delta = np.mean(null) - smooth(mae)
    return plot_timecourse(delta, times, mask=mask, chance=None, ylim=ylim,
                           ylabel="ΔMAE（置换均值 − 实际 MAE）", title=title)
