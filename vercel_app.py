from __future__ import annotations

import io
import json
import os
import time
from html import escape
from urllib.parse import quote

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

import charger_cabinet_planner as planner
import requests


app = FastAPI()


def html_page(title: str, body: str, main_content_style: str = "") -> str:
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
  <div class="muted">部署在 Vercel 的轻量版（功能覆盖：搜索/测算/简报）。本地完整版仍用 Streamlit。</div>
  <div style="{main_content_style}">
    {body}
  </div>
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


def kv_is_configured() -> bool:
    return bool(os.getenv("KV_REST_API_URL")) and bool(os.getenv("KV_REST_API_TOKEN"))


def kv_call(command: str, *args: str, body: bytes | None = None, params: dict[str, str] | None = None) -> object | None:
    url = (os.getenv("KV_REST_API_URL") or "").strip().rstrip("/")
    token = (os.getenv("KV_REST_API_TOKEN") or "").strip()
    if not url or not token:
        return None

    path = "/".join([quote(command.strip().lower(), safe=""), *(quote(str(a), safe="") for a in args)])
    full_url = f"{url}/{path}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if body is None:
            resp = requests.get(full_url, headers=headers, params=params, timeout=6)
        else:
            resp = requests.post(full_url, headers=headers, params=params, data=body, timeout=6)
    except Exception:
        return None

    try:
        payload = resp.json()
    except Exception:
        return None

    if isinstance(payload, dict) and "error" in payload:
        return None
    if isinstance(payload, dict) and "result" in payload:
        return payload.get("result")
    return None


def kv_set_json(key: str, value: object, ex_seconds: int | None = None) -> None:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    params = {"EX": str(int(ex_seconds))} if ex_seconds else None
    kv_call("set", key, body=body, params=params)


def kv_get_json(key: str) -> object | None:
    raw = kv_call("get", key)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def kv_set_text(key: str, value: str, ex_seconds: int | None = None) -> None:
    body = value.encode("utf-8")
    params = {"EX": str(int(ex_seconds))} if ex_seconds else None
    kv_call("set", key, body=body, params=params)


def kv_get_text(key: str) -> str | None:
    raw = kv_call("get", key)
    if isinstance(raw, str) and raw:
        return raw
    return None


def kv_lpush_json(list_key: str, value: object, max_len: int = 50) -> None:
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    kv_call("lpush", list_key, body=body)
    kv_call("ltrim", list_key, "0", str(max_len - 1))


def kv_lrange_json(list_key: str, start: int, stop: int) -> list[object]:
    raw = kv_call("lrange", list_key, str(start), str(stop))
    if not isinstance(raw, list):
        return []
    out: list[object] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            continue
        try:
            out.append(json.loads(item))
        except Exception:
            continue
    return out


@app.get("/", response_class=HTMLResponse)
def home(query: str | None = None, qid: str | None = None, pop: str | None = None, include_subdiv: int = 0):
    query_val = (query or "").strip()
    qid_val = (qid or "").strip()
    pop_val = (pop or "").strip()
    include_subdiv = 1 if include_subdiv else 0

    left_panel_content = """
<div class="card">
  <form method="get" action="/">
    <div><b>1) 搜索地区</b></div>
    <input type="text" name="query" placeholder="例如：永康、北京、杭州西湖区" value="{query}" />
    <div style="margin-top:10px;"><button type="submit">搜索</button></div>
  </form>
</div>
""".format(query=escape(query_val))
    right_panel_content = ""

    if not query_val:
        if kv_is_configured():
            items = kv_lrange_json("history:queries", 0, 9)
            if items:
                links: list[str] = []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    item_qid = str(it.get("qid") or "").strip()
                    item_name = str(it.get("name") or "").strip()
                    item_pop = it.get("population")
                    if not item_qid or not item_name:
                        continue
                    if isinstance(item_pop, int) and item_pop > 0:
                        pop_param = f"&pop={quote(str(item_pop), safe='')}"
                    else:
                        pop_param = ""
                    links.append(
                        f"<div><a href='/?query={quote(item_name, safe='')}&qid={quote(item_qid, safe='')}{pop_param}'>"
                        f"{escape(item_name)} <span class='muted'>({escape(item_qid)})</span>"
                        f"</a></div>"
                    )
                if links:
                    left_panel_content += "<div class='card'><div><b>最近查询</b></div><div style='margin-top:10px;'>" + "".join(links) + "</div></div>"
        body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;">{right_panel_content}</div>
</div>
"""
        return html_page("共享充电宝投放分析工具", body)

    candidates = []
    error = ""
    try:
        candidates = planner.wikidata_search(query_val, limit=10, language="zh")
    except Exception as e:
        error = str(e)

    if error:
        left_panel_content += f"<div class='card error'>联网搜索失败：{escape(error)}</div>"
        body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;'>{right_panel_content}</div>
</div>
"""
        return html_page("共享充电宝投放分析工具", body)

    if not candidates:
        left_panel_content += "<div class='card muted'>未找到匹配项，请换个关键词再试。</div>"
        body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;'>{right_panel_content}</div>
