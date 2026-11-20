"""把可引用的工作窗口参数和事件表映射为 AI 的有版本提案。"""
import json
import math
import os
import tempfile
from copy import copy, deepcopy
from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit
from ..core import project as pj, events as ev, workbench as wb

# 界面字段名、Pipeline 属性、窗口控件。
PRE = {
    "陷波": [("频率Hz","notch","notch"),("去除谐波","harmonics","harm")],
    "高低通": [("高通Hz","l_freq","lf"),("低通Hz","h_freq","hf")],
    "重采样": [("目标采样率Hz","resample","rs")],
    "坏道插值": [("启用","interpolate","interp")],
    "重参考": [("方式","reref","reref"),("参考通道","reref_channels","reref_ch")],
    "ICA": [("启用","ica","ica_on"),("算法","ica_method","ica_m"),("自动识别眼电","ica_auto_eog","ica_auto")],
    "事件分段": [("方式","epoch_mode","mode"),("开始秒","tmin","tmin"),("结束秒","tmax","tmax"),
             ("基线校正","baseline","bl_on"),("基线起点秒","bl0","b0"),("基线终点秒","bl1","b1"),
             ("固定长度秒","fixed_len","flen"),("重叠秒","fixed_overlap","fov"),("剔除阈值µV","reject_uv","rej")]}
STAT_FIELDS = {"cluster置换检验":"cluster", "p阈值":"p", "置换次数":"niter",
               "检验起点秒":"s0", "检验终点秒":"s1"}
ANALYSIS = {
    "ERP": {"通道":"ch", "显示起点秒":"w0", "显示终点秒":"w1", "指标起点秒":"m0", "指标终点秒":"m1",
            "地形图时刻":"topo", "统计方法":"test", "多重比较":"corr", "p阈值":"alpha",
            "置换次数":"niter", "两因素水平":"levels"},
    "时频": {"通道":"ch", "最低频率Hz":"f0", "最高频率Hz":"f1", "频率步长Hz":"fs", "小波周期除数":"div",
             "基线起点秒":"b0", "基线终点秒":"b1", "显示起点秒":"w0", "显示终点秒":"w1",
             "降采样倍数":"decim", "指标频段下限Hz":"mb0", "指标频段上限Hz":"mb1", "指标起点秒":"mt0",
             "指标终点秒":"mt1", "cluster置换检验":"do_stat", "p阈值":"p", "置换次数":"niter"},
    "解码": {"平均试次数":"navg", "时间窗采样点":"win", "步长采样点":"step", "折数":"folds",
            "方法":"method", "重复次数":"reps", "IEM环形特征":"circ", "IEM置换次数":"nperm",
            "并行线程":"jobs", **STAT_FIELDS},
    "RSA": {"时间窗采样点":"win", "步长采样点":"step", "模型RDM":"kind", "模型文件":"mfile",
            "RDM距离":"dist", **STAT_FIELDS},
}
# Previously exposed parameters used their display labels on disk. All other controls
# already have values in 2.5.5 projects under their widget attribute names.
_LABEL_KEYS = {"ERP":{"通道", "指标起点秒", "指标终点秒"},
               "时频":{"通道", "最低频率Hz", "最高频率Hz", "频率步长Hz"},
               "解码":{"平均试次数", "时间窗采样点", "步长采样点", "折数"},
               "RSA":{"时间窗采样点", "步长采样点"}}


def analysis_key(step, field):
    return field if field in _LABEL_KEYS[step] else ANALYSIS[step][field]

TABULAR={".csv",".tsv",".xlsx",".xls"}
TEXT={".txt",".md",".json",".pdf",".docx"}

def saved_parameters(path, target=""):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pipeline = data["pipeline"]
    analysis = data.get("assistant_data", {}).get("analysis", {})
    values = {name: {label: pipeline[attr] for label, attr, _ in fields if attr in pipeline}
              for name, fields in PRE.items()}
    for name, fields in ANALYSIS.items():
        saved = analysis.get(name, {})
        values[name] = {label: saved[analysis_key(name, label)] for label in fields if analysis_key(name, label) in saved}
    if target and target not in values:
        raise ValueError("没有可核对的步骤参数：" + target)
    return {target: values[target]} if target else values


def requested_fields(proposal):
    after = proposal["after"]["params"]
    return proposal.get("fields", [key for key in after if after[key] != proposal["before"]["params"][key]])


