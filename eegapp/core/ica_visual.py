"""Generate review figures for selected components of one fitted ICA."""
import mne
import numpy as np
from matplotlib.figure import Figure
from scipy.signal import welch


def standardized(signal):
    valid = np.isfinite(signal)
    result = np.full_like(signal, np.nan)
    if valid.any():
        values = signal[valid]
        result[valid] = (values - values.mean()) / (values.std() or 1)
    return result


def draw_spectrum(ax, signal, sfreq):
    valid = np.isfinite(signal)
    edges = np.flatnonzero(np.diff(np.r_[False, valid, False].astype(int))).reshape(-1, 2)
    window = min(len(signal), max(4, round(2 * sfreq)))
    spectra, weights = [], []
    for start, end in edges:
        length = end - start
        if length >= window:
            freqs, power = welch(signal[start:end], fs=sfreq, nperseg=window)
            spectra.append(power)
            weights.append(1 + (length - window) // (window - window // 2))
    if spectra:
        average = np.average(spectra, axis=0, weights=weights)
        ax.semilogy(freqs, np.maximum(average, 1e-24), color="#255b91")
    else:
        ax.text(.1, .5, "Insufficient clean interval", transform=ax.transAxes)
    ax.set(xlabel="Frequency (Hz)", ylabel="PSD (a.u./Hz)", title="Spectrum across clean intervals")


def evidence(raw, ica, picks, folder):
    sources = ica.get_sources(raw)
    sfreq = float(raw.info["sfreq"])
    topo_info = mne.pick_info(raw.info, [raw.ch_names.index(c) for c in ica.ch_names])
    maps = ica.get_components()
    refs = mne.pick_types(raw.info, meg=False, eog=True, ecg=True)
    references = raw.get_data(picks=refs, reject_by_annotation="NaN") if len(refs) else []
    result = []
    for idx in picks:
        signal = sources.get_data(picks=[idx], reject_by_annotation="NaN")[0]
        valid = np.isfinite(signal)
        if valid.sum() < 4:
            raise ValueError(f"IC {idx} 有效样本不足")
        fig = Figure(figsize=(10, 6), facecolor="white", layout="constrained")
        grid = fig.add_gridspec(2, 2, width_ratios=(1, 1.8))
        scalp = fig.add_subplot(grid[:, 0])
        spectrum = fig.add_subplot(grid[0, 1])
        trace = fig.add_subplot(grid[1, 1])
        try:
            mne.viz.plot_topomap(maps[:, idx], topo_info, axes=scalp, show=False, contours=5, cmap="RdBu_r")
            scalp.set_title("Scalp map")
        except (ValueError, RuntimeError):
            scalp.clear()
            scalp.axis("off")
            scalp.text(.5, .5, "Scalp map unavailable", ha="center", transform=scalp.transAxes, color="black")
        draw_spectrum(spectrum, signal, sfreq)
        peak = int(np.nanargmax(np.abs(np.where(valid, signal, np.nan) - np.median(signal[valid]))))
        width = max(1, min(len(signal), round(sfreq * 10)))
        start = max(0, min(peak - width // 2, len(signal) - width))
        segment = slice(start, start + width)
        times = np.arange(start, start + width) / sfreq
        trace.plot(times, standardized(signal)[segment], label=f"IC {idx}", color="#255b91", lw=1.1)
        correlations = []
        for ref, y in zip(refs, references):
            mask = valid & np.isfinite(y)
            if mask.sum() > 3 and np.std(y[mask]) and np.std(signal[mask]):
                correlations.append((float(np.corrcoef(signal[mask], y[mask])[0, 1]), ref, y))
        if correlations:
            r, ref, y = max(correlations, key=lambda v: abs(v[0]))
            trace.plot(times, standardized(y)[segment], label=f"{raw.ch_names[ref]} (r={r:.2f})",
                       color="#b25813", alpha=.8, lw=.9)
        trace.legend(fontsize=8, labelcolor="black", facecolor="white")
        trace.set(xlabel="Recording time (s)", ylabel="z score", title="Excerpt around largest clean excursion")
        for ax in (scalp, spectrum, trace):
            ax.set_facecolor("white")
            ax.tick_params(colors="#222222")
            for label in (ax.xaxis.label, ax.yaxis.label, ax.title, *ax.texts):
                label.set_color("#222222")
        fig.suptitle(f"ICA component {idx}  |  {sfreq:g} Hz  |  passband "
                     f"{raw.info['highpass']:g}–{raw.info['lowpass']:g} Hz", color="black")
        path = folder / f"IC-{idx:03d}.png"
        fig.savefig(path, dpi=145, facecolor="white")
        fig.clear()
        result.append(path)
    return result
