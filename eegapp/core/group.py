"""第 3 章：多被试 ERP 与时频分析（手册第 3 章代码 4–17）。"""
from dataclasses import dataclass, field

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure

from .stats import sem

from ..theme import C, SERIES as COLORS


@dataclass
class Condition:
    name: str
    files: list
    key: str = ""
    subjects: list = field(default_factory=list)  # 每个被试 [试次, 通道, 时间]


@dataclass
class GroupData:
    conditions: list
    sfreq: float
    tmin: float
    ch_names: list
    n_channels: int
    n_times: int

    def times(self):
        return self.tmin + np.arange(self.n_times) / self.sfreq

    def summary(self):
        lines = [f"<b>采样率</b>：{self.sfreq:g} Hz　<b>分段起点</b>：{self.tmin:g} s　"
                 f"<b>通道</b>：{self.n_channels}　<b>时间点</b>：{self.n_times}"
                 f"（{self.tmin:g} 到 {self.times()[-1]:.3f} s）"]
        for c in self.conditions:
            trials = [s.shape[0] for s in c.subjects]
            lines.append(f"　· {c.name}：{len(c.subjects)} 个被试，每人试次 "
                         f"{min(trials)}–{max(trials)}")
        return "<br>".join(lines)


def load_group(conditions, dim_order, sfreq=None, tmin=None, log=print):
    from .arrays import load_condition
    meta_all = None
    for c in conditions:
        c.subjects = []
        for f in c.files:
            data, meta = load_condition(f, c.key, dim_order)
            c.subjects.append(data)
            if meta is not None and meta_all is None:
                meta_all = meta
        log(f"条件 {c.name}：读取 {len(c.files)} 个被试文件")
    shapes = {s.shape[1:] for c in conditions for s in c.subjects}
    if len(shapes) != 1:
        raise ValueError(f"各文件的 通道×时间 形状不一致：{sorted(shapes)}。请检查维度顺序或文件。")
    n_ch, n_t = shapes.pop()
    if meta_all is not None:
        sfreq, tmin, ch_names = meta_all["sfreq"], meta_all["tmin"], meta_all["ch_names"]
    else:
        if not sfreq:
            raise ValueError("数组文件需要填写采样率")
        ch_names = []
    return GroupData(conditions, float(sfreq), float(tmin or 0.0), ch_names, n_ch, n_t)


def _window_idx(times, t0, t1):
    idx = np.where((times >= t0 - 1e-9) & (times <= t1 + 1e-9))[0]
    if idx.size == 0:
        raise ValueError(f"时间段 {t0}–{t1} s 不在数据范围 {times[0]:.3f}–{times[-1]:.3f} s 内")
    return idx


def group_erp(gd, ch_idx, win, baseline):
    """返回 times_win, {条件名: [被试, 时间]}。基线用整段数据里的 baseline 区间。"""
    times = gd.times()
    w = _window_idx(times, *win)
    b = _window_idx(times, *baseline) if baseline else None
    out = {}
    for c in gd.conditions:
        rows = []
        for s in c.subjects:
            erp = s[:, ch_idx, :].mean(axis=(0, 1))
            if b is not None:
                erp = erp - erp[b].mean()
            rows.append(erp[w])
        out[c.name] = np.array(rows)
    return times[w], out


def group_tfr(gd, ch_idx, freqs, cycles_div, baseline, win, decim=1, log=print):
    """逐被试 Morlet 时频 → 平均试次与所选通道 → dB 基线校正（10·log10(P/基线)）。

    返回 freqs, times_win, {条件名: [被试, 频率, 时间]}
    """
    from mne.time_frequency import tfr_array_morlet
    n_cycles = freqs / cycles_div
    times = gd.times()[::decim]
    b = _window_idx(times, *baseline)
    w = _window_idx(times, *win)
    out = {}
    for c in gd.conditions:
        rows = []
        for i, s in enumerate(c.subjects):
            x = s[:, ch_idx, :]
            p = tfr_array_morlet(x, gd.sfreq, freqs, n_cycles=n_cycles, output="power",
                                 decim=decim, verbose=False)
            p = p.mean(axis=(0, 1))  # [频率, 时间]
            base = p[:, b].mean(axis=1, keepdims=True)
            rows.append(10 * np.log10(p / base)[:, w])
            log(f"时频：{c.name} 被试 {i + 1}/{len(c.subjects)}")
        out[c.name] = np.array(rows)
    return freqs, times[w], out


