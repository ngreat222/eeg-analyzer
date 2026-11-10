"""事件：和数据分开保存、可以随时添加和整理。

一条记录的事件是一张表：onset（距记录开头的秒数）、duration（秒）、description（事件名）。
来源可以是数据自带的标记、外部表格、某个通道里的数值、固定间隔，或手动添加。
名称以 BAD_ 开头的事件表示坏段，分段时会自动跳过。
"""
import fnmatch
import os
import re

import numpy as np
import pandas as pd

COLS = ["onset", "duration", "description"]
TABLE_FILTER = "表格 (*.csv *.tsv *.txt *.xlsx *.xls);;所有文件 (*)"
TABLE_EXT = (".csv", ".tsv", ".txt", ".xlsx", ".xls")
TIME_UNITS = ["秒", "毫秒", "采样点", "Unix 时间戳（秒）"]


# ---------------------------------------------------------------- 基本
def empty():
    return pd.DataFrame({"onset": pd.Series(dtype=float), "duration": pd.Series(dtype=float),
                         "description": pd.Series(dtype=str)})


def make(onsets, names, durations=None):
    n = len(onsets)
    df = pd.DataFrame({"onset": np.asarray(onsets, float),
                       "duration": np.zeros(n) if durations is None else np.asarray(durations, float),
                       "description": [str(x) for x in names]})
    return tidy(df)


def tidy(df):
    df = df[COLS].copy()
    df["onset"] = df["onset"].astype(float).round(6)
    df["duration"] = df["duration"].fillna(0).astype(float).round(6)
    df["description"] = df["description"].astype(str)
    return df.sort_values("onset", kind="stable").reset_index(drop=True)


def merge(a, b):
    return tidy(pd.concat([a, b], ignore_index=True)) if len(a) else tidy(b)


def load(path):
    if not path or not os.path.exists(path):
        return empty()
    df = pd.read_csv(path, dtype={"description": str}, keep_default_na=False)
    return tidy(df) if len(df) else empty()


