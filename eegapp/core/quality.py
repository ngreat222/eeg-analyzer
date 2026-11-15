"""连续 EEG 质量检查。所有时间相对于当前记录开头。"""
import json
import os
from urllib.request import Request, urlopen

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


def advise(summary, key):
    """模型可按需请求已向用户展示的质量指标。"""
    messages = [{"role": "system", "content": "你是科研 EEG 质控助手。先用工具读取质量指标，再用中文解释依据、局限和建议复核的区间。"},
                {"role": "user", "content": "检查当前连续记录的预处理设置与坏段候选。"}]
    tool = {"type": "function", "function": {"name": "read_quality_summary",
            "description": "读取本次检测及处理设置", "parameters": {"type": "object", "properties": {}}}}
    def call(**options):
        payload = dict(model=os.getenv("DEEPSEEK_MODEL") or "deepseek-flash",
                       messages=messages, thinking={"type": "disabled"}, **options)
        req = Request("https://api.deepseek.com/chat/completions", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        with urlopen(req, timeout=60) as response:
            return json.load(response)["choices"][0]["message"]
    msg = call(tools=[tool], tool_choice={"type": "function", "function": {"name": "read_quality_summary"}})
    calls = msg.get("tool_calls", [])
    if not calls or any(c["function"]["name"] != "read_quality_summary" for c in calls):
        raise ValueError("模型未成功请求质量摘要，请重试")
    messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
    for c in calls:
        messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(summary, ensure_ascii=False)})
    answer = call().get("content")
    if not answer:
        raise ValueError("模型未返回建议")
    return answer