</div>
"""
        return html_page("共享充电宝投放分析工具", body)

    left_panel_content += """
<div class="card">
  <form method="get" action="/">
    <div><b>2) 选择最匹配项</b></div>
    <input type="hidden" name="query" value="{query}" />
""".format(query=escape(query_val))

    for idx, c in enumerate(candidates):
        checked = "checked" if (qid_val and c.qid == qid_val) or (not qid_val and idx == 0) else ""
        desc = f" - {c.description}" if c.description else ""
        left_panel_content += (
            f"<label style='display:block;margin:8px 0;'>"
            f"<input type='radio' name='qid' value='{escape(c.qid)}' {checked} /> "
            f"{escape(c.label)}{escape(desc)} <span class='muted'>({escape(c.qid)})</span>"
            f"</label>"
        )

    checked = "checked" if include_subdiv else ""
    left_panel_content += """
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
        body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;">{right_panel_content}</div>
</div>
"""
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
            left_panel_content += f"<div class='card error'>人口解析失败：{escape(str(e))}</div>"
            body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;">{right_panel_content}</div>
</div>
"""
            return html_page("共享充电宝投放分析工具", body)
    else:
        try:
            population = planner.wikidata_population(selected_qid)
        except Exception as e:
            left_panel_content += f"<div class='card error'>自动获取人口失败：{escape(str(e))}</div>"
            body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;'>{right_panel_content}</div>
</div>
"""
            return html_page("共享充电宝投放分析工具", body)

    if population is None:
        left_panel_content += "<div class='card error'>无法自动获取该地区人口，请手动输入人口数。</div>"
        body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;'>{right_panel_content}</div>
</div>
"""
        return html_page("共享充电宝投放分析工具", body)

    population_int = int(population)
    cache_key = f"calc:{selected_qid}:{population_int}:{planner.PEOPLE_PER_CABINET}:{planner.CABINETS_PER_AGENT}"
    cached = kv_get_json(cache_key) if kv_is_configured() else None
    if isinstance(cached, dict) and cached.get("qid") == selected_qid and cached.get("population") == population_int:
        plan = planner.plan_for_area(str(cached.get("name") or name), population_int)
    else:
        plan = planner.plan_for_area(name, population_int)
    rows = [
        {
            "地区": plan.name,
            "人口(万)": f"{plan.population / 10_000:.2f}",
            "柜机数": f"{plan.cabinets_needed}",
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

    if kv_is_configured():
        ts = int(time.time())
        kv_set_json(
            cache_key,
            {
                "qid": selected_qid,
                "name": plan.name,
                "population": plan.population,
                "cabinets": plan.cabinets_needed,
                "agents": plan.agent_slots,
                "people_per_cabinet": planner.PEOPLE_PER_CABINET,
                "cabinets_per_agent": planner.CABINETS_PER_AGENT,
                "include_subdiv": include_subdiv,
                "ts": ts,
            },
            ex_seconds=60 * 60 * 24 * 30,
        )
        kv_lpush_json(
            "history:queries",
            {"qid": selected_qid, "name": plan.name, "population": plan.population, "ts": ts},
            max_len=60,
        )

    left_panel_content += f"""
<div class="card">
  <div class="ok"><b>测算结果</b></div>
  <div class="row" style="margin-top:10px;">
    <div><b>人口(万)</b><div>{escape(f"{plan.population / 10_000:,.2f}")}</div></div>
    <div><b>建议柜机数</b><div>{escape(f"{plan.cabinets_needed:,}")}</div></div>
    <div><b>代理名额</b><div>{escape(f"{plan.agent_slots:,}")}</div></div>
  </div>
  <div style="margin-top:12px;">{render_table(rows)}</div>
</div>
"""
    report_content = ""
    if selected_qid and plan.name and plan.population:
        report_key = f"report:{selected_qid}:{plan.population}:{planner.PEOPLE_PER_CABINET}:{planner.CABINETS_PER_AGENT}"
        if kv_is_configured():
            cached = kv_get_text(report_key)
            if cached:
                report_content = cached
        if not report_content:
            entity = None
            try:
                entity = planner.wikidata_first_entity(selected_qid, language=planner.WIKIDATA_LANG)
            except Exception:
                entity = None
            report_content = planner.build_area_report(plan=plan, qid=selected_qid, entity=entity)
            if kv_is_configured() and report_content:
                kv_set_text(report_key, report_content, ex_seconds=60 * 60 * 24 * 30)

    right_panel_content = f"""
<div class="card">
  <div class="ok"><b>简报</b></div>
  <pre style="margin-top:10px;">{escape(report_content)}</pre>
</div>
"""
    body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;">{right_panel_content}</div>
</div>
"""
    return html_page("共享充电宝投放分析工具", body, "display: flex; gap: 24px;")