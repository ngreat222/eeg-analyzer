"""连续 EEG 质量检查。所有时间相对于当前记录开头。"""

import mne
import numpy as np


def inspect_raw(raw, threshold_uv):
    picks = mne.pick_types(raw.info, eeg=True, exclude="bads")
    if not len(picks):
        raise ValueError("没有可供检测的 EEG 通道")
    sf = raw.info["sfreq"]
    width = max(2, round(sf))
    candidates = []
    peaks = []
    for i in range(0, raw.n_times, width):
        j = min(i + width, raw.n_times)
        data = raw.get_data(picks=picks, start=i, stop=j) * 1e6
        if not np.isfinite(data).all():
            reason = "非有限值"
        elif j - i < 2:
            continue
        else:
            spans = np.ptp(data, axis=1)
            peaks.append(float(np.max(spans)))
            if np.any(spans > threshold_uv):
                reason = "高振幅"
            elif np.all(spans < 0.1):
                reason = "全导平坦"
            else:
                continue
        if candidates and candidates[-1][1] == i / sf and candidates[-1][2] == reason:
            candidates[-1][1] = j / sf
        else:
            candidates.append([i / sf, j / sf, reason])
    summary = {"采样率Hz": sf, "参与检测的EEG通道数": len(picks), "时长秒": raw.n_times / sf,
               "检测窗秒": width / sf, "峰峰阈值微伏": threshold_uv,
               "窗口最大峰峰值的中位数微伏": round(float(np.median(peaks)), 2) if peaks else None,
               "候选区间（起止秒与原因）": candidates}
    return candidates, summary
