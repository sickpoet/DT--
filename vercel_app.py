from __future__ import annotations

import io
import json
import os
import time
from functools import lru_cache
from html import escape
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

import charger_cabinet_planner as planner
import requests


app = FastAPI()

def env_has(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def kv_env_probe() -> dict[str, bool]:
    names = [
        "KV_REST_API_URL",
        "KV_REST_API_TOKEN",
        "KV_REST_API_READ_ONLY_TOKEN",
        "REDIS_URL",
        "UPSTASH_REDIS_URL",
        "REDIS_REST_URL",
        "REDIS_REST_TOKEN",
        "REDIS_REST_READ_ONLY_TOKEN",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "UPSTASH_REDIS_REST_READ_ONLY_TOKEN",
    ]
    return {n: env_has(n) for n in names}



def get_api_connection_status() -> str:
    amap_status = "已配置" if amap_is_configured() else "未配置"
    probe = kv_env_probe()
    probe_text = " / ".join(
        [
            f"KV_URL={'是' if probe['KV_REST_API_URL'] else '否'}",
            f"KV_TOKEN={'是' if probe['KV_REST_API_TOKEN'] else '否'}",
            f"KV_RO={'是' if probe['KV_REST_API_READ_ONLY_TOKEN'] else '否'}",
            f"REDIS_URL={'是' if probe['REDIS_URL'] else '否'}",
            f"REDIS_REST_URL={'是' if probe['REDIS_REST_URL'] else '否'}",
            f"REDIS_REST_TOKEN={'是' if probe['REDIS_REST_TOKEN'] else '否'}",
            f"UP_URL={'是' if probe['UPSTASH_REDIS_REST_URL'] else '否'}",
            f"UP_TOKEN={'是' if probe['UPSTASH_REDIS_REST_TOKEN'] else '否'}",
            f"UP_RO={'是' if probe['UPSTASH_REDIS_REST_READ_ONLY_TOKEN'] else '否'}",
        ]
    )
    kv_mode = kv_mode_text()
    status_html = f"""
    <div style="background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px; padding: 12px; margin-bottom: 20px;">
      <div style="font-weight: bold; color: #0369a1; margin-bottom: 8px;">API 连接状态</div>
      <div style="display: flex; gap: 20px; flex-wrap: wrap;">
        <div>高德行政区划: <span class="{'ok' if amap_is_configured() else 'error'}">{amap_status}</span></div>
        <div>缓存(Vercel KV/Upstash): <span class="{kv_mode['class']}">{kv_mode['text']}</span></div>
      </div>
      <div class="muted" style="margin-top: 8px;">运行时环境变量：<code>{escape(probe_text)}</code></div>
      <div class="muted" style="margin-top: 8px;">在 Vercel 控制台启用 Storage → Upstash → Redis 并绑定项目后，会自动注入 UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN。</div>
    </div>
    """
    return status_html


def html_page(title: str, body: str, main_content_style: str = "") -> str:
    safe_title = escape(title)
    api_status = get_api_connection_status()
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
    .row > * {{ flex: 1; min-width: 320px; }}
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
    code, pre {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px; overflow: auto; white-space: pre-wrap; word-break: break-all; }}
  </style>
</head>
<body>
  <h1>{safe_title}</h1>
  <div class="muted">部署在 Vercel 的轻量版（功能覆盖：搜索/测算/简报）。本地完整版仍用 Streamlit。</div>
  {api_status}
  <div style="margin-top: 24px; {main_content_style}">
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
    return kv_can_read()


def kv_rest_url() -> str:
    for k in ("KV_REST_API_URL", "REDIS_REST_URL", "UPSTASH_REDIS_REST_URL"):
        v = (os.getenv(k) or "").strip()
        if v:
            return v.rstrip("/")
    return ""


def kv_rest_write_token() -> str:
    for k in ("KV_REST_API_TOKEN", "REDIS_REST_TOKEN", "UPSTASH_REDIS_REST_TOKEN"):
        v = (os.getenv(k) or "").strip()
        if v:
            return v
    return ""


