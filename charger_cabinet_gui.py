import streamlit as st
import pandas as pd
import sys
from pathlib import Path
import io

# 将当前目录加入模块路径以导入之前的逻辑
sys.path.append(str(Path(__file__).parent))
import charger_cabinet_planner as planner

# 缓存搜索结果以提高性能和稳定性
@st.cache_data(ttl=3600)
def cached_wikidata_search(query):
    return planner.wikidata_search(query, limit=10)

@st.cache_data(ttl=3600)
def cached_wikidata_population(qid):
    return planner.wikidata_population(qid)

@st.cache_data(ttl=3600)
def cached_wikidata_first_entity(qid):
    return planner.wikidata_first_entity(qid)


@st.cache_data(ttl=3600)
def cached_wikidata_subdivisions(parent_qid):
    entity = planner.wikidata_first_entity(parent_qid)
    if not entity:
        return []
    return planner.wikidata_entity_list_qids_labels(entity, "P150", limit=120)


def format_population_wan(population: int) -> str:
    value = population / 10_000
    return f"{value:,.2f}"


def build_plans_table(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["地区", "人口(万)", "柜机数", "代理名额"])
    df = pd.DataFrame(rows)
    for col in ["人口(万)"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["柜机数", "代理名额"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df


def compute_area_rows(name: str, qid: str, population: int) -> tuple[list[dict], planner.AreaPlan]:
    plan = planner.plan_for_area(name, population)
    children = cached_wikidata_subdivisions(qid)
    rows = [
        {
            "地区": plan.name,
            "人口(万)": float(f"{population / 10_000:.2f}"),
            "柜机数": plan.cabinets_needed,
            "代理名额": plan.agent_slots,
            "_qid": qid,
        }
    ]

    total = len(children)
    progress = st.progress(0, text="正在拉取下一级行政区划人口…")
    fetched = 0
    for child_qid, child_label in children:
        child_pop = cached_wikidata_population(child_qid)
        if child_pop is not None:
            child_plan = planner.plan_for_area(child_label, int(child_pop))
            rows.append(
                {
                    "地区": child_plan.name,
                    "人口(万)": float(f"{int(child_pop) / 10_000:.2f}"),
                    "柜机数": child_plan.cabinets_needed,
                    "代理名额": child_plan.agent_slots,
                    "_qid": child_qid,
                }
            )
        fetched += 1
        if total > 0 and fetched % 3 == 0:
            progress.progress(min(1.0, fetched / total), text=f"正在拉取下一级行政区划人口… {fetched}/{total}")
    progress.progress(1.0, text="下一级行政区划人口拉取完成")

    return rows, plan


# 页面配置
st.set_page_config(
    page_title="共享充电宝投放分析工具",
    page_icon="🔋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 自定义样式
st.markdown("""
    <style>
    .main {
        background-color: #f5f7f9;
    }
    .stMetric {
        background-color: white;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .report-card {
        background-color: white;
        padding: 20px;
        border-radius: 10px;
        border-left: 5px solid #007bff;
        margin-bottom: 20px;
    }
    </style>
""", unsafe_allow_html=True)

# 侧边栏：参数配置
with st.sidebar:
    st.title("⚙️ 测算参数配置")
    people_per_cabinet = st.number_input("👥 人口/柜机 比例", value=planner.PEOPLE_PER_CABINET, min_value=1, step=10, help="多少人共用一台柜机")
    cabinets_per_agent = st.number_input("🤝 柜机/代理 比例", value=planner.CABINETS_PER_AGENT, min_value=1, step=10, help="一名代理负责多少台柜机")
    
    # 更新全局常量（针对当前运行环境）
    planner.PEOPLE_PER_CABINET = people_per_cabinet
    planner.CABINETS_PER_AGENT = cabinets_per_agent

    st.divider()
    st.markdown("### 关于工具")
    st.info("本工具支持联网搜索城市人口与面积，自动计算投放规模并生成简报。")

# 主界面
st.title("🔋 共享充电宝投放分析与测算")

tab1, tab2 = st.tabs(["🔍 单个地区分析", "📂 CSV 批量处理"])

with tab1:
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("📍 地区选择")
        search_query = st.text_input("🏢 输入城市或区县名称进行搜索", placeholder="例如：永康、北京、杭州西湖区...")
        
        selected_city = None
        if search_query:
            try:
                candidates = cached_wikidata_search(search_query)
                if candidates:
                    options = {f"{c.label} ({c.description or '无描述'})": c for c in candidates}
                    choice = st.selectbox("📍 请选择最匹配的项", options.keys())
                    selected_city = options[choice]
                else:
                    st.warning("⚠️ 未找到匹配地区，请换个关键词试试")
            except Exception as e:
                st.error(f"🌐 联网搜索出错: {e}")

        st.divider()
        
        pop_input = st.text_input("👥 人口数量", placeholder="例如 100万、350000 (留空则尝试自动查询)")
        st.checkbox("测算后自动生成简报（右侧）", value=True, key="auto_report_after_calc")
        calc_btn = st.button("🚀 开始分析测算", type="primary", use_container_width=True)

    with col2:
        if calc_btn:
            with st.spinner("⏳ 正在拉取人口并生成测算表..."):
                try:
                    name = selected_city.label if selected_city else (search_query if search_query else "未命名地区")
                    qid = selected_city.qid if selected_city else None
                    if not qid:
                        st.error("⚠️ 请先选择最匹配的地区（带下拉候选的那一步）")
                        st.stop()

                    population: int | None = None
                    if pop_input:
                        population = planner.parse_population(pop_input)
                    else:
                        population = cached_wikidata_population(qid)
                    if population is None:
                        st.error("❌ 无法自动获取该地区人口，请手动输入人口数")
                        st.stop()

                    rows, plan = compute_area_rows(name=name, qid=qid, population=int(population))
                    st.session_state["calc_rows"] = rows
                    st.session_state["calc_parent"] = {"name": plan.name, "qid": qid, "population": int(population)}
                    st.session_state.pop("last_report", None)
                    st.session_state.pop("last_report_name", None)
                    if bool(st.session_state.get("auto_report_after_calc", True)):
                        entity = cached_wikidata_first_entity(str(qid)) if qid else None
                        with st.spinner("⏳ 正在生成简报..."):
                            report = planner.build_area_report(plan, str(qid), entity)
                        st.session_state["last_report"] = report
                        st.session_state["last_report_name"] = plan.name
                except Exception as e:
                    import traceback

                    st.error(f"❌ 分析过程中出错: {str(e)}")
                    st.expander("错误详情").code(traceback.format_exc())

        rows = st.session_state.get("calc_rows")
        parent = st.session_state.get("calc_parent")
        if not rows or not parent:
            st.info("在左侧输入地区并点击按钮开始分析")
            st.stop()

        population = int(parent.get("population") or 0)
        plan = planner.plan_for_area(str(parent.get("name") or "未命名地区"), population)
        qid = str(parent.get("qid") or "")

        left, right = st.columns([1.35, 1])

        with left:
            st.subheader("📊 核心测算结果")
            m1, m2, m3 = st.columns(3)
            m1.metric("👥 人口总数(万)", f"{format_population_wan(population)}")
            m2.metric("🔋 建议柜机数", f"{plan.cabinets_needed:,}")
            m3.metric("🤝 代理名额", f"{plan.agent_slots:,}")

            st.divider()

            st.subheader("🏘️ 本地区 & 下一级行政区划测算表")
            df = build_plans_table(rows).drop(columns=["_qid"], errors="ignore").sort_values(by="人口(万)", ascending=False)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.download_button(
                "📥 下载测算表 (CSV)",
                df.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{plan.name}_测算表.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with right:
            st.subheader("📝 生成详细分析简报")
            options = {r["地区"]: r for r in rows if r.get("地区")}
            option_names = list(options.keys())
            default_idx = 0
            if plan.name in option_names:
                default_idx = option_names.index(plan.name)
            target_name = st.selectbox("选择要生成简报的地区", option_names, index=default_idx, key="report_target")

            if st.button("📄 生成/刷新简报", type="secondary", use_container_width=True):
                target = options[target_name]
                target_qid = target.get("_qid")
                target_pop_wan = target.get("人口(万)")
                target_pop: int | None = None
                if isinstance(target_pop_wan, (int, float)):
                    target_pop = int(round(float(target_pop_wan) * 10_000))
                if (target_pop is None or target_pop <= 0) and target_qid:
                    fetched = cached_wikidata_population(str(target_qid))
                    if fetched is not None:
                        target_pop = int(fetched)
                if target_pop is None or target_pop <= 0:
                    st.error("❌ 该地区人口缺失，无法生成简报（请先补齐人口或换一个地区）")
                    st.stop()
                target_plan = planner.plan_for_area(target_name, int(target_pop))
                entity = cached_wikidata_first_entity(str(target_qid)) if target_qid else None
                with st.spinner("⏳ 正在生成简报..."):
                    report = planner.build_area_report(target_plan, str(target_qid) if target_qid else None, entity)
                st.session_state["last_report"] = report
                st.session_state["last_report_name"] = target_name

            report = st.session_state.get("last_report")
            report_name = st.session_state.get("last_report_name") or target_name
            if report:
                st.text_area("分析结果", value=report, height=520)
                st.download_button(
                    "📥 下载简报文本",
                    report,
                    file_name=f"{report_name}_投放分析报告.txt",
                    mime="text/plain",
                    use_container_width=True,
                )
            else:
                st.info("点击“生成/刷新简报”，简报会显示在右侧。")

with tab2:
    st.subheader("📂 批量上传 CSV 文件进行计算")
    uploaded_file = st.file_uploader("📁 选择 CSV 文件", type=["csv"])
    
    if uploaded_file is not None:
        try:
            content = uploaded_file.read().decode("utf-8-sig")
            f = io.StringIO(content)
            
            reader = pd.read_csv(f)
            st.write("📄 文件预览：", reader.head())
            
            name_cols = [c for c in reader.columns if str(c).lower() in {"name", "city", "area", "地区", "城市", "名称"}]
            pop_cols = [c for c in reader.columns if str(c).lower() in {"population", "pop", "people", "人口", "人数"}]
            
            if not name_cols or not pop_cols:
                st.error("⚠️ CSV 必须包含地区名称列和人口列")
            else:
                name_key = name_cols[0]
                pop_key = pop_cols[0]
                
                if st.button("⚡ 开始批量计算", use_container_width=True):
                    results = []
                    failed = 0
                    for _, row in reader.iterrows():
                        name = str(row[name_key])
                        try:
                            pop_str = str(row[pop_key])
                            pop = planner.parse_population(pop_str)
                            plan = planner.plan_for_area(name, pop)
                            results.append({
                                "地区": plan.name,
                                "人口(万)": float(f"{plan.population / 10_000:.2f}"),
                                "🔋 柜机数量": plan.cabinets_needed,
                                "🤝 代理名额": plan.agent_slots
                            })
                        except Exception:
                            failed += 1
                            continue
                    
                    res_df = pd.DataFrame(results)
                    st.session_state["batch_results_df"] = res_df
                    st.session_state["batch_failed"] = failed

                res_df = st.session_state.get("batch_results_df")
                if isinstance(res_df, pd.DataFrame) and not res_df.empty:
                    failed = int(st.session_state.get("batch_failed") or 0)
                    suffix = f"，跳过 {failed} 条无效行" if failed else ""
                    st.success(f"✅ 成功处理 {len(res_df)} 条数据{suffix}")
                    st.dataframe(res_df, use_container_width=True, hide_index=True)
                    csv_data = res_df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "📥 下载计算结果 (CSV)",
                        csv_data,
                        "批量计算结果.csv",
                        "text/csv",
                        use_container_width=True,
                    )
                    
        except Exception as e:
            st.error(f"处理 CSV 失败: {e}")