def save(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tidy(df).to_csv(path, index=False, encoding="utf-8-sig")


def from_raw(raw):
    """数据自带的标记 → 事件表（时间换成距数据开头的秒数）。"""
    ann = raw.annotations
    if not len(ann):
        return empty()
    shift = raw.first_time if ann.orig_time is not None else 0.0
    return make(ann.onset - shift, ann.description, ann.duration)


def to_annotations(df):
    import mne
    df = tidy(df)
    return mne.Annotations(df["onset"].values, df["duration"].values, df["description"].tolist(), orig_time=None)


def summary(df):
    """[(事件名, 个数, 第一次出现的时间)]，按首次出现排序。"""
    if not len(df):
        return []
    g = df.groupby("description", sort=False)["onset"]
    out = [(name, int(s.size), float(s.min())) for name, s in g]
    return sorted(out, key=lambda x: x[2])


def names(df, include_bad=False):
    return [n for n, _, _ in summary(df) if include_bad or not is_bad(n)]


def is_bad(name):
    return str(name).upper().startswith("BAD")


def fmt_value(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v).strip()
    if not np.isfinite(f):
        return ""
    return str(int(f)) if f.is_integer() else f"{f:g}"


# ---------------------------------------------------------------- 从表格导入
def read_table(path, skip=0):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(path, skiprows=skip)
    else:
        last = None
        for enc in ("utf-8-sig", "gbk", "latin1"):
            try:
                df = pd.read_csv(path, sep=None, engine="python", skiprows=skip, encoding=enc)
                break
            except UnicodeDecodeError as e:
                last = e
        else:
            raise last
    df.columns = [str(c).strip() for c in df.columns]
    return df


_TIME_KEYS = ("latency", "onset", "time", "sec", "秒", "时间", "时刻", "timestamp")
_LABEL_KEYS = ("marker_value", "value", "code", "trigger", "event", "type", "label", "description", "name",
               "condition", "事件", "标记", "条件", "名称")
_DUR_KEYS = ("duration", "dur", "时长", "持续")


def guess_columns(df):
    """猜哪一列是时间、事件名、时长。"""
    cols = list(df.columns)
    low = [c.lower() for c in cols]
    num = [c for c in cols if pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.9]

    def pick(keys, pool):
        for k in keys:
            for c, lc in zip(cols, low):
                if c in pool and k in lc:
                    return c
        return ""
    t = pick(_TIME_KEYS, num) or (num[0] if num else "")
    lab = pick(_LABEL_KEYS, [c for c in cols if c != t]) or next((c for c in cols if c != t), "")
    dur = pick(_DUR_KEYS, [c for c in num if c not in (t, lab)])
    unit = "秒"
    if t:
        v = pd.to_numeric(df[t], errors="coerce").dropna()
        if len(v) and v.median() > 1e8:
            unit = "Unix 时间戳（秒）"
        elif "ms" in t.lower() or "毫秒" in t:
            unit = "毫秒"
        elif "sample" in t.lower() or "采样" in t:
            unit = "采样点"
    return dict(time_col=t, label_col=lab, dur_col=dur, unit=unit)


def table_to_events(tab, spec, sfreq, rec_start_unix=None, rec_duration=None, log=print):
    """spec: dict(time_col, label_col, dur_col, unit, offset)。返回事件表。"""
    t = pd.to_numeric(tab[spec["time_col"]], errors="coerce")
    ok = t.notna()
    if not ok.any():
        raise ValueError(f"“{spec['time_col']}”这一列不是数字，换一列作为时间")
    tab, t = tab[ok], t[ok].astype(float).values
    unit = spec.get("unit", "秒")
    if unit == "毫秒":
        t = t / 1000.0
    elif unit == "采样点":
        t = t / sfreq
    elif unit.startswith("Unix"):
        if rec_start_unix is None:
            raise ValueError("这条数据没有记录开始时间，不能用时间戳对齐，请换成“秒”并填写偏移")
        t = t - rec_start_unix
        if rec_duration:
            t = _fix_timezone(t, rec_duration, log)
    labels = [fmt_value(v) for v in tab[spec["label_col"]]] if spec.get("label_col") else ["事件"] * len(t)
    dur = (pd.to_numeric(tab[spec["dur_col"]], errors="coerce").fillna(0).values
           if spec.get("dur_col") else np.zeros(len(t)))
    if unit == "毫秒" and spec.get("dur_col"):
        dur = dur / 1000.0
    t = t + float(spec.get("offset") or 0)
    df = make(t, labels, dur)
    df = df[df["description"] != ""]
    if rec_duration:
        inside = (df["onset"] >= 0) & (df["onset"] <= rec_duration)
        if (~inside).any():
            log(f"有 {int((~inside).sum())} 个事件落在记录时间范围外，已跳过")
        df = df[inside]
    return tidy(df)


def _fix_timezone(t, dur, log):
    """设备记录的开始时间常常少了时区，按整小时平移到落在记录范围内最多的位置。"""
    def hits(x):
        return int(((x >= 0) & (x <= dur)).sum())
    best, best_h = hits(t), 0
    for h in range(-14, 15):
        n = hits(t + h * 3600)
        if n > best:
            best, best_h = n, h
    if best_h:
        log(f"时间戳和记录开始时间差了 {best_h} 小时（时区），已自动校正")
    return t + best_h * 3600


def find_partner_table(data_path, example_data=None, example_table=None):
    """给一条数据找对应的事件表格。

    有示例（某条数据和它的表格）时，按示例的命名规律找，例如 X.edf ↔ X_intervalMarker.csv；
    否则在同一文件夹里找唯一的表格文件。
    """
    folder = os.path.dirname(data_path)
    stem = _stem(data_path)
    if example_data and example_table:
        ex_stem, ex_name = _stem(example_data), os.path.basename(example_table)
        if ex_name.startswith(ex_stem):
            cand = os.path.join(folder, stem + ex_name[len(ex_stem):])
            if os.path.exists(cand):
                return cand
    try:
        tables = [f for f in os.listdir(folder) if f.lower().endswith(TABLE_EXT)]
    except OSError:
        return None
    same = [f for f in tables if f.startswith(stem)]
    if len(same) == 1:
        return os.path.join(folder, same[0])
    if len(tables) == 1:
        return os.path.join(folder, tables[0])
    return None


def _stem(path):
    name = os.path.basename(path)
    return name[: -len(os.path.splitext(name)[1])] if "." in name else name


# ---------------------------------------------------------------- 其他来源
def from_channel(values, sfreq, start_only=True):
    """通道里的数值变化 → 事件。数值从 0（或别的值）变成非 0 的时刻记为一个事件，事件名就是这个数值。"""
    v = np.nan_to_num(np.asarray(values, float))
    v = np.round(v, 6)
    prev = np.r_[0.0, v[:-1]]
    idx = np.flatnonzero((v != 0) & (v != prev)) if start_only else np.flatnonzero(v != 0)
    return make(idx / sfreq, [fmt_value(x) for x in v[idx]])


def fixed_interval(start, end, step, name):
    if step <= 0:
        raise ValueError("间隔要大于 0")
    if end <= start:
        raise ValueError("结束时间要晚于开始时间")
    t = np.arange(start, end + 1e-9, step)
    return make(t, [name] * len(t))


def parse_manual(text, default_name="事件"):
    """每行一个事件：“时间 名称”或“时间, 名称”，名称可省略；也可以一行写多个时间。"""
    on, nm = [], []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^([-+]?\d+(?:\.\d+)?)\s*[,，\t ]\s*(\D.*)$", line)
        if m:
            on.append(float(m.group(1)))
            nm.append(m.group(2).strip())
            continue
        parts = [p for p in re.split(r"[,，\s]+", line) if p]
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            raise ValueError(f"这一行看不懂：“{line}”。写成 “12.5 刺激” 这样") from None
        on += vals
        nm += [default_name] * len(vals)
    if not on:
        raise ValueError("没有填写时间")
    return make(on, nm)


# ---------------------------------------------------------------- 整理
def parse_rules(text):
    """改名规则，每条 “旧=新”，用逗号或换行分开。

    旧名里 x 代表任意一位数字（9xx 匹配 900–999），* 代表任意文字；不写通配符就是精确匹配。
    """
    rules = []
    for part in re.split(r"[,，;；\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        if not re.search(r"[=＝]", part):
            raise ValueError(f"改名规则写法不对：“{part}”，应写成 旧=新，例如 9xx=刺激出现")
        old, new = [x.strip() for x in re.split(r"[=＝]", part, maxsplit=1)]
        if not old or not new:
            raise ValueError(f"改名规则缺少内容：“{part}”")
        if re.fullmatch(r"[0-9xX]+", old) and re.search(r"[xX]", old):
            rx = re.compile("^" + re.sub(r"[xX]", r"\\d", old) + "$")
        else:
            rx = re.compile(fnmatch.translate(old))
        rules.append((rx, new, old))
    return rules


def apply_rules(df, rules):
    """返回 (新表, {旧名: 新名})。每个事件只按第一条匹配的规则改名。"""
    df = df.copy()
    changed = {}

    def rename(n):
        for rx, new, _ in rules:
            if rx.match(n):
                if n != new:
                    changed[n] = new
                return new
        return n
    df["description"] = [rename(n) for n in df["description"]]
    return tidy(df), changed


def shift(df, which, sec):
    df = df.copy()
    m = df["description"].isin(which) if which else np.ones(len(df), bool)
    df.loc[m, "onset"] = df.loc[m, "onset"] + sec
    return tidy(df)


def drop(df, which):
    return tidy(df[~df["description"].isin(which)])


# ---------------------------------------------------------------- 画图
def plot_timeline(df, duration=None, title=None):
    import matplotlib.pyplot as plt
    from ..theme import SERIES, C
    items = summary(df)
    n = max(len(items), 1)
    fig, ax = plt.subplots(figsize=(9, 0.6 + 0.32 * min(n, 24)))
    for i, (name, _, _) in enumerate(items[:24]):
        d = df[df["description"] == name]
        col = C["danger"] if is_bad(name) else SERIES[i % len(SERIES)]
        if (d["duration"] > 0).any():
            ax.broken_barh(list(zip(d["onset"], d["duration"].clip(lower=0.05))), (i - 0.3, 0.6), color=col, alpha=0.45)
        ax.vlines(d["onset"], i - 0.32, i + 0.32, color=col, lw=1.4)
    labels = [f"{nm}  ×{c}" for nm, c, _ in items[:24]]
    ax.set_yticks(range(min(n, 24)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("时间（秒，从记录开头算）")
    if duration:
        ax.set_xlim(0, duration)
    ax.grid(axis="x", alpha=0.3)
    if title:
        ax.set_title(title, loc="left")
    fig.tight_layout()
    # tight_layout 对中文标签宽度估得偏小，按字数再留出左边距
    width = max((sum(1.0 if ord(ch) > 127 else 0.6 for ch in s) for s in labels), default=4)
    fig.subplots_adjust(left=min(0.45, (0.35 + 0.14 * width) / fig.get_figwidth()))
    return fig
