"""事件：和数据分开保存、可以随时添加和整理。

一条记录的事件是一张表：onset（距记录开头的秒数）、duration（秒）、description（事件名）。
来源可以是数据自带的标记、外部表格、某个通道里的数值、固定间隔，或手动添加。
名称以 BAD_ 开头的事件表示坏段，分段时会自动跳过。
"""
import fnmatch
import os
import re
import unicodedata

import numpy as np
import pandas as pd

COLS = ["onset", "duration", "description"]
TABLE_FILTER = "表格 (*.csv *.tsv *.txt *.xlsx *.xls);;所有文件 (*)"
TABLE_EXT = (".csv", ".tsv", ".txt", ".xlsx", ".xls")
TIME_UNITS = ["秒", "毫秒", "采样点", "Unix 时间戳（秒）", "Unix 时间戳（毫秒）"]
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_TIME_UNIT = r"milliseconds?|msecs?|ms|毫秒|seconds?|secs?|s|秒|minutes?|mins?|分钟|采样点|samples?"
_CLOCK = r"[+-]?(?:\d+:)?\d+:[0-5]\d(?:\.\d+)?"
_TIME = rf"(?:{_CLOCK}|{_NUMBER}(?:[ \t]*(?:{_TIME_UNIT}))?)"


def time_seconds(value, unit="秒", sfreq=None):
    """Parse elapsed time; an explicit unit in a cell takes precedence over its column unit."""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower().replace("−", "-")
    if re.fullmatch(_CLOCK, text):
        parts = text.lstrip("+-").split(":")
        if len(parts) == 3 and int(parts[1]) >= 60:
            raise ValueError(f"小时后的分钟应为 0–59：{text}")
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
        return -total if text.startswith("-") else total
    match = re.fullmatch(rf"({_NUMBER})\s*({_TIME_UNIT})?", text, re.I)
    if not match:
        raise ValueError(f"无法识别时间“{value}”，可填 1.5 秒、250 ms 或 01:02.5")
    number = float(match[1])
    if not np.isfinite(number):
        raise ValueError(f"时间必须是有限数值：{value}")
    scale = match[2] or unit
    if scale in ("毫秒", "ms", TIME_UNITS[4]) or scale.startswith(("msec", "millisecond")):
        return number / 1000
    if scale == "分钟" or scale.startswith("min"):
        return number * 60
    if scale == "采样点" or scale.startswith("sample"):
        if not sfreq or sfreq <= 0:
            raise ValueError("采样点需要记录的采样率才能换算为秒")
        return number / sfreq
    return number


def time_list(text):
    text = unicodedata.normalize("NFKC", text).strip().replace("−", "-")
    if not re.fullmatch(rf"{_TIME}(?:[\s,、;]+{_TIME})*", text, re.I):
        raise ValueError("请填写一个或多个时间，例如 100ms、0.4 秒")
    return [time_seconds(match[0]) for match in re.finditer(_TIME, text, re.I)]


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
    df = df[COLS + [c for c in df.columns if c not in COLS]].copy()
    df["onset"] = df["onset"].astype(float).round(6)
    df["duration"] = df["duration"].fillna(0).astype(float).round(6)
    df["description"] = df["description"].astype(str)
    return df.sort_values("onset", kind="stable").reset_index(drop=True)


def merge(a, b):
    return tidy(pd.concat([a, b], ignore_index=True)) if len(a) else tidy(b)


def load(path):
    if not path or not os.path.exists(path):
        return empty()
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return tidy(df)