def kv_rest_read_token() -> str:
    for k in (
        "KV_REST_API_READ_ONLY_TOKEN",
        "REDIS_REST_READ_ONLY_TOKEN",
        "UPSTASH_REDIS_REST_READ_ONLY_TOKEN",
        "KV_REST_API_TOKEN",
        "REDIS_REST_TOKEN",
        "UPSTASH_REDIS_REST_TOKEN",
    ):
        v = (os.getenv(k) or "").strip()
        if v:
            return v
    return ""


def kv_can_read() -> bool:
    return (bool(kv_rest_url()) and bool(kv_rest_read_token())) or bool(kv_redis_url())


def kv_can_write() -> bool:
    return (bool(kv_rest_url()) and bool(kv_rest_write_token())) or bool(kv_redis_url())


def kv_mode_text() -> dict[str, str]:
    if kv_redis_url():
        return {"text": "已配置(Redis URL)", "class": "ok"}
    if kv_can_write():
        return {"text": "已配置(读写)", "class": "ok"}
    if kv_can_read():
        return {"text": "已配置(只读)", "class": "muted"}
    return {"text": "未配置(可选)", "class": "muted"}


def kv_redis_url() -> str:
    for k in ("REDIS_URL", "UPSTASH_REDIS_URL"):
        v = (os.getenv(k) or "").strip()
        if v:
            return v
    return ""


@lru_cache(maxsize=1)
def kv_redis_client():
    url = kv_redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore
    except Exception:
        return None
    try:
        return redis.Redis.from_url(url, socket_timeout=3, socket_connect_timeout=3, retry_on_timeout=True)
    except Exception:
        return None



def kv_call(command: str, *args: str, body: bytes | None = None, params: dict[str, str] | None = None) -> object | None:
    url = kv_rest_url()
    if not url or not token:
        client = kv_redis_client()
        if client is None:
            return None
        cmd = command.strip().lower()
        try:
            if cmd == "get" and len(args) >= 1:
                v = client.get(str(args[0]))
                if v is None:
                    return None
                if isinstance(v, bytes):
                    return v.decode("utf-8", errors="ignore")
                return str(v)
            if cmd == "set" and len(args) >= 1 and body is not None:
                ex = None
                if params and isinstance(params.get("EX"), str) and params["EX"].strip().isdigit():
                    ex = int(params["EX"].strip())
                ok = client.set(str(args[0]), body, ex=ex)
                return "OK" if ok else None
            if cmd == "lpush" and len(args) >= 1 and body is not None:
                client.lpush(str(args[0]), body)
                return 1
            if cmd == "ltrim" and len(args) >= 3:
                client.ltrim(str(args[0]), int(args[1]), int(args[2]))
                return 1
            if cmd == "lrange" and len(args) >= 3:
                items = client.lrange(str(args[0]), int(args[1]), int(args[2]))
                out: list[str] = []
                for it in items:
                    if isinstance(it, bytes):
                        out.append(it.decode("utf-8", errors="ignore"))
                    else:
                        out.append(str(it))
                return out
        except Exception:
            return None
        return None
        return None

    path = "/".join([quote(command.strip().lower(), safe=""), *(quote(str(a), safe="") for a in args)])
    full_url = f"{url}/{path}"
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
    if not kv_can_write():
        return
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    params = {"EX": str(int(ex_seconds))} if ex_seconds else None
    kv_call("set", key, body=body, params=params)


def kv_get_json(key: str) -> object | None:
    if not kv_can_read():
        return None
    raw = kv_call("get", key)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def kv_set_text(key: str, value: str, ex_seconds: int | None = None) -> None:
    if not kv_can_write():
        return
    body = value.encode("utf-8")
    params = {"EX": str(int(ex_seconds))} if ex_seconds else None
    kv_call("set", key, body=body, params=params)


def kv_get_text(key: str) -> str | None:
    if not kv_can_read():
        return None
    raw = kv_call("get", key)
    if isinstance(raw, str) and raw:
        return raw
    return None


