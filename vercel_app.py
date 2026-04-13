from __future__ import annotations

import csv
import io
from html import escape
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

import charger_cabinet_planner as planner


app = FastAPI()


def html_page(title: str, body: str) -> str:
    safe_title = escape(title)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial; margin: 24px; }}
    .card {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; margin: 12px 0; }}
    .row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .row > * {{ flex: 1; min-width: 220px; }}
    input[type="text"] {{ width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 8px; }}
    button {{ padding: 10px 14px; border: 0; border-radius: 8px; background: #2563eb; color: white; cursor: pointer; }}
    button.secondary {{ background: #111827; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 8px; text-align: left; }}
    th {{ background: #f9fafb; }}
    .muted {{ color: #6b7280; }}
    .error {{ color: #b91c1c; }}
    .ok {{ color: #047857; }}
    a {{ color: #2563eb; text-decoration: none; }}
    code, pre {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>{safe_title}</h1>
  <div class="muted">部署在 Vercel 的轻量版（功能覆盖：搜索/测算/简报/CSV）。本地完整版仍用 Streamlit。</div>
  {body}
</body>
</html>"""


def render_table(rows: list[dict]) -> str:
    if not rows:
        return "<div class='muted'>无数据</div>"
    headers = [k for k in rows[0].keys() if not k.startswith("_")]
    thead = "<tr>" + "".join(f"<th>{escape(str(h))}</th>" for h in headers) + "</tr>"
    tbody_rows = []
    for r in rows:
        tds = "".join(f"<td>{escape(str(r.get(h, '')))}</td>" for h in headers)
        tbody_rows.append(f"<tr>{tds}</tr>")
    tbody = "".join(tbody_rows)
    return f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>"


def read_uploaded_csv(upload: UploadFile) -> list[dict]:
    raw = upload.file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    f = io.StringIO(text)
    reader = csv.DictReader(f)
    rows: list[dict] = []
    for row in reader:
        if isinstance(row, dict):
            rows.append(row)
    return rows


@app.get("/", response_class=HTMLResponse)
def home(query: str | None = None, qid: str | None = None, pop: str | None = None, include_subdiv: int = 0):
    query_val = (query or "").strip()
    qid_val = (qid or "").strip()
    pop_val = (pop or "").strip()
    include_subdiv = 1 if include_subdiv else 0

    body = """
<div class="card">
  <div class="row">
    <form method="get" action="/">
      <div><b>1) 搜索地区</b></div>
      <input type="text" name="query" placeholder="例如：永康、北京、杭州西湖区" value="{query}" />
      <div style="margin-top:10px;"><button type="submit">搜索</button></div>
    </form>
    <form method="get" action="/csv">
      <div><b>批量 CSV</b></div>
      <div class="muted">跳转到 CSV 上传与批量计算</div>
      <div style="margin-top:10px;"><button class="secondary" type="submit">打开 CSV 页</button></div>
    </form>
  </div>
</div>
""".format(query=escape(query_val))

    if not query_val:
        return html_page("共享充电宝投放分析工具", body)

    candidates = []
    error = ""
    try:
        candidates = planner.wikidata_search(query_val, limit=10, language="zh")
    except Exception as e:
        error = str(e)

    if error:
        body += f"<div class='card error'>联网搜索失败：{escape(error)}</div>"
        return html_page("共享充电宝投放分析工具", body)

    if not candidates:
        body += "<div class='card muted'>未找到匹配项，请换个关键词再试。</div>"
        return html_page("共享充电宝投放分析工具", body)

    body += """
<div class="card">
  <form method="get" action="/">
    <div><b>2) 选择最匹配项</b></div>
    <input type="hidden" name="query" value="{query}" />
""".format(query=escape(query_val))

    for idx, c in enumerate(candidates):
        checked = "checked" if (qid_val and c.qid == qid_val) or (not qid_val and idx == 0) else ""
        desc = f" - {c.description}" if c.description else ""
        body += (
            f"<label style='display:block;margin:8px 0;'>"
            f"<input type='radio' name='qid' value='{escape(c.qid)}' {checked} /> "
            f"{escape(c.label)}{escape(desc)} <span class='muted'>({escape(c.qid)})</span>"
            f"</label>"
        )

    checked = "checked" if include_subdiv else ""
    body += """
    <div style="margin-top:12px;"><b>3) 人口（可留空自动查）</b></div>
    <input type="text" name="pop" placeholder="例如：100万、350000、0.35亿（留空自动查询）" value="{pop}" />
    <div style="margin-top:10px;">
      <label><input type="checkbox" name="include_subdiv" value="1" {checked} /> 拉取下一级行政区划（可能较慢）</label>
    </div>
    <div style="margin-top:10px;"><button type="submit">开始测算</button></div>
  </form>
</div>
""".format(pop=escape(pop_val), checked=checked)

    selected_qid = qid_val or candidates[0].qid
    if not selected_qid:
        return html_page("共享充电宝投放分析工具", body)

    entity = None
    try:
        entity = planner.wikidata_first_entity(selected_qid, language=planner.WIKIDATA_LANG)
    except Exception:
        entity = None

    name = (entity.label if entity and entity.label else query_val).strip() or query_val

    population: int | None = None
    if pop_val:
        try:
            population = planner.parse_population(pop_val)
        except Exception as e:
            body += f"<div class='card error'>人口解析失败：{escape(str(e))}</div>"
            return html_page("共享充电宝投放分析工具", body)
    else:
        try:
            population = planner.wikidata_population(selected_qid)
        except Exception as e:
            body += f"<div class='card error'>自动获取人口失败：{escape(str(e))}</div>"
            return html_page("共享充电宝投放分析工具", body)

    if population is None:
        body += "<div class='card error'>无法自动获取该地区人口，请手动输入人口数。</div>"
        return html_page("共享充电宝投放分析工具", body)

    plan = planner.plan_for_area(name, int(population))
    rows = [
        {
            "地区": plan.name,
            "人口(万)": f"{plan.population / 10_000:.2f}",
            "柜机数": f"{plan.cabinets_needed}",
            "代理名额": f"{plan.agent_slots}",
            "_qid": selected_qid,
        }
    ]

    if include_subdiv and entity:
        try:
            children = planner.wikidata_entity_list_qids_labels(entity, "P150", limit=40)
        except Exception:
            children = []

        for child_qid, child_label in children:
            try:
                child_pop = planner.wikidata_population(child_qid)
            except Exception:
                child_pop = None
            if child_pop is None:
                continue
            child_plan = planner.plan_for_area(child_label, int(child_pop))
            rows.append(
                {
                    "地区": child_plan.name,
                    "人口(万)": f"{child_plan.population / 10_000:.2f}",
                    "柜机数": f"{child_plan.cabinets_needed}",
                    "代理名额": f"{child_plan.agent_slots}",
                    "_qid": child_qid,
                }
            )

    body += f"""
<div class="card">
  <div class="ok"><b>测算结果</b></div>
  <div class="row" style="margin-top:10px;">
    <div><b>人口(万)</b><div>{escape(f"{plan.population / 10_000:,.2f}")}</div></div>
    <div><b>建议柜机数</b><div>{escape(f"{plan.cabinets_needed:,}")}</div></div>
    <div><b>代理名额</b><div>{escape(f"{plan.agent_slots:,}")}</div></div>
  </div>
  <div style="margin-top:12px;">{render_table(rows)}</div>
  <div style="margin-top:12px;">
    <a href="/report?qid={escape(selected_qid)}&name={escape(plan.name)}&pop={escape(str(plan.population))}">生成简报</a>
  </div>
</div>
"""
    return html_page("共享充电宝投放分析工具", body)


@app.get("/report", response_class=PlainTextResponse)
def report(qid: str, name: str, pop: int):
    entity = None
    try:
        entity = planner.wikidata_first_entity(qid, language=planner.WIKIDATA_LANG)
    except Exception:
        entity = None
    plan = planner.plan_for_area(name, int(pop))
    text = planner.build_area_report(plan=plan, qid=qid, entity=entity)
    return text


@app.get("/csv", response_class=HTMLResponse)
def csv_page():
    body = """
<div class="card">
  <form method="post" action="/csv" enctype="multipart/form-data">
    <div><b>批量上传 CSV</b></div>
    <div class="muted">需要包含列：name/地区/城市/名称 与 population/人口/人数（支持 万/亿）。</div>
    <div style="margin-top:10px;">
      <input type="file" name="file" accept=".csv,text/csv" />
    </div>
    <div style="margin-top:10px;">
      <button type="submit" name="mode" value="view">查看结果</button>
      <button class="secondary" type="submit" name="mode" value="download">下载 CSV</button>
      <a style="margin-left:10px;" href="/">返回首页</a>
    </div>
  </form>
</div>
"""
    return html_page("CSV 批量处理", body)


@app.post("/csv", response_class=HTMLResponse)
def csv_compute(mode: str = Form("view"), file: UploadFile = File(...)):
    rows = read_uploaded_csv(file)
    if not rows:
        return html_page("CSV 批量处理", "<div class='card error'>CSV 为空或无法解析。</div><a href='/csv'>返回</a>")

    header = set(rows[0].keys())
    name_key = next((k for k in header if str(k).lower() in {"name", "city", "area", "地区", "城市", "名称"}), None)
    pop_key = next((k for k in header if str(k).lower() in {"population", "pop", "people", "人口", "人数"}), None)
    if not name_key or not pop_key:
        return html_page(
            "CSV 批量处理",
            "<div class='card error'>CSV 必须包含地区名称列和人口列。</div><a href='/csv'>返回</a>",
        )

    out: list[dict] = []
    failed = 0
    for r in rows:
        name = str(r.get(name_key) or "").strip()
        pop_raw = str(r.get(pop_key) or "").strip()
        if not name and not pop_raw:
            continue
        try:
            pop = planner.parse_population(pop_raw)
            p = planner.plan_for_area(name, pop)
            out.append(
                {
                    "地区": p.name,
                    "人口(万)": f"{p.population / 10_000:.2f}",
                    "柜机数": f"{p.cabinets_needed}",
                    "代理名额": f"{p.agent_slots}",
                }
            )
        except Exception:
            failed += 1

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["地区", "人口(万)", "柜机数", "代理名额"])
    writer.writeheader()
    for item in out:
        writer.writerow(item)
    content = buf.getvalue().encode("utf-8-sig")
    if (mode or "").strip().lower() == "download":
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=批量计算结果.csv"},
        )

    body = f"""
<div class="card">
  <div class="ok"><b>批量结果</b></div>
  <div class="muted">成功 {len(out)} 条，跳过 {failed} 条</div>
  <div style="margin-top:12px;">{render_table(out[:200])}</div>
  <div style="margin-top:12px;">
    <a href="/csv">返回继续上传</a>
    <a style="margin-left:10px;" href="/">返回首页</a>
  </div>
</div>
"""
    return HTMLResponse(content=html_page("CSV 批量处理", body))