def save(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tidy(df).to_csv(path, index=False, encoding="utf-8-sig")


def from_raw(raw):
    """数据自带的标记 → 事件表（时间换成距数据开头的秒数）。"""
    ann = raw.annotations
    if not len(ann):
        return empty()
    return make(ann.onset - raw.first_time, ann.description, ann.duration)


def to_annotations(df):
    import mne
    df = tidy(df)
    return mne.Annotations(df["onset"].values, df["duration"].values, df["description"].tolist(), orig_time=None)


def editable_annotations(raw, table):
    """Keep source row identities as annotations are reordered or clipped by the viewer."""
    import mne
    table = tidy(table)
    annotations = mne.Annotations(table.onset, table.duration, table.description.tolist(),
                                  extras=[{"biap_row": i} for i in table.index])
    raw.set_annotations(annotations)
    return wave_rows(raw)


def wave_rows(raw):
    return [(ann["extras"].get("biap_row"), round(float(ann["onset"] - raw.first_time), 6),
             round(float(ann["duration"]), 6), str(ann["description"])) for ann in raw.annotations]


def wave_edits(table, opened, edited):
    """Preserve extra columns and rows outside the visible time range when saving plot edits."""
    table = tidy(table)
    original = {i: (start, dur, name) for i, start, dur, name in opened}
    observed = {i for i, *_ in edited if i is not None}
    removed = set(original) - observed
    keep = [row for i, row in enumerate(table.to_dict("records")) if i not in original]
    records = table.to_dict("records")
    for idx, start, dur, name in edited:
        # The MNE browser recreates an annotation when resizing a region.
        if idx is None:
            matches = [i for i in removed if original[i][2] == name and
                       start <= original[i][0] + original[i][1] and original[i][0] <= start + dur]
            if len(matches) == 1:
                idx = matches[0]
                removed.remove(idx)
        row = dict(records[idx]) if idx is not None else dict.fromkeys(table.columns, "")
        if idx is None or (start, dur, name) != original[idx]:
            row.update(onset=start, duration=dur, description=name)
        keep.append(row)
    return tidy(pd.DataFrame(keep, columns=table.columns))


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


_TIME_KEYS = ("onset", "latency", "开始", "起点", "起始", "sample", "采样点", "start", "timestamp", "time", "时刻", "时间", "秒")
_LABEL_KEYS = ("marker_value", "description", "trial_type", "事件名", "事件类型", "条件", "label", "value", "code", "trigger", "event", "type", "name",
               "condition", "事件", "标记", "条件", "名称")
_DUR_KEYS = ("duration", "时长", "持续", "dur")


def column_unit(name):
    name = unicodedata.normalize("NFKC", str(name)).lower()
    if "毫秒" in name or "millisec" in name or "msec" in name or re.search(r"(?<![a-z])ms(?![a-z])|(?<=onset)ms\b", name):
        return "毫秒"
    if "采样点" in name or "sample" in name:
        return "采样点"
    if "秒" in name or "second" in name or re.search(r"(?<![a-z])(?:sec|s)(?![a-z])", name):
        return "秒"
    return ""


def guess_columns(df):
    """猜哪一列是时间、事件名、时长。"""
    cols = list(df.columns)
    low = [unicodedata.normalize("NFKC", c).strip().lower() for c in cols]
    sample = df.head(100)
    num = [c for c in cols if pd.to_numeric(sample[c], errors="coerce").notna().mean() > 0.9]

    def pick(keys, pool):
        for k in keys:
            for c, lc in zip(cols, low):
                if c in pool and k in lc:
                    return c
        return ""
    dur = pick(_DUR_KEYS, cols)
    candidates = [c for c in cols if c != dur]
    t = pick(_TIME_KEYS, candidates) or next((c for c in num if c != dur), "")
    other = [c for c in candidates if c != t]
    lab = pick(_LABEL_KEYS, other) or next(iter(other), "")
    unit = column_unit(t) or "秒"
    if t:
        v = pd.to_numeric(sample[t], errors="coerce").dropna()
        if len(v) and v.median() > 1e11 and unit != "采样点":
            unit = TIME_UNITS[4]
        elif len(v) and v.median() > 1e8 and unit == "秒":
            unit = "Unix 时间戳（秒）"
    return dict(time_col=t, label_col=lab, dur_col=dur, unit=unit)


def table_to_events(tab, spec, sfreq, rec_start_unix=None, rec_duration=None, log=print):
    """spec: dict(time_col, label_col, dur_col, unit, offset)。返回事件表。"""
    if spec.get("time_col") not in tab.columns:
        raise ValueError("请选择表格中的起始时间列")
    unit = spec.get("unit", "秒")
    tab = tab.reset_index(drop=True)
    tab = tab.loc[tab[spec["time_col"]].notna() & tab[spec["time_col"]].astype(str).str.strip().ne("")]
    if tab.empty:
        raise ValueError(f"“{spec['time_col']}”列没有起始时间，请选择有数据的列")

    def seconds_column(col, scale, fill_blank=False):
        if pd.api.types.is_numeric_dtype(tab[col]):
            numeric = tab[col].fillna(0).to_numpy(dtype=float)
            if not np.isfinite(numeric).all():
                raise ValueError(f"“{col}”列存在无效数值，请检查后再导入")
            return numeric * time_seconds(1, scale, sfreq)
        values = []
        for i, cell in zip(tab.index, tab[col]):
            try:
                values.append(0.0 if fill_blank and (pd.isna(cell) or str(cell).strip() == "") else
                              time_seconds(cell, scale, sfreq))
            except ValueError as exc:
                raise ValueError(f"“{col}”列（数据行 {i+1}）：{exc}") from exc
        return np.asarray(values, dtype=float)

    t = seconds_column(spec["time_col"], unit)
    if unit.startswith("Unix"):
        if rec_start_unix is None:
            raise ValueError("这条数据没有记录开始时间，不能用时间戳对齐，请换成“秒”并填写偏移")
        t = t - rec_start_unix
        if rec_duration:
            t = _fix_timezone(t, rec_duration, log)
    labels = [fmt_value(v) for v in tab[spec["label_col"]]] if spec.get("label_col") else ["事件"] * len(t)
    dur_col = spec.get("dur_col")
    dur_unit = spec.get("dur_unit") or column_unit(dur_col) or ("毫秒" if "毫秒" in unit else "秒")
    dur = seconds_column(dur_col, dur_unit, fill_blank=True) if dur_col else np.zeros(len(t))
    t = t + float(spec.get("offset") or 0)
    df = pd.DataFrame({"onset": t, "description": labels, "duration": dur})
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
    """时间列表或逐条“时间 名称”；用等号显式指定数字事件名。"""
    on, nm = [], []
    for raw_line in re.split(r"[;；\n]", text or ""):
        line = raw_line.strip().replace("：", ":")
        if not line:
            continue
        try:
            vals = time_list(line)
        except ValueError:
            vals = None
        if vals is not None:
            on.extend(vals)
            nm.extend([default_name] * len(vals))
            continue
        match = re.fullmatch(rf"({_TIME})\s*(?:[=＝,，、]|\s)\s*(\S.*)", line, re.I)
        if not match:
            raise ValueError(f"无法识别“{line}”。例如 500ms 刺激、01:02.5 反应，数字事件名可写 1.5秒=10")
        on.append(time_seconds(match[1]))
        nm.append(match[2])
    if not on:
        raise ValueError("没有填写时间")
    return make(on, nm)


# ---------------------------------------------------------------- 整理
def parse_rules(text):
    """改名规则，每条 “旧=新”，用逗号或换行分开。

    旧名里 x 代表任意一位数字（9xx 匹配 900–999），* 代表任意文字；不写通配符就是精确匹配。
    """
    rules = []
    for part in re.split(r"[,，、;；\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        fields = re.split(r"->|→|改为|改成|改名为|[=＝]", part, maxsplit=1)
        if len(fields) != 2:
            raise ValueError(f"请说明要把“{part}”改成什么，例如 9xx 改为 刺激出现")
        old, new = [x.strip() for x in fields]
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