def kv_lpush_json(list_key: str, value: object, max_len: int = 50) -> None:
    if not kv_can_write():
        return
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    kv_call("lpush", list_key, body=body)
    kv_call("ltrim", list_key, "0", str(max_len - 1))


def kv_lrange_json(list_key: str, start: int, stop: int) -> list[object]:
    if not kv_can_read():
        return []
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


def amap_key() -> str | None:
    for k in ("AMAP_KEY", "GAODE_KEY", "AMAP_WEB_KEY"):
        v = (os.getenv(k) or "").strip()
        if v:
            return v
    return None


def amap_is_configured() -> bool:
    return bool(amap_key())


def amap_get_json(params: dict[str, str]) -> dict | None:
    key = amap_key()
    if not key:
        return None
    merged = dict(params)
    merged["key"] = key
    try:
        resp = requests.get("https://restapi.amap.com/v3/config/district", params=merged, timeout=6)
    except Exception:
        return None
    try:
        payload = resp.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("status") != "1":
        return None
    return payload


def amap_get_poi_json(params: dict[str, str]) -> dict | None:
    key = amap_key()
    if not key:
        return None
    merged = dict(params)
    merged["key"] = key
    try:
        resp = requests.get("https://restapi.amap.com/v3/place/text", params=merged, timeout=6)
    except Exception:
        return None
    try:
        payload = resp.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("status") != "1":
        return None
    return payload


def amap_poi_count(city: str, keyword: str) -> int | None:
    city_val = city.strip()
    kw_val = keyword.strip()
    if not city_val or not kw_val:
        return None

    cache_key = f"amap:poi:count:{city_val}:{kw_val}"
    if kv_is_configured():
        cached = kv_get_json(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("count"), int):
            return int(cached["count"])

    payload = amap_get_poi_json(
        {
            "keywords": kw_val,
            "city": city_val,
            "citylimit": "true",
            "children": "0",
            "offset": "1",
            "page": "1",
            "extensions": "base",
        }
    )
    if not payload:
        return None
    count_raw = payload.get("count")
    try:
        count = int(str(count_raw or "0").strip())
    except Exception:
        count = 0
    if kv_is_configured():
        kv_set_json(cache_key, {"count": count}, ex_seconds=60 * 60 * 24 * 14)
    return count


def amap_poi_samples(city: str, keyword: str, limit: int = 3) -> list[str]:
    city_val = city.strip()
    kw_val = keyword.strip()
    limit = max(1, min(int(limit), 10))
    if not city_val or not kw_val:
        return []

    cache_key = f"amap:poi:samples:{city_val}:{kw_val}:{limit}"
    if kv_is_configured():
        cached = kv_get_json(cache_key)
        if isinstance(cached, list):
            out: list[str] = []
            for it in cached:
                s = str(it or "").strip()
                if s:
                    out.append(s)
            if out:
                return out[:limit]

    payload = amap_get_poi_json(
        {
            "keywords": kw_val,
            "city": city_val,
            "citylimit": "true",
            "children": "0",
            "offset": str(limit),
            "page": "1",
            "extensions": "base",
        }
    )
    if not payload:
        return []
    pois = payload.get("pois")
    if not isinstance(pois, list) or not pois:
        return []

    out: list[str] = []
    for p in pois:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if name:
            out.append(name)
        if len(out) >= limit:
            break

    if kv_is_configured() and out:
        kv_set_json(cache_key, out, ex_seconds=60 * 60 * 24 * 14)
    return out


def amap_build_poi_section(city: str) -> str:
    if not amap_is_configured():
        return ""
    city_val = city.strip()
    if not city_val:
        return ""

    items = [
        ("商场/购物中心", "商场"),
        ("餐饮", "餐厅"),
        ("医院", "医院"),
        ("学校", "学校"),
        ("地铁站", "地铁站"),
        ("景点", "景区"),
        ("写字楼", "写字楼"),
    ]
    lines: list[str] = []
    lines.append("（高德）POI 概览")
    ok_any = False
    for label, kw in items:
        cnt = amap_poi_count(city_val, kw)
        if cnt is None:
            continue
        ok_any = True
        samples = amap_poi_samples(city_val, kw, limit=3)
        if samples:
            lines.append(f"- {label}：{cnt:,}（例如：{'、'.join(samples)}）")
        else:
            lines.append(f"- {label}：{cnt:,}")
    if not ok_any:
        return ""
    lines.append("")
    return "\n".join(lines)