def tfr_cluster(tfr, p, clusterp, n_iter, other=None):
    from neurora.stuff import (clusterbased_permutation_2d_1samp_2sided,
                               clusterbased_permutation_2d_2sided)

    from .stats import warn_small_n
    warn_small_n(tfr.shape[0], clusterp)
    if other is None:
        return clusterbased_permutation_2d_1samp_2sided(
            tfr, 0, p_threshold=p, clusterp_threshold=clusterp, n_iter=n_iter)
    return clusterbased_permutation_2d_2sided(
        tfr, other, p_threshold=p, clusterp_threshold=clusterp, n_iter=n_iter)


# ---------------------------------------------------------------- 出图
def _spines(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(2)
    ax.spines["bottom"].set_linewidth(2)
    ax.axhline(0, color=C["axis"], lw=0.8)
    ax.axvline(0, color=C["axis"], lw=0.8)


def plot_group_erp(times, erps, stat=None, ylim=None, title="", unit="µV", ms=True):
    fig = Figure(figsize=(8, 4.5), layout="constrained")
    ax = fig.subplots()
    t = times * 1000 if ms else times
    for i, (name, x) in enumerate(erps.items()):
        avg, err = x.mean(0), sem(x)
        col = COLORS[i % len(COLORS)]
        ax.fill_between(t, avg - err, avg + err, color=col, alpha=0.2)
        ax.plot(t, avg, color=col, lw=1.8, label=f"{name}（n={x.shape[0]}）")
    if ylim:
        ax.set_ylim(*ylim)
    y0, y1 = ax.get_ylim()
    dt = (t[1] - t[0]) if len(t) > 1 else 1
    if stat is not None:
        if "effects" in stat:
            for j, (name, r) in enumerate(stat["effects"].items()):
                for k in np.where(r["sig"])[0]:
                    ax.axvspan(t[k], t[k] + dt, ymin=0.01 + 0.06 * j, ymax=0.06 + 0.06 * j,
                               color=COLORS[j + 3], alpha=0.6, lw=0)
                ax.plot([], [], color=COLORS[j + 3], lw=6, alpha=0.6, label=name)
        else:
            sig = np.asarray(stat["sig"])
            for k in np.where(sig != 0)[0]:
                ax.axvspan(t[k], t[k] + dt, color=C["sig"], alpha=0.22, lw=0)
            if np.any(sig != 0):
                ax.plot([], [], color=C["sig"], lw=6, alpha=0.5, label="显著时间点")
    ax.set_ylim(y0, y1)
    _spines(ax)
    ax.set_xlabel("时间 (ms)" if ms else "时间 (s)")
    ax.set_ylabel(f"振幅 ({unit})")
    ax.set_title(title)
    ax.legend(frameon=False, fontsize=9)
    return fig


def plot_tfr_map(tfr_mean, freqs, times, mask=None, clim=None, title="", cbar="dB", ms=True):
    fig = Figure(figsize=(7.5, 4.5), layout="constrained")
    ax = fig.subplots()
    t = times * 1000 if ms else times
    clim = clim or (-np.nanmax(np.abs(tfr_mean)), np.nanmax(np.abs(tfr_mean)))
    im = ax.imshow(tfr_mean, cmap="mocha_div", origin="lower", aspect="auto",
                   extent=[t[0], t[-1], freqs[0], freqs[-1]], vmin=clim[0], vmax=clim[1])
    if mask is not None and np.any(mask != 0):
        # 与手册相同：四周补一圈 0，再画 ±0.5 等值线勾出显著区域
        nf, nt = mask.shape
        pad = np.zeros([nf + 2, nt + 2])
        pad[1:nf + 1, 1:nt + 1] = mask
        dt = (t[-1] - t[0]) / max(nt - 1, 1)
        df = (freqs[-1] - freqs[0]) / max(nf - 1, 1)
        x = np.concatenate(([t[0] - dt], np.linspace(t[0], t[-1], nt), [t[-1] + dt]))
        y = np.concatenate(([freqs[0] - df], np.linspace(freqs[0], freqs[-1], nf), [freqs[-1] + df]))
        X, Y = np.meshgrid(x, y)
        if np.any(pad > 0):
            ax.contour(X, Y, pad, [0.5], colors=C["pos"], linewidths=2, linestyles="dashed")
        if np.any(pad < 0):
            ax.contour(X, Y, pad, [-0.5], colors=C["neg"], linewidths=2, linestyles="dashed")
        ax.set_xlim(t[0], t[-1])
        ax.set_ylim(freqs[0], freqs[-1])
    cb = fig.colorbar(im, ax=ax)
    cb.set_label(cbar)
    ax.set_xlabel("时间 (ms)" if ms else "时间 (s)")
    ax.set_ylabel("频率 (Hz)")
    ax.set_title(title)
    return fig


def close_pyplot():
    plt.close("all")
