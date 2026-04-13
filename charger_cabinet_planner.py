from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PEOPLE_PER_CABINET = 100
CABINETS_PER_AGENT = 200
HTTP_USER_AGENT = "charger-cabinet-planner/1.0 (contact: local-script)"
WIKIDATA_LANG = "zh"
WIKIPEDIA_LANG = "zh"


@dataclass(frozen=True)
class AreaPlan:
    name: str
    population: int
    cabinets_needed: int
    agent_slots: int


@dataclass(frozen=True)
class WikidataCandidate:
    qid: str
    label: str
    description: str


@dataclass(frozen=True)
class WikidataEntity:
    qid: str
    label: str
    description: str
    claims: dict


@dataclass(frozen=True)
class WikipediaSummary:
    title: str
    extract: str
    url: str


def unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def ceil_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be > 0")
    if numerator <= 0:
        return 0
    return (numerator + denominator - 1) // denominator


def parse_population(value: str) -> int:
    raw = value.strip().replace(",", "").replace("_", "")
    if not raw:
        raise ValueError("人口不能为空")

    multiplier = 1
    if raw.endswith("万"):
        multiplier = 10_000
        raw = raw[:-1].strip()
    elif raw.endswith("亿"):
        multiplier = 100_000_000
        raw = raw[:-1].strip()

    if not raw:
        raise ValueError("人口数格式不正确")

    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"无法解析人口数: {value!r}") from exc

    population = int(round(number * multiplier))
    if population < 0:
        raise ValueError("人口不能为负数")
    return population


def plan_for_area(name: str, population: int) -> AreaPlan:
    cabinets = ceil_div(population, PEOPLE_PER_CABINET)
    agents = ceil_div(cabinets, CABINETS_PER_AGENT)
    return AreaPlan(
        name=name.strip() or "未命名地区",
        population=population,
        cabinets_needed=cabinets,
        agent_slots=agents,
    )


def plans_from_csv(path: Path) -> list[AreaPlan]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV 文件缺少表头")

        name_key = next((k for k in reader.fieldnames if k.lower() in {"name", "city", "area", "地区", "城市", "名称"}), None)
        pop_key = next(
            (k for k in reader.fieldnames if k.lower() in {"population", "pop", "people", "人口", "人数"}), None
        )
        if not name_key or not pop_key:
            raise ValueError("CSV 需要包含列: name/地区/城市/名称 以及 population/人口/人数")

        plans: list[AreaPlan] = []
        for row in reader:
            name = (row.get(name_key) or "").strip()
            pop_raw = (row.get(pop_key) or "").strip()
            if not name and not pop_raw:
                continue
            population = parse_population(pop_raw)
            plans.append(plan_for_area(name=name, population=population))
        return plans


