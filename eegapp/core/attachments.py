"""本地选取的附件只读解析；模型接收内容摘要，不接收本地路径。"""
import base64
import codecs
import io
import json
import mimetypes
import shutil
import subprocess
import zipfile
from pathlib import Path

TEXT_LIMIT = 400_000
DOCUMENT_LIMIT = 40 * 1024 * 1024
IMAGE_FORMATS = {".png",".jpg",".jpeg",".gif",".webp",".bmp",".tif",".tiff",".ico"}
OFFICE = {".pdf",".docx",".pptx"}


def inspect_file(path):
    p = Path(path).resolve(strict=True)
    if not p.is_file():
        raise ValueError("请选择文件；文件夹可以先压缩为 ZIP。")
    stat = p.stat()
    with p.open("rb") as f:
        sample = f.read(4096)
    ext = p.suffix.lower()
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    encoding = None
    if ext in IMAGE_FORMATS:
        kind = "图片预览"
    elif ext in OFFICE:
        kind = "文档正文"
    elif ext == ".zip":
        kind = "压缩包目录"
    elif mime.startswith(("audio/","video/")):
        kind = "媒体信息"
    else:
        if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        elif b"\0" not in sample:
            for enc in ("utf-8-sig","gb18030"):
                try:
                    txt = codecs.getincrementaldecoder(enc)().decode(sample, final=False)
                except UnicodeDecodeError:
                    continue
                if all(c.isprintable() or c.isspace() for c in txt):
                    encoding = enc
                    break
        kind = "文本内容" if encoding else "文件信息"
    return dict(path=str(p), name=p.name, size=stat.st_size, mtime=stat.st_mtime_ns,
                mime=mime, kind=kind, encoding=encoding)


def document_parts(p):
    if p.suffix.lower()==".pdf":
        from pypdf import PdfReader
        reader=PdfReader(p)
        for i,page in enumerate(reader.pages,1):
            yield f"[第 {i} 页]\n"+(page.extract_text() or "（本页没有可提取的文字）")
        return
    with zipfile.ZipFile(p) as z:
        if sum(i.file_size for i in z.infolist())>150*1024*1024:
            raise ValueError("文档展开后过大")
    if p.suffix.lower()==".docx":
        from docx import Document
        from docx.table import Table
        doc=Document(p)
        for block in doc.iter_inner_content():
            if isinstance(block,Table):
                yield "\n".join(" | ".join(c.text for c in row.cells) for row in block.rows)
            else:
                yield block.text
    else:
        from pptx import Presentation
        for i,slide in enumerate(Presentation(p).slides,1):
            yield f"[幻灯片 {i}]"
            for shape in slide.shapes:
                if shape.has_text_frame:
                    yield shape.text
                elif shape.has_table:
                    for row in shape.table.rows:
                        yield " | ".join(cell.text for cell in row.cells)


def prepare(info):
    p=Path(info["path"])
    st=p.stat()
    if (st.st_size,st.st_mtime_ns)!=(info["size"],info["mtime"]):
        raise ValueError(f"{p.name} 已变化，请移除并重新添加附件")
    meta={"文件名":p.name,"大小（字节）":st.st_size,"类型":info["mime"],"读取方式":info["kind"]}
    text=""
    image=None
    try:
        kind=info["kind"]
        if kind=="文本内容":
            with p.open(encoding=info["encoding"],errors="replace") as f:
                text=f.read(TEXT_LIMIT+1)
        elif kind in ("文档正文","压缩包目录"):
            if st.st_size>DOCUMENT_LIMIT:
                raise ValueError("超过本地文档解析上限 40 MiB")
            if kind=="压缩包目录":
                with zipfile.ZipFile(p) as z:
                    meta["条目总数"]=len(z.infolist())
                    text="\n".join(i.filename for i in z.infolist()[:5000])
                    meta["列出条目数"]=min(5000,len(z.infolist()))
            else:
                buf=io.StringIO()
                for part in document_parts(p):
                    buf.write(part[:TEXT_LIMIT+1-buf.tell()]+"\n")
                    if buf.tell()>TEXT_LIMIT:break
                text=buf.getvalue()
        elif kind=="图片预览":
            from PIL import Image,ImageOps
            with Image.open(p) as im:
                meta["原始尺寸"]=list(im.size)
                if im.width*im.height>60_000_000: raise ValueError("图片像素过大")
                im.thumbnail((1800,1800))
                rgb=ImageOps.exif_transpose(im).convert("RGBA")
                canvas=Image.new("RGB",rgb.size,"white")
                canvas.paste(rgb,mask=rgb.getchannel("A"))
                buf=io.BytesIO()
                canvas.save(buf,format="JPEG",quality=89)
                image="data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode()
                meta["预览尺寸"]=list(canvas.size)
                if getattr(im,"is_animated",False):meta["说明"]="使用首帧"
        elif kind=="媒体信息":
            probe=shutil.which("ffprobe")
            if probe:
                cmd=[probe,"-v","error","-protocol_whitelist","file,pipe","-show_entries",
                     "format=duration,format_name:stream=codec_type,codec_name,width,height,sample_rate,channels",
                     "-of","json",str(p)]
                result=subprocess.run(cmd,capture_output=True,timeout=8,check=True,
                                      creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                meta["媒体参数"]=json.loads(result.stdout)
            meta["内容范围"]="文件信息；未转录声音或解析视频画面"
        else:
            meta["内容范围"]="仅文件属性，没有专用内容解析器"
    except Exception as exc:
        meta["读取方式"]="文件信息"
        meta["解析提示"]=f"正文或预览读取失败（{type(exc).__name__}）"
    if text:
        meta["截断"]=len(text)>TEXT_LIMIT
        text=text[:TEXT_LIMIT]
        meta["可读字符数"]=len(text)
        meta["开头"]=text[:1800]
    return meta,text,image