def insert_poi_after_section3(report: str, poi_section: str) -> str:
    base = (report or "").strip()
    poi = (poi_section or "").strip()
    if not poi:
        return report
    if not base:
        return poi + "\n"

    marker = "\n四、投放建议"
    idx = base.find(marker)
    if idx == -1:
        marker = "\n五、核心板块拆解"
        idx = base.find(marker)
    if idx == -1:
        return base.rstrip() + "\n\n" + poi + "\n"

    before = base[:idx].rstrip()
    after = base[idx:].lstrip()
    return before + "\n\n" + poi + "\n\n" + after + "\n"


def amap_district_search(keyword: str, limit: int = 10) -> list[dict[str, str]]:
    kw = keyword.strip()
    if not kw:
        return []

    cache_key = f"amap:district:search:{kw}:{limit}"
    if kv_is_configured():
        cached = kv_get_json(cache_key)
        if isinstance(cached, list):
            out: list[dict[str, str]] = []
            for item in cached:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                adcode = str(item.get("adcode") or "").strip()
                level = str(item.get("level") or "").strip()
                if name and adcode:
                    out.append({"name": name, "adcode": adcode, "level": level})
            if out:
                return out

    payload = amap_get_json(
        {
            "keywords": kw,
            "subdistrict": "0",
            "extensions": "base",
            "page": "1",
            "offset": str(max(1, min(limit, 50))),
        }
    )
    if not payload:
        return []
    districts = payload.get("districts")
    if not isinstance(districts, list):
        return []

    out: list[dict[str, str]] = []
    for d in districts:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        adcode = str(d.get("adcode") or "").strip()
        level = str(d.get("level") or "").strip()
        if name and adcode:
            out.append({"name": name, "adcode": adcode, "level": level})
        if len(out) >= limit:
            break

    if kv_is_configured() and out:
        kv_set_json(cache_key, out, ex_seconds=60 * 60 * 24 * 7)
    return out


def amap_district_detail(adcode_or_keyword: str, subdistrict: int = 0) -> dict | None:
    key = adcode_or_keyword.strip()
    if not key:
        return None

    cache_key = f"amap:district:detail:{key}:{int(bool(subdistrict))}"
    if kv_is_configured():
        cached = kv_get_json(cache_key)
        if isinstance(cached, dict) and str(cached.get("adcode") or "").strip():
            return cached

    payload = amap_get_json(
        {
            "keywords": key,
            "subdistrict": "1" if subdistrict else "0",
            "extensions": "base",
            "page": "1",
            "offset": "1",
        }
    )
    if not payload:
        return None
    districts = payload.get("districts")
    if not isinstance(districts, list) or not districts:
        return None
    d0 = districts[0]
    if not isinstance(d0, dict):
        return None
    if kv_is_configured():
        kv_set_json(cache_key, d0, ex_seconds=60 * 60 * 24 * 30)
    return d0


def pick_best_wikidata_qid(name: str, candidates: list[planner.WikidataCandidate]) -> str | None:
    target = name.strip()
    if not target or not candidates:
        return None

    best_qid: str | None = None
    best_score = -1
    for c in candidates:
        label = (c.label or "").strip()
        desc = (c.description or "").strip()
        score = 0
        if label == target:
            score += 50
        if "中国" in desc or "中华人民共和国" in desc:
            score += 12
        if any(k in desc for k in ("省", "市", "县", "区", "自治州", "地区", "乡", "镇", "街道")):
            score += 8
        if any(k in label for k in ("省", "市", "县", "区", "自治州", "地区", "乡", "镇", "街道")):
            score += 4
        if c.qid and c.qid.startswith("Q"):
            score += 1
        if score > best_score:
            best_score = score
            best_qid = c.qid
    return best_qid