def http_get_json(url: str, params: dict[str, str]) -> object:
    encoded = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url=f"{url}?{encoded}",
        headers={"User-Agent": HTTP_USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def http_get_json_raw(url: str) -> object:
    request = urllib.request.Request(
        url=url,
        headers={"User-Agent": HTTP_USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def wikidata_search(term: str, limit: int = 20, language: str = "zh") -> list[WikidataCandidate]:
    if not term.strip():
        return []

    data = http_get_json(
        "https://www.wikidata.org/w/api.php",
        {
            "action": "wbsearchentities",
            "format": "json",
            "language": language,
            "uselang": language,
            "search": term.strip(),
            "limit": str(limit),
        },
    )
    if not isinstance(data, dict):
        return []

    results = data.get("search")
    if not isinstance(results, list):
        return []

    candidates: list[WikidataCandidate] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        description = str(item.get("description") or "").strip()
        if qid and label:
            candidates.append(WikidataCandidate(qid=qid, label=label, description=description))
    return candidates


def wikidata_get_entities(qids: list[str], language: str = WIKIDATA_LANG) -> dict[str, WikidataEntity]:
    cleaned = [q.strip() for q in qids if q and q.strip()]
    if not cleaned:
        return {}

    data = http_get_json(
        "https://www.wikidata.org/w/api.php",
        {
            "action": "wbgetentities",
            "format": "json",
            "ids": "|".join(cleaned),
            "props": "labels|descriptions|claims",
            "languages": language,
            "languagefallback": "1",
        },
    )
    if not isinstance(data, dict):
        return {}

    entities = data.get("entities")
    if not isinstance(entities, dict):
        return {}

    out: dict[str, WikidataEntity] = {}
    for qid, entity in entities.items():
        if not isinstance(entity, dict):
            continue
        labels = entity.get("labels")
        descriptions = entity.get("descriptions")
        claims = entity.get("claims")
        label = ""
        description = ""
        if isinstance(labels, dict):
            zh = labels.get(language)
            if isinstance(zh, dict):
                label = str(zh.get("value") or "")
        if isinstance(descriptions, dict):
            zh = descriptions.get(language)
            if isinstance(zh, dict):
                description = str(zh.get("value") or "")
        if not isinstance(claims, dict):
            claims = {}
        out[qid] = WikidataEntity(qid=qid, label=label or qid, description=description, claims=claims)
    return out


def wikidata_first_entity(qid: str, language: str = WIKIDATA_LANG) -> WikidataEntity | None:
    entities = wikidata_get_entities([qid], language=language)
    return entities.get(qid)


def parse_wikidata_time(value: str) -> tuple[int, int, int]:
    raw = value.strip()
    if raw.startswith("+"):
        raw = raw[1:]
    date_part = raw.split("T", 1)[0]
    parts = date_part.split("-")
    year = int(parts[0]) if len(parts) >= 1 and parts[0].isdigit() else 0
    month = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    day = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
    return (year, month, day)


def wikidata_population(qid: str) -> int | None:
    qid = qid.strip()
    if not qid:
        return None

    data = http_get_json(
        "https://www.wikidata.org/w/api.php",
        {
            "action": "wbgetentities",
            "format": "json",
            "ids": qid,
            "props": "claims",
        },
    )
    if not isinstance(data, dict):
        return None
    entities = data.get("entities")
    if not isinstance(entities, dict):
        return None
    entity = entities.get(qid)
    if not isinstance(entity, dict):
        return None
    claims = entity.get("claims")
    if not isinstance(claims, dict):
        return None

    population_claims = claims.get("P1082")
    if not isinstance(population_claims, list) or not population_claims:
        return None

    best_amount: int | None = None
    best_date: tuple[int, int, int] = (0, 0, 0)

    for claim in population_claims:
        if not isinstance(claim, dict):
            continue
        mainsnak = claim.get("mainsnak")
        if not isinstance(mainsnak, dict):
            continue
        datavalue = mainsnak.get("datavalue")
        if not isinstance(datavalue, dict):
            continue
        value = datavalue.get("value")
        if not isinstance(value, dict):
            continue
        amount_raw = value.get("amount")
        if not isinstance(amount_raw, str):
            continue
        try:
            amount = int(float(amount_raw))
        except Exception:
            continue

        date = (0, 0, 0)
        qualifiers = claim.get("qualifiers")
        if isinstance(qualifiers, dict):
            times = qualifiers.get("P585")
            if isinstance(times, list) and times:
                t0 = times[0]
                if isinstance(t0, dict):
                    dv = t0.get("datavalue")
                    if isinstance(dv, dict):
                        v = dv.get("value")
                        if isinstance(v, dict):
                            time_value = v.get("time")
                            if isinstance(time_value, str):
                                date = parse_wikidata_time(time_value)

        if date > best_date:
            best_date = date
            best_amount = amount

    return best_amount


def parse_wikidata_quantity_amount(claim: dict) -> float | None:
    if not isinstance(claim, dict):
        return None
    mainsnak = claim.get("mainsnak")
    if not isinstance(mainsnak, dict):
        return None
    datavalue = mainsnak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    value = datavalue.get("value")
    if not isinstance(value, dict):
        return None
    amount_raw = value.get("amount")
    if not isinstance(amount_raw, str):
        return None
    try:
        return float(amount_raw)
    except Exception:
        return None


def wikidata_area_km2(entity: WikidataEntity) -> float | None:
    claims = entity.claims
    if not isinstance(claims, dict):
        return None
    area_claims = claims.get("P2046")
    if not isinstance(area_claims, list) or not area_claims:
        return None

    best: float | None = None
    best_rank = 0
    for c in area_claims:
        if not isinstance(c, dict):
            continue
        rank = 2 if c.get("rank") == "preferred" else 1 if c.get("rank") == "normal" else 0
        amount = parse_wikidata_quantity_amount(c)
        if amount is None:
            continue
        if best is None or rank > best_rank or (rank == best_rank and amount > best):
            best = amount
            best_rank = rank

    if best is None:
        return None

    if best <= 0:
        return None
    return best


def wikidata_admin_path_qids(entity: WikidataEntity, max_depth: int = 6) -> list[str]:
    claims = entity.claims
    if not isinstance(claims, dict):
        return []
    parents = claims.get("P131")
    if not isinstance(parents, list) or not parents:
        return []

    path: list[str] = []
    current_claims = parents
    depth = 0
    while depth < max_depth and isinstance(current_claims, list) and current_claims:
        first = current_claims[0]
        if not isinstance(first, dict):
            break
        mainsnak = first.get("mainsnak")
        if not isinstance(mainsnak, dict):
            break
        datavalue = mainsnak.get("datavalue")
        if not isinstance(datavalue, dict):
            break
        value = datavalue.get("value")
        if not isinstance(value, dict):
            break
        qid = value.get("id")
        if not isinstance(qid, str) or not qid.strip():
            break
        qid = qid.strip()
        if qid in path:
            break
        path.append(qid)
        parent_entity = wikidata_first_entity(qid, language=WIKIDATA_LANG)
        if not parent_entity:
            break
        current_claims = parent_entity.claims.get("P131") if isinstance(parent_entity.claims, dict) else None
        depth += 1
    return path


def wikidata_claim_list(entity: WikidataEntity, prop: str) -> list[dict]:
    claims = entity.claims
    if not isinstance(claims, dict):
        return []
    items = claims.get(prop)
    if not isinstance(items, list):
        return []
    return [c for c in items if isinstance(c, dict)]


def wikidata_claim_rank(claim: dict) -> int:
    rank = claim.get("rank")
    if rank == "preferred":
        return 2
    if rank == "normal":
        return 1
    return 0


def wikidata_claim_point_in_time(claim: dict) -> tuple[int, int, int]:
    qualifiers = claim.get("qualifiers")
    if not isinstance(qualifiers, dict):
        return (0, 0, 0)
    times = qualifiers.get("P585")
    if not isinstance(times, list) or not times:
        return (0, 0, 0)
    t0 = times[0]
    if not isinstance(t0, dict):
        return (0, 0, 0)
    dv = t0.get("datavalue")
    if not isinstance(dv, dict):
        return (0, 0, 0)
    v = dv.get("value")
    if not isinstance(v, dict):
        return (0, 0, 0)
    time_value = v.get("time")
    if not isinstance(time_value, str):
        return (0, 0, 0)
    return parse_wikidata_time(time_value)


def wikidata_claim_entity_qid(claim: dict) -> str | None:
    mainsnak = claim.get("mainsnak")
    if not isinstance(mainsnak, dict):
        return None
    datavalue = mainsnak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    value = datavalue.get("value")
    if not isinstance(value, dict):
        return None
    qid = value.get("id")
    if not isinstance(qid, str):
        return None
    qid = qid.strip()
    return qid or None


def wikidata_claim_string(claim: dict) -> str | None:
    mainsnak = claim.get("mainsnak")
    if not isinstance(mainsnak, dict):
        return None
    datavalue = mainsnak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    value = datavalue.get("value")
    if isinstance(value, str):
        return value.strip() or None
    return None


def wikidata_claim_time(claim: dict) -> tuple[int, int, int] | None:
    mainsnak = claim.get("mainsnak")
    if not isinstance(mainsnak, dict):
        return None
    datavalue = mainsnak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    value = datavalue.get("value")
    if not isinstance(value, dict):
        return None
    time_value = value.get("time")
    if not isinstance(time_value, str):
        return None
    return parse_wikidata_time(time_value)


def wikidata_claim_coordinate(claim: dict) -> tuple[float, float] | None:
    mainsnak = claim.get("mainsnak")
    if not isinstance(mainsnak, dict):
        return None
    datavalue = mainsnak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    value = datavalue.get("value")
    if not isinstance(value, dict):
        return None
    lat = value.get("latitude")
    lon = value.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return (float(lat), float(lon))


def wikidata_claim_quantity(claim: dict) -> tuple[float, str | None] | None:
    mainsnak = claim.get("mainsnak")
    if not isinstance(mainsnak, dict):
        return None
    datavalue = mainsnak.get("datavalue")
    if not isinstance(datavalue, dict):
        return None
    value = datavalue.get("value")
    if not isinstance(value, dict):
        return None
    amount_raw = value.get("amount")
    if not isinstance(amount_raw, str):
        return None
    try:
        amount = float(amount_raw)
    except Exception:
        return None
    unit_raw = value.get("unit")
    unit_qid: str | None = None
    if isinstance(unit_raw, str) and unit_raw.startswith("http"):
        unit_qid = unit_raw.rsplit("/", 1)[-1].strip() or None
        if unit_qid == "1":
            unit_qid = None
    return (amount, unit_qid)


def wikidata_best_quantity(entity: WikidataEntity, prop: str) -> tuple[float, str | None, tuple[int, int, int]]:
    best_amount: float | None = None
    best_unit: str | None = None
    best_date: tuple[int, int, int] = (0, 0, 0)
    best_rank = -1
    for c in wikidata_claim_list(entity, prop):
        qty = wikidata_claim_quantity(c)
        if qty is None:
            continue
        amount, unit_qid = qty
        date = wikidata_claim_point_in_time(c)
        rank = wikidata_claim_rank(c)
        if best_amount is None or rank > best_rank or (rank == best_rank and date > best_date):
            best_amount = amount
            best_unit = unit_qid
            best_date = date
            best_rank = rank
    if best_amount is None:
        return (0.0, None, (0, 0, 0))
    return (best_amount, best_unit, best_date)


def wikidata_best_string(entity: WikidataEntity, prop: str) -> str | None:
    best: str | None = None
    best_rank = -1
    for c in wikidata_claim_list(entity, prop):
        s = wikidata_claim_string(c)
        if not s:
            continue
        rank = wikidata_claim_rank(c)
        if best is None or rank > best_rank:
            best = s
            best_rank = rank
    return best


def wikidata_best_entity_label(entity: WikidataEntity, prop: str) -> str | None:
    qids: list[str] = []
    best_rank = -1
    for c in wikidata_claim_list(entity, prop):
        q = wikidata_claim_entity_qid(c)
        if not q:
            continue
        rank = wikidata_claim_rank(c)
        if rank > best_rank:
            qids = [q]
            best_rank = rank
    if not qids:
        return None
    entities = wikidata_get_entities(qids, language=WIKIDATA_LANG)
    if qids[0] in entities:
        return entities[qids[0]].label
    return None


def wikidata_entity_list_labels(entity: WikidataEntity, prop: str, limit: int = 30) -> list[str]:
    qids: list[str] = []
    for c in wikidata_claim_list(entity, prop):
        q = wikidata_claim_entity_qid(c)
        if q:
            qids.append(q)
        if len(qids) >= limit:
            break
    if not qids:
        return []
    entities = wikidata_get_entities(unique_preserve_order(qids), language=WIKIDATA_LANG)
    labels: list[str] = []
    for q in qids:
        e = entities.get(q)
        if e and e.label:
            labels.append(e.label)
    return unique_preserve_order(labels)


def wikidata_entity_list_qids_labels(entity: WikidataEntity, prop: str, limit: int = 80) -> list[tuple[str, str]]:
    qids: list[str] = []
    for c in wikidata_claim_list(entity, prop):
        q = wikidata_claim_entity_qid(c)
        if q:
            qids.append(q)
        if len(qids) >= limit:
            break
    if not qids:
        return []

    entities = wikidata_get_entities(unique_preserve_order(qids), language=WIKIDATA_LANG)
    out: list[tuple[str, str]] = []
    for q in qids:
        e = entities.get(q)
        if e and e.label:
            out.append((q, e.label))
    return unique_preserve_order(out)


def fmt_date(date: tuple[int, int, int]) -> str:
    y, m, d = date
    if y <= 0:
        return "—"
    if m <= 0:
        return f"{y}"
    if d <= 0:
        return f"{y}-{m:02d}"
    return f"{y}-{m:02d}-{d:02d}"


def fmt_int(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


def fmt_float(n: float | None, digits: int = 2) -> str:
    if n is None:
        return "—"
    return f"{n:,.{digits}f}"


def build_area_report(
    plan: AreaPlan,
    qid: str | None,
    entity: WikidataEntity | None,
) -> str:
    lines: list[str] = []
    title = plan.name
    if qid:
        title = f"{title} ({qid})"
    lines.append(title)
    lines.append("=" * len(title))
    lines.append("")

    area_km2: float | None = None
    density: float | None = None
    admin_path_labels: list[str] = []
    basic_fields: dict[str, str] = {}
    tags: list[str] = []
    wikipedia: WikipediaSummary | None = None
    subdivs: list[str] = []
    instance_of: list[str] = []
    gdp_value: float | None = None
    gdp_date: tuple[int, int, int] = (0, 0, 0)
    gdp_unit_label: str | None = None
    if entity:
        area_km2 = wikidata_area_km2(entity)
        if area_km2 and plan.population > 0:
            density = plan.population / area_km2

        try:
            admin_qids = wikidata_admin_path_qids(entity)
            if admin_qids:
                entities = wikidata_get_entities(admin_qids, language=WIKIDATA_LANG)
                admin_path_labels = [entities[q].label for q in admin_qids if q in entities and entities[q].label]
        except Exception:
            admin_path_labels = []

        try:
            instance_of = wikidata_entity_list_labels(entity, "P31", limit=10)
        except Exception:
            instance_of = []

        coord: tuple[float, float] | None = None
        for c in wikidata_claim_list(entity, "P625"):
            coord = wikidata_claim_coordinate(c)
            if coord:
                break

        elevation_m: float | None = None
        elev_amount, elev_unit, elev_date = wikidata_best_quantity(entity, "P2044")
        if elev_amount > 0:
            elevation_m = elev_amount

        inception: tuple[int, int, int] | None = None
        for c in wikidata_claim_list(entity, "P571"):
            inception = wikidata_claim_time(c)
            if inception:
                break

        website = wikidata_best_string(entity, "P856")
        postal_code = wikidata_best_string(entity, "P281")
        dialing_code = wikidata_best_string(entity, "P473")
        vehicle_code = wikidata_best_string(entity, "P395")
        timezone = wikidata_best_entity_label(entity, "P421")
        capital = wikidata_best_entity_label(entity, "P36")

        if coord:
            basic_fields["坐标"] = f"{coord[0]:.5f}, {coord[1]:.5f}"
        if elevation_m is not None:
            basic_fields["海拔(m)"] = fmt_float(elevation_m, digits=0)
        if inception:
            basic_fields["设立/成立"] = fmt_date(inception)
        if timezone:
            basic_fields["时区"] = timezone
        if capital:
            basic_fields["行政中心/治所"] = capital
        if postal_code:
            basic_fields["邮编"] = postal_code
        if dialing_code:
            basic_fields["电话区号"] = dialing_code
        if vehicle_code:
            basic_fields["车牌代码"] = vehicle_code
        if website:
            basic_fields["官网"] = website

        try:
            subdivs = wikidata_entity_list_labels(entity, "P150", limit=80)
        except Exception:
            subdivs = []

        try:
            gdp_amount, gdp_unit, gdp_date = wikidata_best_quantity(entity, "P2131")
            if gdp_amount != 0.0:
                gdp_value = gdp_amount
                if gdp_unit:
                    unit_entity = wikidata_first_entity(gdp_unit, language=WIKIDATA_LANG)
                    gdp_unit_label = unit_entity.label if unit_entity else None
        except Exception:
            gdp_value = None

        try:
            wiki_title = wikipedia_search_title(entity.label or plan.name, language=WIKIPEDIA_LANG)
            if wiki_title:
                wikipedia = wikipedia_summary(wiki_title, language=WIKIPEDIA_LANG)
        except Exception:
            wikipedia = None

        if wikipedia and wikipedia.extract:
            tags = extract_tags(wikipedia.extract + " " + (entity.description or ""))
        else:
            tags = extract_tags(entity.description or "")

    lines.append("一、城市画像（尽可能多字段，自动生成）")
    if wikipedia and wikipedia.extract:
        summary_lines = [s.strip() for s in wikipedia.extract.splitlines() if s.strip()]
        summary = summary_lines[0] if summary_lines else wikipedia.extract.strip()
        if summary:
            lines.append(f"- 概览：{summary}")
    if wikipedia and wikipedia.url:
        lines.append(f"- 参考：{wikipedia.url}")
    if instance_of:
        lines.append(f"- 类型：{'、'.join(instance_of[:8])}")
    if tags:
        lines.append(f"- 产业/标签：{'、'.join(tags)}")
    if gdp_value is not None:
        unit = f" {gdp_unit_label}" if gdp_unit_label else ""
        date = fmt_date(gdp_date)
        suffix = f"（统计期：{date}）" if date != "—" else ""
        lines.append(f"- GDP（Wikidata）：{fmt_float(gdp_value, digits=0)}{unit}{suffix}")
    if entity and entity.description and not (wikipedia and wikipedia.extract):
        lines.append(f"- 备注：{entity.description}")
    lines.append("")

    lines.append("二、核心指标")
    lines.append(f"- 人口：{fmt_int(plan.population)}")
    lines.append(f"- 面积(km²)：{fmt_float(area_km2, digits=2)}")
    lines.append(f"- 人口密度(人/km²)：{fmt_float(density, digits=0)}")
    if admin_path_labels:
        lines.append(f"- 行政隶属：{' / '.join(admin_path_labels)}")
    if basic_fields:
        for k in ["坐标", "海拔(m)", "设立/成立", "时区", "行政中心/治所", "邮编", "电话区号", "车牌代码", "官网"]:
            v = basic_fields.get(k)
            if v:
                lines.append(f"- {k}：{v}")
    lines.append("")

    lines.append("三、资源测算")
    lines.append(f"- 充电宝柜机（按 {PEOPLE_PER_CABINET}:1）：{fmt_int(plan.cabinets_needed)} 台")
    lines.append(f"- 代理名额（按 {CABINETS_PER_AGENT}:1）：{fmt_int(plan.agent_slots)} 个")
    if area_km2 and area_km2 > 0:
        cabinets_per_km2 = plan.cabinets_needed / area_km2
        lines.append(f"- 柜机密度（台/km²）：{fmt_float(cabinets_per_km2, digits=2)}")
    lines.append("")

    lines.append("四、投放建议（可直接用于简报）")
    if density is None:
        lines.append("- 未获取到面积/密度数据：建议按商圈/交通枢纽/高校/医院/政务中心优先投放。")
        lines.append("- 第一阶段可先覆盖：核心商圈 + 交通枢纽（火车站/客运站/地铁口）+ 医院。")
    else:
        if density >= 6000:
            lines.append("- 高密度区域：优先布局地铁站点、写字楼、核心商圈与社区商业。")
            lines.append("- 先做“点位密集覆盖”，再扩展到社区与公共服务场景。")
        elif density >= 2000:
            lines.append("- 中密度区域：商圈/交通枢纽优先，其次覆盖高校、医院与政务中心。")
            lines.append("- 采用“主干场景 + 补盲点”策略，控制点位间距。")
        else:
            lines.append("- 低密度区域：优先高人流节点（车站、医院、核心商业街、景区）。")
            lines.append("- 采用“少量强点位”策略，先验证周转与收益再扩张。")
    lines.append("")

    if subdivs:
        def subarea_priority(name: str) -> int:
            order = ["街道", "开发区", "新区", "镇", "乡", "区", "县"]
            for i, key in enumerate(order):
                if key in name:
                    return i
            return len(order)

        selected = sorted(unique_preserve_order(subdivs), key=lambda s: (subarea_priority(s), s))[:8]
        lines.append("五、核心板块拆解（基于下辖单位自动挑选）")
        lines.append(f"- 下辖单位数量（Wikidata P150）：{len(unique_preserve_order(subdivs))}")
        lines.append(f"- 建议优先关注：{'、'.join(selected)}")
        lines.append("")

    lines.append("六、你可以补充的扩展数据（补全后更像你截图的深度分析）")
    lines.append("- GDP/社零/规上工业增加值/财政收入：统计公报或政府年鉴（更权威）")
    lines.append("- 产业集群与龙头企业：工信/招商资料、园区官网")
    lines.append("- 人流与消费场景：POI（商圈/交通枢纽/医院/高校/景区）+ 热力/客流（如有）")
    lines.append("- 竞品与点位资源：你已有点位库、BD清单、渠道覆盖情况")
    return "\n".join(lines)


def names_from_file(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"找不到名称文件: {path}")

    if path.suffix.lower() != ".csv":
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        names = [line.strip() for line in lines if line.strip()]
        return unique_preserve_order(names)

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("名称 CSV 文件缺少表头")

        name_key = next((k for k in reader.fieldnames if k.lower() in {"name", "city", "area", "地区", "城市", "名称"}), None)
        if not name_key:
            raise ValueError("名称 CSV 需要包含列: name/地区/城市/名称")

        names: list[str] = []
        for row in reader:
            name = (row.get(name_key) or "").strip()
            if name:
                names.append(name)
        return unique_preserve_order(names)


def format_plans(plans: Iterable[AreaPlan]) -> str:
    lines: list[str] = []
    for p in plans:
        lines.append(
            f"{p.name}\t人口: {p.population:,}\t柜机: {p.cabinets_needed:,}\t代理名额: {p.agent_slots:,}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按人口计算充电宝柜机数量(100:1)与代理名额(200:1)。支持人口单位: 万/亿。",
    )
    parser.add_argument("--name", "-n", help="城市/地区名称")
    parser.add_argument("--qid", help="Wikidata 实体ID（例如 北京市 Q956），用于精确生成报告/拉取人口")
    parser.add_argument("--population", "--pop", "-p", help="人口数，例如: 350000、35万、0.35亿")
    parser.add_argument("--csv", type=Path, help="批量计算：CSV 文件路径，需包含 name 与 population(或同义列)")
    parser.add_argument(
        "--names-file",
        type=Path,
        help="用于自动补全的名称文件（txt 每行一个名称；或 csv，需包含 name/地区/城市/名称 列）",
    )
    parser.add_argument(
        "--name-provider",
        choices=["none", "wikidata"],
        default="none",
        help="当未提供 --names-file 时，用于在线搜索/补全名称的提供方（wikidata 无需 key）",
    )
    parser.add_argument(
        "--auto-pop",
        action="store_true",
        help="交互模式下人口可留空，将尝试从在线提供方自动获取（需 --name-provider）",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="输出地区数据分析报告（文本），适合复制到简报/文档",
    )
    return parser


def wikipedia_search_title(term: str, language: str = WIKIPEDIA_LANG) -> str | None:
    term = term.strip()
    if not term:
        return None
    data = http_get_json(
        f"https://{language}.wikipedia.org/w/api.php",
        {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": term,
            "srlimit": "1",
            "utf8": "1",
        },
    )
    if not isinstance(data, dict):
        return None
    query = data.get("query")
    if not isinstance(query, dict):
        return None
    search = query.get("search")
    if not isinstance(search, list) or not search:
        return None
    first = search[0]
    if not isinstance(first, dict):
        return None
    title = first.get("title")
    if not isinstance(title, str):
        return None
    return title


def wikipedia_summary(title: str, language: str = WIKIPEDIA_LANG) -> WikipediaSummary | None:
    title = title.strip()
    if not title:
        return None
    encoded = urllib.parse.quote(title, safe="")
    data = http_get_json_raw(f"https://{language}.wikipedia.org/api/rest_v1/page/summary/{encoded}")
    if not isinstance(data, dict):
        return None
    extract = data.get("extract")
    if not isinstance(extract, str):
        extract = ""
    display_title = data.get("title")
    if not isinstance(display_title, str):
        display_title = title
    url = ""
    content_urls = data.get("content_urls")
    if isinstance(content_urls, dict):
        desktop = content_urls.get("desktop")
        if isinstance(desktop, dict):
            page = desktop.get("page")
            if isinstance(page, str):
                url = page
    return WikipediaSummary(title=display_title, extract=extract.strip(), url=url)


def extract_tags(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    keywords = [
        ("五金", "五金"),
        ("制造", "制造业"),
        ("制造业", "制造业"),
        ("工业", "工业"),
        ("电动工具", "电动工具"),
        ("门业", "门业"),
        ("汽车", "汽车"),
        ("电商", "电商"),
        ("跨境", "跨境"),
        ("外贸", "外贸"),
        ("旅游", "旅游"),
        ("景区", "旅游"),
        ("港口", "港口"),
        ("机场", "机场"),
        ("高铁", "高铁"),
        ("地铁", "地铁"),
        ("高校", "高校"),
        ("大学", "高校"),
        ("医院", "医疗"),
        ("工业园", "产业园"),
        ("开发区", "开发区"),
        ("新区", "新区"),
        ("县级市", "县级市"),
        ("地级市", "地级市"),
        ("省会", "省会"),
    ]
    tags: list[str] = []
    for k, t in keywords:
        if k in text:
            tags.append(t)
    return unique_preserve_order(tags)



def try_prompt_toolkit_name(names: list[str]) -> str | None:
    try:
        from prompt_toolkit import prompt
        from prompt_toolkit.completion import WordCompleter
    except Exception:
        return None

    completer = WordCompleter(names, ignore_case=True, match_middle=True)
    return prompt("请输入城市/地区名称: ", completer=completer).strip()


def try_readline_name(names: list[str]) -> str | None:
    try:
        import readline  # type: ignore
    except Exception:
        return None

    def complete(text: str, state: int) -> str | None:
        matches = [n for n in names if n.startswith(text)]
        if state < 0 or state >= len(matches):
            return None
        return matches[state]

    old_completer = readline.get_completer()
    try:
        readline.set_completer(complete)
        readline.parse_and_bind("tab: complete")
        return input("请输入城市/地区名称(按 Tab 补全): ").strip()
    finally:
        readline.set_completer(old_completer)


def prompt_name_with_fallback(names: list[str]) -> str:
    while True:
        value = input("请输入城市/地区名称(输入前缀后回车可选择): ").strip()
        if not value:
            return value

        matches = [n for n in names if n.startswith(value)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            return value

        shown = matches[:20]
        for i, item in enumerate(shown, 1):
            print(f"{i}. {item}")

        pick = input("匹配到多个名称，请输入序号(或直接输入完整名称): ").strip()
        if pick.isdigit():
            idx = int(pick)
            if 1 <= idx <= len(shown):
                return shown[idx - 1]
        if pick:
            return pick


def prompt_name_from_wikidata() -> tuple[str, str | None]:
    while True:
        query = input("请输入城市/地区名称(联网搜索，输入前缀或关键词): ").strip()
        if not query:
            return ("", None)

        try:
            candidates = wikidata_search(query, limit=20, language="zh")
        except Exception as e:
            print(f"联网搜索失败: {e}", file=sys.stderr)
            continue

        if not candidates:
            print("未找到匹配项，请换个关键词再试。", file=sys.stderr)
            continue

        for i, c in enumerate(candidates, 1):
            desc = f" - {c.description}" if c.description else ""
            print(f"{i}. {c.label}{desc} ({c.qid})")

        pick = input("请选择序号(或直接输入完整名称): ").strip()
        if pick.isdigit():
            idx = int(pick)
            if 1 <= idx <= len(candidates):
                chosen = candidates[idx - 1]
                return (chosen.label, chosen.qid)
        if pick:
            return (pick, None)


def prompt_interactive(
    names: list[str] | None = None,
    name_provider: str = "none",
    auto_pop: bool = False,
) -> tuple[AreaPlan, str | None]:
    name = ""
    qid: str | None = None
    if names:
        name = try_prompt_toolkit_name(names) or try_readline_name(names) or prompt_name_with_fallback(names)
    elif name_provider == "wikidata":
        name, qid = prompt_name_from_wikidata()
    else:
        name = input("请输入城市/地区名称: ").strip()

    while True:
        hint = "请输入人口数(支持 万/亿，例如 35万、0.35亿): "
        if auto_pop and name_provider != "none":
            hint = "请输入人口数(支持 万/亿，例如 35万、0.35亿；留空自动查询): "
        pop_raw = input(hint).strip()
        try:
            if auto_pop and not pop_raw and name_provider == "wikidata":
                resolved_qid = qid
                if not resolved_qid and name.strip():
                    candidates = wikidata_search(name, limit=5, language="zh")
                    if candidates:
                        resolved_qid = candidates[0].qid
                if resolved_qid:
                    population = wikidata_population(resolved_qid)
                    if population is not None:
                        return (plan_for_area(name=name, population=population), resolved_qid)
                raise ValueError("无法自动获取人口，请手动输入人口数")

            population = parse_population(pop_raw)
            return (plan_for_area(name=name, population=population), qid)
        except ValueError as e:
            print(f"输入有误: {e}", file=sys.stderr)


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.csv:
        plans = plans_from_csv(args.csv)
        print(format_plans(plans))
        return 0

    if args.population is None:
        names: list[str] | None = None
        if args.names_file:
            names = names_from_file(args.names_file)
        plan, selected_qid = prompt_interactive(names=names, name_provider=args.name_provider, auto_pop=args.auto_pop)
        if args.report:
            qid = args.qid or selected_qid
            entity = wikidata_first_entity(qid, language=WIKIDATA_LANG) if qid else None
            print(build_area_report(plan=plan, qid=qid, entity=entity))
        else:
            print(format_plans([plan]))
        return 0

    population = parse_population(args.population)
    plan = plan_for_area(name=args.name or "", population=population)
    if args.report:
        qid = args.qid
        if not qid and args.name_provider == "wikidata" and args.name:
            try:
                candidates = wikidata_search(args.name, limit=1, language=WIKIDATA_LANG)
                if candidates:
                    qid = candidates[0].qid
            except Exception:
                qid = None
        entity = wikidata_first_entity(qid, language=WIKIDATA_LANG) if qid else None
        print(build_area_report(plan=plan, qid=qid, entity=entity))
    else:
        print(format_plans([plan]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