def saved_record_ica(path, rid):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for subject in data["subjects"]:
        for rec in subject.get("recordings", []):
            if rec["id"] == rid:
                return {"记录":rid, "启用ICA":data["pipeline"].get("ica"),
                        "标记方式":"手动" if rec.get("ica_manual") else "自动",
                        "排除成分":rec.get("ica_exclude", []), "处理状态":rec.get("status", pj.REC_RAW)}
    raise ValueError("找不到这条记录，请先在项目目录中核对完整记录名。")


def parameter_receipt(path, proposals):
    expected = {(p["name"], field): p["after"]["params"][field]
                for p in proposals if p["kind"] == "step" for field in requested_fields(p)}
    records = {p["record"] for p in proposals if p["kind"] == "ica"}
    if not expected and not records:
        return ""
    saved = saved_parameters(path) if expected else {}
    for proposal in proposals:
        if proposal["kind"] == "step" and "条件" in requested_fields(proposal):
            scope = proposal["before"].get("condition_scope")
            if scope:
                settings = json.loads(Path(path).read_text(encoding="utf-8")).get("assistant_data", {}).get("analysis", {})
                group, key = _condition_key(scope)
                prefs = settings.get(proposal["name"], {}).get(group, {})
                if key in prefs:
                    saved[proposal["name"]]["条件"] = prefs[key]
    lines = ["已重新读取 project.json："]
    for (name, field), requested in expected.items():
        params = saved[name]
        if field not in params:
            value = "未保存"
        else:
            actual = params[field]
            value = ("开启" if actual else "关闭") if isinstance(actual, bool) else str(actual)
        status = "" if field in params and params[field] == requested else "（与预览不一致）"
        lines.append(f"{name} · {field}：{value}{status}")
    if records:
        for rid in sorted(records):
            record = saved_record_ica(path, rid)
            marks = record["排除成分"]
            listing = "、".join(f"IC-{i:03d}" for i in marks) or "无"
            verified = all((p["component"] in marks) == p["after"] for p in proposals
                           if p["kind"] == "ica" and p["record"] == rid)
            status = "与采用的标记不一致，请复核"
            if verified:
                status = "再次处理这条记录时生效" if record["启用ICA"] else "启用 ICA 并重新处理这条记录后生效"
            lines.append(f"{rid} · ICA 排除清单：{listing}。{status}。")
    return "\n".join(lines)
def read_control(w):
    if isinstance(w,QLineEdit): return w.text()
    if isinstance(w,QCheckBox): return w.isChecked()
    if isinstance(w,QComboBox): return w.currentText()
    return w.value()


def put_control(w,value):
    if isinstance(w,QLineEdit): w.setText(value)
    elif isinstance(w,QCheckBox): w.setChecked(value)
    elif isinstance(w,QComboBox): w.setCurrentText(value)
    else: w.setValue(value)


def validate_control(w,value):
    if isinstance(w,QComboBox) and w.findText(value)<0: raise ValueError(f"未知选项：{value}")
    if hasattr(w,"minimum") and (not math.isfinite(value) or not w.minimum()<=value<=w.maximum()):
        raise ValueError(f"{value} 超出了界面允许的范围")
    if hasattr(w,"decimals") and abs(round(value,w.decimals())-value)>1e-9:
        raise ValueError("数值精度超出工作窗口支持的范围")


def analysis_values(host):
    return {n:{label:read_control(getattr(host.pages[n],field)) for label,field in fields.items()}
            for n,fields in ANALYSIS.items()}


def restore_analysis(host):
    from PySide6.QtCore import QSignalBlocker
    saved=host.project.assistant_data.get("analysis",{})
    for name, fields in ANALYSIS.items():
        for label, field in fields.items():
            value=saved.get(name,{}).get(analysis_key(name, label),host.analysis_defaults[name][label])
            widget = getattr(host.pages[name], field)
            with QSignalBlocker(widget):
                put_control(widget, value)


def _condition_key(scope):
    return ("record_conditions", scope["record"]) if scope["per_record"] else ("conds", scope["subject"])


