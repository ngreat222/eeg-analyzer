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
ANALYSIS = {"ERP":{"通道":"ch","指标起点秒":"m0","指标终点秒":"m1"},
            "时频":{"通道":"ch","最低频率Hz":"f0","最高频率Hz":"f1","频率步长Hz":"fs"},
            "解码":{"平均试次数":"navg","时间窗采样点":"win","步长采样点":"step","折数":"folds"},
            "RSA":{"时间窗采样点":"win","步长采样点":"step"}}
TABULAR={".csv",".tsv",".xlsx",".xls"}
TEXT={".txt",".md",".json",".pdf",".docx"}
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
    saved=host.project.assistant_data.get("analysis",{})
    for name, fields in ANALYSIS.items():
        for label, field in fields.items():
            value=saved.get(name,{}).get(label,host.analysis_defaults[name][label])
            put_control(getattr(host.pages[name],field),value)


def steps(host):
    p=host.project.pipeline
    values=analysis_values(host)
    for name,fields in PRE.items():
        page=host.pages["事件" if name=="事件分段" else "预处理"]
        visible=host.stack.currentWidget() is page
        values[name]={label:read_control(getattr(page,widget)) if visible else getattr(p,attr)
                      for label,attr,widget in fields}
    names=["导入记录","坏段与通道检查",*p.order,"ICA","事件分段",*ANALYSIS,"报告导出"]
    return [{"name":name,"params":values.get(name,{})} for name in names]


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


def session_state(host):
    return {"flow":steps(host),"tables":dict(host.project.assistant_data.get("tables",{})),
            "project":host.project.name,"sortable":list(host.project.pipeline.order)}


def check_flow(state):
    values={s["name"]:s["params"] for s in state["flow"]}
    a=values["高低通"]
    if a["低通Hz"] and a["高通Hz"]>=a["低通Hz"]: raise ValueError("低通必须高于高通")
    a=values["事件分段"]
    if a["方式"]==pj.EPOCH_EVENTS:
        if a["开始秒"]>=a["结束秒"]: raise ValueError("分段起点必须早于终点")
        if a["基线校正"] and not a["开始秒"]<=a["基线起点秒"]<=a["基线终点秒"]<=a["结束秒"]:
            raise ValueError("基线必须位于分段时间窗内")
    elif a["重叠秒"]>=a["固定长度秒"]: raise ValueError("重叠须小于固定长度")
    if values["ERP"]["指标起点秒"]>values["ERP"]["指标终点秒"]: raise ValueError("ERP 指标时间窗倒置")
    if values["时频"]["最低频率Hz"]>values["时频"]["最高频率Hz"]: raise ValueError("时频频率范围倒置")


def apply_selected(host, proposals, transient):
    """先校验全部提案，事件文件写入新版本，最后一次性切换项目清单。"""
    p=host.project
    if host.runner.busy: raise ValueError("当前项目正在计算，请等计算结束再采用修改。")
    if p._saved_bytes is not None and Path(p.json_path).read_bytes()!=p._saved_bytes:
        raise ValueError("磁盘上的项目已变化，请重新打开并生成预览。")
    state=session_state(host)
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
        if prop["kind"]=="table" and name in events and name not in updated_tables:
            state["tables"][name]=event_table(p,events[name])
        wb.apply_proposal(state,prop)
        if prop["kind"]=="table": updated_tables[name]=prop["after"]
    check_flow(state)
    staged=copy(p)
    staged.pipeline=deepcopy(p.pipeline)
    staged.subjects=deepcopy(p.subjects)
    staged.assistant_data=deepcopy(p.assistant_data)
    old={s["name"]:s["params"] for s in before}
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
            if value==old[name][label]: continue
            if name in ANALYSIS:
                w=getattr(host.pages[name],ANALYSIS[name][label])
                staged.assistant_data.setdefault("analysis",{}).setdefault(name,{})[label]=value
            else:
                attr,field=next((a,b) for k,a,b in PRE[name] if k==label)
                w=getattr(host.pages["事件" if name=="事件分段" else "预处理"],field)
                setattr(staged.pipeline,attr,value)
                if name=="事件分段": epoch_changed=True
                else: pre_changed=True
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
            dirty.add(subj.name)
        for subj in staged.subjects:
            if subj.recordings and (pre_changed or epoch_changed or subj.name in dirty):
                dirty.add(subj.name)
                subj.status,subj.epochs_file,subj.n_trials=pj.STATUS_RAW,"",{}
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
            a.ica_manual, a.ica_exclude, a.reviewed_ica = b.ica_manual, b.ica_exclude, b.reviewed_ica
    for name in dirty: p._cache.pop(name,None)
    for w,value in widgets: put_control(w,value)
    if marks is not None:
        page.ica.exclude = sorted(marks)
        page.excl.setText((", ".join(map(str, sorted(marks))) or "无") if updated.ica_manual else "")
    if pre_changed:
        page=host.pages["预处理"]
        page.raw=page.ica=page.raw_ica=page._sig=None
    return pre_changed, bool(dirty)