def resolve_wikidata_qid_for_name(name: str) -> str | None:
    target = name.strip()
    if not target:
        return None

    cache_key = f"wikidata:resolve:{target}"
    if kv_is_configured():
        cached = kv_get_text(cache_key)
        if isinstance(cached, str) and cached.startswith("Q"):
            return cached

    try:
        candidates = planner.wikidata_search(target, limit=12, language="zh")
    except Exception:
        candidates = []

    qid = pick_best_wikidata_qid(target, candidates)
    if kv_is_configured() and qid and qid.startswith("Q"):
        kv_set_text(cache_key, qid, ex_seconds=60 * 60 * 24 * 30)
    return qid


def is_county_level_amap(level: str) -> bool:
    lv = (level or "").strip().lower()
    return lv in {"district", "street"}


def is_county_level_wikidata(label: str, description: str) -> bool:
    text = f"{label} {description}".strip()
    if not text:
        return False
    markers = ["自治县", "县级市", "市辖区", "旗", "自治旗", "林区", "县", "区"]
    return any(m in text for m in markers)


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    query: str | None = None,
    code: str | None = None,
    qid: str | None = None,
    pop: str | None = None,
    include_subdiv: int = 0,
):
    query_val = (query or "").strip()
    code_val = (code or "").strip()
    qid_val = (qid or "").strip()
    pop_val = (pop or "").strip()
    include_subdiv_raw = 1 if include_subdiv else 0
    include_subdiv_provided = "include_subdiv" in request.query_params

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
                seen: set[tuple[str, str, str, int | None]] = set()
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    item_qid = str(it.get("qid") or "").strip()
                    item_code = str(it.get("code") or "").strip()
                    item_name = str(it.get("name") or "").strip()
                    item_pop = it.get("population")
                    if not item_name or (not item_qid and not item_code):
                        continue
                    pop_key = int(item_pop) if isinstance(item_pop, int) else None
                    dedup_key = (item_qid, item_code, item_name, pop_key)
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    if isinstance(item_pop, int) and item_pop > 0:
                        pop_param = f"&pop={quote(str(item_pop), safe='')}"
                    else:
                        pop_param = ""
                    if item_code:
                        id_param = f"&code={quote(item_code, safe='')}"
                    else:
                        id_param = f"&qid={quote(item_qid, safe='')}"
                    links.append(
                        f"<div><a href='/?query={quote(item_name, safe='')}{id_param}{pop_param}'>"
                        f"{escape(item_name)} <span class='muted'>({escape(item_code or item_qid)})</span>"
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

    amap_candidates: list[dict[str, str]] = []
    wikidata_candidates: list[planner.WikidataCandidate] = []
    error = ""
    prefer_wikidata = bool(qid_val.startswith("Q") and not code_val)
    if amap_is_configured() and not prefer_wikidata:
        try:
            amap_candidates = amap_district_search(query_val, limit=10)
        except Exception:
            amap_candidates = []
    if not amap_candidates:
        try:
            wikidata_candidates = planner.wikidata_search(query_val, limit=10, language="zh")
        except Exception as e:
            error = str(e)

    if error:
        left_panel_content += f"<div class='card error'>联网搜索失败：{escape(error)}</div>"
        body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;">{right_panel_content}</div>
</div>
"""
        return html_page("共享充电宝投放分析工具", body)

    if not amap_candidates and not wikidata_candidates:
        left_panel_content += "<div class='card muted'>未找到匹配项，请换个关键词再试。</div>"
        body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;">{right_panel_content}</div>
</div>
"""
        return html_page("共享充电宝投放分析工具", body)

    left_panel_content += """
<div class="card">
  <form method="get" action="/">
    <div><b>2) 选择最匹配项</b></div>
    <input type="hidden" name="query" value="{query}" />
""".format(query=escape(query_val))

    selection_mode = "wikidata" if prefer_wikidata else ("amap" if amap_candidates else "wikidata")
    default_include_subdiv = 1
    if selection_mode == "amap":
        selected = None
        if code_val:
            selected = next((c for c in amap_candidates if str(c.get("adcode") or "").strip() == code_val), None)
        if selected is None and amap_candidates:
            selected = amap_candidates[0]
        level = str((selected or {}).get("level") or "").strip()
        default_include_subdiv = 0 if is_county_level_amap(level) else 1
    else:
        selected = None
        if qid_val:
            selected = next((c for c in wikidata_candidates if c.qid == qid_val), None)
        if selected is None and wikidata_candidates:
            selected = wikidata_candidates[0]
        label = (selected.label if selected else "").strip()
        desc = (selected.description if selected else "").strip()
        default_include_subdiv = 0 if is_county_level_wikidata(label, desc) else 1

    include_subdiv_effective = include_subdiv_raw if include_subdiv_provided else default_include_subdiv
    if selection_mode == "amap":
        for idx, c in enumerate(amap_candidates):
            adcode = str(c.get("adcode") or "").strip()
            name = str(c.get("name") or "").strip()
            level = str(c.get("level") or "").strip()
            if not adcode or not name:
                continue
            checked = "checked" if (code_val and adcode == code_val) or (not code_val and idx == 0) else ""
            level_text = f"{escape(level)} / " if level else ""
            left_panel_content += (
                f"<label style='display:block;margin:8px 0;'>"
                f"<input type='radio' name='code' value='{escape(adcode)}' {checked} /> "
                f"{escape(name)} <span class='muted'>({level_text}{escape(adcode)})</span>"
                f"</label>"
            )
    else:
        for idx, c in enumerate(wikidata_candidates):
            checked = "checked" if (qid_val and c.qid == qid_val) or (not qid_val and idx == 0) else ""
            desc = f" - {c.description}" if c.description else ""
            left_panel_content += (
                f"<label style='display:block;margin:8px 0;'>"
                f"<input type='radio' name='qid' value='{escape(c.qid)}' {checked} /> "
                f"{escape(c.label)}{escape(desc)} <span class='muted'>({escape(c.qid)})</span>"
                f"</label>"
            )

    checked = "checked" if include_subdiv_effective else ""
    left_panel_content += """
    <div style="margin-top:12px;"><b>3) 人口（可留空自动查）</b></div>
    <input type="text" name="pop" placeholder="例如：100万、350000、0.35亿（留空自动查询）" value="{pop}" />
    <div style="margin-top:10px;">
      <input type="hidden" name="include_subdiv" value="0" />
      <label><input type="checkbox" name="include_subdiv" value="1" {checked} /> 拉取下一级行政区划（可能较慢）</label>
    </div>
    <div style="margin-top:10px;"><button type="submit">开始测算</button></div>
  </form>
</div>
""".format(pop=escape(pop_val), checked=checked)

    selected_code = ""
    selected_qid = ""
    selected_name = query_val
    resolved_qid: str | None = None
    if selection_mode == "amap":
        selected_code = code_val or str(amap_candidates[0].get("adcode") or "").strip()
        if not selected_code:
            body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;">{right_panel_content}</div>
</div>
"""
            return html_page("共享充电宝投放分析工具", body)
        detail = amap_district_detail(selected_code, subdistrict=1 if include_subdiv_effective else 0) or {}
        selected_name = str(detail.get("name") or query_val).strip() or query_val
        if qid_val.startswith("Q"):
            resolved_qid = qid_val
        else:
            resolved_qid = resolve_wikidata_qid_for_name(selected_name)
        if resolved_qid:
            selected_qid = resolved_qid
    else:
        selected_qid = qid_val or wikidata_candidates[0].qid
        resolved_qid = selected_qid if selected_qid.startswith("Q") else None
        selected_name = query_val

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
        if resolved_qid:
            try:
                population = planner.wikidata_population(resolved_qid)
            except Exception as e:
                left_panel_content += f"<div class='card error'>自动获取人口失败：{escape(str(e))}</div>"
                body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;">{right_panel_content}</div>
</div>
"""
                return html_page("共享充电宝投放分析工具", body)

    if population is None:
        if selection_mode == "amap" and not resolved_qid:
            left_panel_content += "<div class='card error'>已从高德获取行政区划，但未匹配到可用的人口来源，请手动输入人口数。</div>"
        else:
            left_panel_content += "<div class='card error'>无法自动获取该地区人口，请手动输入人口数。</div>"
        body = f"""
<div class="row">
  <div style="flex:1;">{left_panel_content}</div>
  <div style="flex:1;">{right_panel_content}</div>
</div>
"""
        return html_page("共享充电宝投放分析工具", body)

    population_int = int(population)
    calc_id = selected_qid if selected_qid else f"code:{selected_code}"
    cache_key = f"calc:{calc_id}:{population_int}:{planner.PEOPLE_PER_CABINET}:{planner.CABINETS_PER_AGENT}"
    cached = kv_get_json(cache_key) if kv_is_configured() else None
    if isinstance(cached, dict) and cached.get("id") == calc_id and cached.get("population") == population_int:
        plan = planner.plan_for_area(str(cached.get("name") or selected_name), population_int)
    else:
        plan = planner.plan_for_area(selected_name, population_int)
    rows = [
        {
            "地区": plan.name,
            "人口(万)": f"{plan.population / 10_000:.2f}",
            "柜机数": f"{plan.cabinets_needed}",
            "代理名额": f"{plan.agent_slots}",
            "_qid": selected_qid or selected_code,
        }
    ]

    entity = None
    if resolved_qid:
        try:
            entity = planner.wikidata_first_entity(resolved_qid, language=planner.WIKIDATA_LANG)
        except Exception:
            entity = None

    if include_subdiv_effective and entity:
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
                "id": calc_id,
                "qid": selected_qid,
                "code": selected_code,
                "name": plan.name,
                "population": plan.population,
                "cabinets": plan.cabinets_needed,
                "agents": plan.agent_slots,
                "people_per_cabinet": planner.PEOPLE_PER_CABINET,
                "cabinets_per_agent": planner.CABINETS_PER_AGENT,
                "include_subdiv": include_subdiv_effective,
                "ts": ts,
            },
            ex_seconds=60 * 60 * 24 * 30,
        )
        newest = kv_lrange_json("history:queries", 0, 0)
        should_push = True
        if newest and isinstance(newest[0], dict):
            prev = newest[0]
            prev_qid = str(prev.get("qid") or "").strip()
            prev_code = str(prev.get("code") or "").strip()
            prev_name = str(prev.get("name") or "").strip()
            prev_pop = prev.get("population")
            if (
                prev_qid == str(selected_qid or "").strip()
                and prev_code == str(selected_code or "").strip()
                and prev_name == str(plan.name or "").strip()
                and isinstance(prev_pop, int)
                and prev_pop == int(plan.population)
            ):
                should_push = False
        if should_push:
            kv_lpush_json(
                "history:queries",
                {"qid": selected_qid, "code": selected_code, "name": plan.name, "population": plan.population, "ts": ts},
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
    report_qid = selected_qid or None
    poi_city = selected_code or selected_name
    poi_section = amap_build_poi_section(poi_city) if poi_city else ""

    if report_qid and plan.name and plan.population:
        report_key = f"report:v2:{report_qid}:{selected_code}:{plan.population}:{planner.PEOPLE_PER_CABINET}:{planner.CABINETS_PER_AGENT}"
        if kv_is_configured():
            cached = kv_get_text(report_key)
            if cached:
                report_content = cached
        if not report_content:
            report_content = planner.build_area_report(plan=plan, qid=report_qid, entity=entity)
            if poi_section:
                report_content = insert_poi_after_section3(report_content, poi_section)
            if kv_is_configured() and report_content:
                kv_set_text(report_key, report_content, ex_seconds=60 * 60 * 24 * 30)
    else:
        if poi_section:
            report_content = poi_section

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
    return html_page("共享充电宝投放分析工具", body)