def steps(host, *, record_analysis=None):
    p=host.project.pipeline
    values=analysis_values(host)
    for name,fields in PRE.items():
        page=host.pages["事件" if name=="事件分段" else "预处理"]
        visible=host.stack.currentWidget() is page
        values[name]={label:read_control(getattr(page,widget)) if visible else getattr(p,attr)
                      for label,attr,widget in fields}
    names=["导入记录","坏段与通道检查",*p.order,"ICA","事件分段",*ANALYSIS,"报告导出"]
    result = [{"name":name,"params":values.get(name,{})} for name in names]
    if record_analysis:
        analysis, project, rid = record_analysis
        owner = host.project.get_rec(rid)[0]
        scoped, _ = project.get_rec(rid)
        if not scoped.epochs_file or len(scoped.recordings) != 1 or scoped.name != owner.name:
            raise ValueError("尚无这条记录的独立分段结果，请先完成分段再选择条件。")
        step = next(s for s in result if s["name"] == analysis)
        if analysis in ("时频", "ERP"):
            counts = project.condition_counts(scoped.name)
            page = host.pages[analysis]
            options, selected = page.condition_menu(counts, scoped.name, rid)
            step["params"]["条件"] = selected
            step["choices"] = {"条件":[name for name, _ in options]}
            step["condition_scope"] = {"subject":scoped.name, "record":rid, "per_record":len(owner.recordings) > 1}
            if analysis == "ERP":
                methods = _record_tests(project, scoped.name, selected)
                step["choices"]["统计方法"] = methods
                preferred = page._preferences().get("test")
                if preferred not in methods:
                    preferred = next((t for t in methods if t.startswith("cluster")), methods[min(1, len(methods)-1)])
                step["params"]["统计方法"] = preferred
            else:
                available = selected and all(counts[scoped.name][c] >= 2 for c in selected)
                preferred = page._preferences().get("do_stat", page._defaults["do_stat"])
                step["params"]["cluster置换检验"] = bool(available and preferred)
        elif analysis in ("解码", "RSA"):
            from .base import label_values
            labels = project.label_columns(scoped.name)
            step["标签"] = labels
            page = host.pages[analysis]
            label = page._preferences().get("label", {}).get(scoped.name, page.label.currentText())
            if label not in labels:
                label = labels[1] if len(labels) > 1 else labels[0]
            names = label_values(project, label, scoped.name)
            step["当前标签"] = label
            step["可用类别数"] = len(names)
            step["可用类别（前80项）"] = names[:80]
            step["编辑入口"] = "工作窗口 → 数据与标签，可选择标签和类别。"
    return result


def _record_tests(project, subject, conditions):
    from ..core import stats
    counts = project.condition_counts(subject)[subject]
    tests = stats.tests_for(len(conditions), "trial") if conditions and all(counts.get(c, 0) >= 2 for c in conditions) else []
    return ["不做检验", *tests]


def event_table(p, rec):
    import pandas as pd
    path=Path(p.events_path(rec))
    if path.is_file():
        df=pd.read_csv(path,keep_default_na=False,dtype=str,nrows=wb.MAX_ROWS+1)
        for c in ("onset","duration"): df[c]=pd.to_numeric(df[c]).astype(float)
    else:
        from ..core import reading
        raw=reading.read_header(rec.source)
        try: df=ev.from_raw(raw)
        finally: raw.close()
    return wb.valid_table(list(df.columns),df.values.tolist())


def normalized_events(table):
    import pandas as pd
    if not set(ev.COLS)<=set(table["columns"]):
        raise ValueError("事件表必须保留 onset、duration、description 三列")
    df=pd.DataFrame(table["rows"],columns=table["columns"])
    for col in ("onset","duration"):
        df[col]=pd.to_numeric(df[col],errors="raise")
        if not all(math.isfinite(v) and v>=0 for v in df[col]):
            raise ValueError(f"{col} 应为非负有限秒数")
    if any(not isinstance(v,str) or not v.strip() for v in df.description):
        raise ValueError("事件名应为非空文本")
    for col in set(df.columns)-set(ev.COLS): df[col]=df[col].fillna("").astype(str)
    return ev.tidy(df)


def event_name(rec):
    return "事件表 · "+rec.id


def session_state(host, *, record_analysis=None):
    return {"flow":steps(host, record_analysis=record_analysis),"tables":dict(host.project.assistant_data.get("tables",{})),
            "project":host.project.name,"sortable":list(host.project.pipeline.order)}


def check_flow(state, edited):
    values={s["name"]:s["params"] for s in state["flow"]}
    if "高低通" in edited:
        a = values["高低通"]
        if a["低通Hz"] and a["高通Hz"] >= a["低通Hz"]:
            raise ValueError("低通必须高于高通")
    if "事件分段" in edited:
        a = values["事件分段"]
        if a["方式"] == pj.EPOCH_EVENTS:
            if a["开始秒"] >= a["结束秒"]: raise ValueError("分段起点必须早于终点")
            if a["基线校正"] and not a["开始秒"] <= a["基线起点秒"] <= a["基线终点秒"] <= a["结束秒"]:
                raise ValueError("基线必须位于分段时间窗内")
        elif a["重叠秒"] >= a["固定长度秒"]: raise ValueError("重叠须小于固定长度")
    for name in edited & ANALYSIS.keys():
        for lo, hi in (("显示起点秒","显示终点秒"), ("指标起点秒","指标终点秒"), ("最低频率Hz","最高频率Hz"),
                       ("基线起点秒","基线终点秒"), ("指标频段下限Hz","指标频段上限Hz"), ("检验起点秒","检验终点秒")):
            if lo in values[name] and values[name][lo] > values[name][hi]:
                raise ValueError(f"{name}的{lo}须小于等于{hi}。")


def apply_selected(host, proposals, transient, *, record_analysis=None):
    """先校验全部提案，事件文件写入新版本，最后一次性切换项目清单。"""
    p=host.project
    if host.runner.busy: raise ValueError("当前项目正在计算，请等计算结束再采用修改。")
    if p._saved_bytes is not None and Path(p.json_path).read_bytes()!=p._saved_bytes:
        raise ValueError("磁盘上的项目已变化，请重新打开并生成预览。")
    state=session_state(host, record_analysis=record_analysis)
    events={event_name(r):r for _,r in p.recordings()}
    state["tables"]={**transient, **state["tables"]}
    before=deepcopy(state["flow"])
    updated_tables={}
    marks = None
    for prop in proposals:
        if prop["kind"] == "ica":
            page = host.pages["预处理"]
            subj, record = page.check_fit(prop["fit"])
            idx = prop["component"]
            if record.id != prop["record"] or not 0 <= idx < page.ica.n_components_:
                raise ValueError("成分与当前记录不匹配，请重新查看成分图。")
            if marks is None:
                marks = page.marked_components()
            if wb.stamp(idx in marks) != prop["base"]:
                raise ValueError("该成分标记已变化，请重新生成建议。")
            if prop["after"]:
                marks.add(idx)
            else:
                marks.discard(idx)
            continue
        name=prop["name"]
        if prop.get("event_source"):
            source = p.get_rec(prop["event_source"])[1]
            if wb.stamp(event_table(p, source)) != prop["source_base"]:
                raise ValueError("来源记录的事件已变化，请重新生成预览。")
        if prop["kind"]=="table" and name in events and name not in updated_tables:
            state["tables"][name]=event_table(p,events[name])
        wb.apply_proposal(state,prop)
        if prop["kind"]=="table": updated_tables[name]=prop["after"]
    check_flow(state, {prop["name"] for prop in proposals if prop["kind"] == "step"})
    staged=copy(p)
    staged.pipeline=deepcopy(p.pipeline)
    staged.subjects=deepcopy(p.subjects)
    staged.assistant_data=deepcopy(p.assistant_data)
    old={s["name"]:s["params"] for s in before}
    requested = {(p["name"], field) for p in proposals if p["kind"] == "step" for field in requested_fields(p)}
    order=[s["name"] for s in state["flow"] if s["name"] in pj.STEPS]
    if [s["name"] for s in state["flow"]]!=["导入记录","坏段与通道检查",*order,"ICA","事件分段",*ANALYSIS,"报告导出"]:
        raise ValueError("当前执行引擎支持调整五项连续预处理步骤的顺序；导入、ICA、分段和分析的位置固定。")
    pre_changed=order!=p.pipeline.order
    epoch_changed=False
    staged.pipeline.order=order
    widgets=[]
    for step in state["flow"]:
        name=step["name"]
        for label,value in step["params"].items():
            if value == old[name][label] and (name, label) not in requested:
                continue
            if label == "条件" and name in ("时频", "ERP"):
                choices = step["choices"][label]
                if (not isinstance(value, list) or not value or not all(isinstance(v, str) and v in choices for v in value)
                        or len(set(value)) != len(value) or (name == "时频" and len(value) > 2)):
                    raise ValueError(f"请从当前记录保留的条件中选择{' 1–2 项' if name == '时频' else '一项或多项'}：{choices}")
                group, key = _condition_key(step["condition_scope"])
                prefs = staged.assistant_data.setdefault("analysis", {}).setdefault(name, {})
                prefs.setdefault(group, {})[key] = list(value)
                continue
            if name in ANALYSIS:
                w=getattr(host.pages[name],ANALYSIS[name][label])
                if record_analysis and record_analysis[0] == name == "ERP" and label == "统计方法":
                    _, scoped_project, _ = record_analysis
                    methods = _record_tests(scoped_project, step["condition_scope"]["subject"], step["params"]["条件"])
                    if value not in methods:
                        raise ValueError(f"所选条件可用的统计方法：{'、'.join(methods)}")
                if record_analysis and record_analysis[0] == name == "时频" and label == "cluster置换检验" and value:
                    subject = step["condition_scope"]["subject"]
                    counts = record_analysis[1].condition_counts(subject)[subject]
                    selected = step["params"]["条件"]
                    if not selected or any(counts.get(c, 0) < 2 for c in selected):
                        raise ValueError("这条记录所选的每个条件至少需要两个保留试次才能运行时频置换检验。")
                key = analysis_key(name, label)
                if value == old[name][label] and staged.assistant_data.get("analysis", {}).get(name, {}).get(key) == value:
                    continue
                staged.assistant_data.setdefault("analysis",{}).setdefault(name,{})[key]=value
            else:
                attr,field=next((a,b) for k,a,b in PRE[name] if k==label)
                w=getattr(host.pages["事件" if name=="事件分段" else "预处理"],field)
                if value == old[name][label] and getattr(staged.pipeline, attr) == value:
                    continue
                setattr(staged.pipeline,attr,value)
                if name=="事件分段": epoch_changed=True
                else: pre_changed=True
            if not (record_analysis and name == record_analysis[0] == "ERP" and label == "统计方法"):
                validate_control(w,value)
            widgets.append((w,value))
    dirty=set()
    if marks is not None:
        if pre_changed:
            raise ValueError("本次预处理参数调整会改变 ICA 分解，请先采用参数，再拟合并复核成分。")
        subj, updated = staged.get_rec(record.id)
        updated.ica_manual = True
        updated.ica_exclude = sorted(marks)
        updated.reviewed_ica = pj.ica_identity(page.ica)
        restore = next((p["restore_marks"] for p in proposals if "restore_marks" in p), None)
        if restore and updated.ica_exclude == restore["marks"]:
            updated.ica_manual = not restore["auto"]
            updated.reviewed_ica = restore["identity"]
        updated.status = pj.REC_RAW
        updated.record_epochs_file = ""
        dirty.add(subj.name)
    created=[]
    try:
        for name,table in updated_tables.items():
            if name not in events:
                staged.assistant_data.setdefault("tables",{})[name]=table
                continue
            df=normalized_events(table)
            subj,rec=staged.get_rec(events[name].id)
            folder=Path(p.folder)/"events"/"versions"
            folder.mkdir(parents=True,exist_ok=True)
            fd,path=tempfile.mkstemp(dir=folder,suffix=".csv")
            os.close(fd)
            created.append(Path(path))
            ev.save(df,path)
            rec.events_file=os.path.relpath(path,p.folder)
            rec.record_epochs_file = ""
            dirty.add(subj.name)
        for subj in staged.subjects:
            if subj.recordings and (pre_changed or epoch_changed or subj.name in dirty):
                dirty.add(subj.name)
                subj.status,subj.epochs_file,subj.n_trials=pj.STATUS_RAW,"",{}
                if pre_changed or epoch_changed:
                    for rec in subj.recordings:
                        rec.record_epochs_file = ""
                if pre_changed:
                    for rec in subj.recordings: rec.status=pj.REC_RAW
        staged.save()
    except Exception:
        for f in created: f.unlink(missing_ok=True)
        raise
    p.pipeline,p.assistant_data,p._saved_bytes=staged.pipeline,staged.assistant_data,staged._saved_bytes
    for dest,src in zip(p.subjects,staged.subjects):
        dest.status,dest.epochs_file,dest.n_trials=src.status,src.epochs_file,src.n_trials
        for a,b in zip(dest.recordings,src.recordings):
            a.status,a.events_file=b.status,b.events_file
            a.record_epochs_file = b.record_epochs_file
            a.ica_manual, a.ica_exclude, a.reviewed_ica = b.ica_manual, b.ica_exclude, b.reviewed_ica
    for name in dirty: p._cache.pop(name,None)
    for w,value in widgets: put_control(w,value)
    if ("时频", "cluster置换检验") in requested:
        page = host.pages["时频"]
        page._cluster_requested = staged.assistant_data["analysis"]["时频"]["do_stat"]
        if hasattr(page, "_condition_counts"):
            page._stats_changed()
    if marks is not None:
        page.ica.exclude = sorted(marks)
        page.excl.setText((", ".join(map(str, sorted(marks))) or "无") if updated.ica_manual else "")
        page.refresh_component_maps()
    if pre_changed:
        page=host.pages["预处理"]
        page.raw=page.ica=page.raw_ica=page._sig=None
    return pre_changed, bool(dirty)
