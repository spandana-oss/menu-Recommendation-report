"""
AI Menu Intelligence Platform — Streamlit app.

Run locally:
    pip install -r requirements.txt
    streamlit run app.py

Then open the local URL Streamlit prints (usually http://localhost:8501).

Build order (per the project plan): Overview + sidebar nav first, then the
Menu Refresh module end-to-end, then Menu Creation.
"""

import io

import streamlit as st
import pandas as pd

import analysis_engine as ae
import workflow_orchestrator as wo
import input_normalizer as inorm
import run_menu_creation as rmc

st.set_page_config(
    page_title="AI Menu Intelligence Platform",
    page_icon="🍽️",
    layout="wide",
)


# ----------------------------------------------------------------------
# Theme (session-scoped CSS override, not Streamlit's native theme engine)
# ----------------------------------------------------------------------
# NOTE: Streamlit's real theme (config.toml / the ⋮ menu → Settings → Theme)
# is compiled at server startup and can't be flipped live without a restart.
# This injects CSS on every rerun instead, so it survives download-button
# clicks and page switches within the same session but won't touch every
# native widget pixel-for-pixel — it's an approximation, not the real thing.
if "theme" not in st.session_state:
    st.session_state["theme"] = "dark"


def apply_theme(theme: str):
    if theme == "dark":
        bg, bg2, text, border, muted = "#0e1117", "#161a23", "#fafafa", "#30363d", "#9aa1ac"
    else:
        bg, bg2, text, border, muted = "#ffffff", "#f5f5f7", "#1c1c1e", "#d0d0d5", "#5c5c63"

    st.markdown(
        f"""
        <style>
        .stApp {{
            background-color: {bg} !important;
            color: {text} !important;
        }}

        /* Catch-all: any text-bearing element inside the app inherits the
           theme text color unless a more specific rule below overrides it.
           Portal-rendered elements (dropdown popovers, tooltips) attach to
           <body>, not .stApp, so they're targeted separately. */
        .stApp, .stApp p, .stApp span, .stApp label, .stApp li,
        .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5,
        .stApp small, .stApp div {{
            color: {text};
        }}

        section[data-testid="stSidebar"] {{
            background-color: {bg2} !important;
        }}
        section[data-testid="stSidebar"] * {{
            color: {text} !important;
        }}

        div[data-testid="stExpander"], div[data-testid="stMetric"],
        div[data-testid="stDataFrame"], div[data-testid="stStatusWidget"],
        div[data-testid="stAlert"] {{
            background-color: {bg2} !important;
            border: 1px solid {border};
            border-radius: 8px;
        }}

        .stTextInput input, .stNumberInput input,
        .stSelectbox div[data-baseweb="select"] > div {{
            background-color: {bg2} !important;
            color: {text} !important;
            border-color: {border} !important;
        }}

        /* File uploader dropzone card — not covered by the generic rules
           above because Streamlit ships it with its own fixed styling. */
        section[data-testid="stFileUploaderDropzone"],
        div[data-testid="stFileUploaderDropzone"] {{
            background-color: {bg2} !important;
            border: 1px dashed {border} !important;
        }}
        section[data-testid="stFileUploaderDropzone"] *,
        div[data-testid="stFileUploaderDropzone"] * {{
            color: {text} !important;
        }}
        section[data-testid="stFileUploaderDropzone"] small,
        div[data-testid="stFileUploaderDropzone"] small {{
            color: {muted} !important;
        }}
        div[data-testid="stFileUploaderDropzone"] button,
        section[data-testid="stFileUploaderDropzone"] button {{
            background-color: {bg} !important;
            color: {text} !important;
            border: 1px solid {border} !important;
        }}
        div[data-testid="stFileUploaderFile"] {{
            color: {text} !important;
        }}

        /* Buttons (upload's own button is handled above; this covers the
           regular st.button / st.download_button secondary style). */
        .stApp button[kind="secondary"] {{
            background-color: {bg2} !important;
            color: {text} !important;
            border: 1px solid {border} !important;
        }}

        /* Dropdown/select popovers render in a portal attached to <body>,
           outside .stApp, so they need their own top-level selector. */
        div[data-baseweb="popover"] {{
            background-color: {bg2} !important;
        }}
        div[data-baseweb="popover"] * {{
            color: {text} !important;
        }}
        li[role="option"]:hover {{
            background-color: {border} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


apply_theme(st.session_state["theme"])


# ----------------------------------------------------------------------
# Sidebar navigation
# ----------------------------------------------------------------------
PAGES = ["🏠 Overview", "🔄 Menu Refresh", "📋 Menu Creation", "⚙️ Settings"]

with st.sidebar:
    st.markdown("## AI Menu Intelligence")
    page = st.radio("Navigate", PAGES, label_visibility="collapsed")
    st.markdown("---")
    st.caption("Running locally · nothing leaves this machine")


# ----------------------------------------------------------------------
# Shared helper: preview every sheet of a generated workbook
# ----------------------------------------------------------------------
def _preview_workbook(workbook_bytes, key_prefix: str):
    """Show a sheet-by-sheet preview of an in-memory .xlsx workbook."""
    raw = workbook_bytes.getvalue() if hasattr(workbook_bytes, "getvalue") else workbook_bytes
    try:
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None)
    except Exception as e:
        st.caption(f"Couldn't render a workbook preview ({e}).")
        return

    if not sheets:
        return

    sheet_names = list(sheets.keys())
    if len(sheet_names) == 1:
        st.dataframe(sheets[sheet_names[0]], use_container_width=True, hide_index=True)
        return

    tabs = st.tabs(sheet_names)
    for tab, name in zip(tabs, sheet_names):
        with tab:
            st.dataframe(sheets[name], use_container_width=True, hide_index=True)


# ----------------------------------------------------------------------
# Overview
# ----------------------------------------------------------------------
def render_overview():
    st.title("🏠 Overview")
    st.markdown(
        "Two modules live in this app. **Menu Refresh** and **Menu Creation** "
        "are both wired up."
    )

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🔄 Menu Refresh")
        st.markdown(
            "- Four entry points: a finished analysis workbook, a raw "
            "item list, a Menu Intelligence workbook (financial + market "
            "data pre-merged), **or** a raw POS sales export that builds "
            "Menu Intelligence from scratch first\n"
            "- Popularity, profitability, forecasting, and the recommendation "
            "rules are validated exactly against a real menu\n"
            "- Occasion-role and duplicate detection are heuristic — flagged "
            "for review, not silently trusted\n"
            "- Generates the workbook (.xlsx) and the client HTML report\n"
            "- **Status: working** — open it from the sidebar"
        )
    with col2:
        st.subheader("📋 Menu Creation")
        st.markdown(
            "- Upload a restaurant file, optionally add a comparable-data file, then pick a ZCTA\n"
            "- Generate a brand-new menu using deterministic item sourcing from real comparable-store data\n"
            "- **Status: working** — open it from the sidebar"
        )

    st.markdown("---")
    st.caption(
        "Note on the raw-data path: REFRESH items get the new *price* "
        "computed automatically, but the creative content — what to name "
        "the upgraded dish, what to add, how to describe it — isn't "
        "generated. That's a judgment call. The workbook marks those "
        "fields `[NEEDS INPUT]`; the natural next step is handing the "
        "generated workbook to an assistant to draft them."
    )


# ----------------------------------------------------------------------
# Menu Refresh
# ----------------------------------------------------------------------
def render_menu_refresh():
    st.title("🔄 Menu Refresh")

    # If a result from an earlier run is still sitting in session_state,
    # show it (this is what survives download-button clicks) instead of
    # re-showing the upload form.
    if st.session_state.get("last_result") is not None:
        if st.button("🔄 Start a new Menu Refresh", key="mr_start_new"):
            st.session_state["last_result"] = None
            st.session_state["last_result_source"] = None
            st.rerun()
        _render_workflow_result(
            st.session_state["last_result"],
            st.session_state.get("last_result_source", "your file"),
        )
        return

    st.markdown(
        "Upload your menu data. The system figures out what it's looking at, "
        "fetches whatever market data it needs, runs the full analysis, and "
        "hands you back the refreshed menu, the workbook, and the report."
    )

    with st.expander("What counts as 'menu data', and what happens automatically", expanded=False):
        st.markdown(
            "Upload **any one** of these — the file is auto-detected, no need "
            "to say which kind it is:\n"
            "- A raw POS sales export (one row per item per month)\n"
            "- A raw item list (name/category/qty/price/cost)\n"
            "- A Menu Intelligence workbook (financial + market data pre-merged)\n"
            "- A finished Menu Refresh analysis workbook\n\n"
            "**What runs automatically, in order:** market-data fetch (Census "
            "ACS5 residents + LODES commuter flow, if the file doesn't already "
            "have it embedded) → role classification → duplicate detection → "
            "the full popularity/profitability/commuter/forecast engine → "
            "the Keep/Refresh/Remove decision for every item → for every "
            "REMOVE, a replacement brief identifying the market gap it leaves "
            "→ the workbook → the executive report.\n\n"
            "**What's genuinely automated vs. estimated vs. not generated at "
            "all** — see the README's confidence table. Short version: the "
            "scoring/forecast/decision math is validated exactly against real "
            "data; occasion-role and duplicate detection are heuristic and "
            "flagged for review; REFRESH/REPLACE creative content (new dish "
            "names, descriptions, ingredients) is a judgment call the "
            "engine deliberately doesn't invent — it marks those fields "
            "for follow-up instead of guessing."
        )

    uploaded = st.file_uploader(
        "Menu data (.csv or .xlsx)",
        type=["csv", "xlsx"],
        key="orchestrator_uploader",
    )

    zcta = st.text_input(
        "ZIP / ZCTA (only needed if this file has no market data embedded, "
        "and you're not entering it manually below)",
        value="", max_chars=5, key="orchestrator_zcta",
    )

    with st.expander("Only if market-data fetch fails or isn't wanted: enter it manually", expanded=False):
        st.caption("Leave all at 0 to skip this — the system will try to fetch automatically instead.")
        c1, c2, c3 = st.columns(3)
        with c1:
            residents = st.number_input("Residents", value=0.0, min_value=0.0, key="man_res")
            daytime_workers = st.number_input("Daytime workers", value=0.0, min_value=0.0, key="man_dw")
        with c2:
            worker_inflow = st.number_input("Worker inflow", value=0.0, min_value=0.0, key="man_wi")
            resident_outflow = st.number_input("Resident outflow", value=0.0, min_value=0.0, key="man_ro")
            stay_local = st.number_input("Stay-local residents", value=0.0, min_value=0.0, key="man_sl")
        with c3:
            pct_income_high = st.number_input("% inflow >$3,333/mo", value=0.0, min_value=0.0, max_value=100.0, key="man_pih")
            pct_income_low = st.number_input("% inflow <$1,250/mo", value=0.0, min_value=0.0, max_value=100.0, key="man_pil")
            pct_age_mid = st.number_input("% inflow age 30-54", value=0.0, min_value=0.0, max_value=100.0, key="man_pam")
            pct_age_senior = st.number_input("% inflow age 55+", value=0.0, min_value=0.0, max_value=100.0, key="man_pas")

    manual_market = None
    if residents > 0:
        manual_market = ae.MarketIntel(
            residents=residents, daytime_workers=daytime_workers, worker_inflow=worker_inflow,
            resident_outflow=resident_outflow, stay_local=stay_local,
            pct_income_high=pct_income_high, pct_income_low=pct_income_low,
            pct_age_mid=pct_age_mid, pct_age_senior=pct_age_senior, pct_office_jobs=0.0,
        )

    if not st.button("🚀 Start Menu Refresh", type="primary", use_container_width=True):
        return

    if uploaded is None:
        st.error("Upload your menu data first.")
        return

    log_lines = []
    with st.status("Running Menu Refresh…", expanded=True) as status:
        log_placeholder = st.empty()

        def progress(message, fraction=None):
            log_lines.append(message)
            log_placeholder.markdown("\n".join(f"- {m}" for m in log_lines))
            if fraction is not None:
                status.update(label=f"Running Menu Refresh… ({int(fraction*100)}%)")

        try:
            result = wo.run_menu_refresh(
                uploaded, zcta=zcta or None, market_override=manual_market,
                template_path="report_template.html", progress_callback=progress,
            )
        except wo.WorkflowError as e:
            status.update(label="Stopped", state="error")
            st.error(str(e))
            if manual_market is None:
                st.info(
                    "If the automatic market-data fetch is the problem, open "
                    "'enter it manually' above and fill in at least Residents, "
                    "then run again."
                )
            return
        except Exception as e:
            status.update(label="Stopped", state="error")
            st.error(f"Unexpected error: {e}")
            return

        status.update(label="Menu Refresh complete", state="complete")

    # Persist so the result survives reruns (e.g. clicking a download button)
    # until the user explicitly starts a new refresh.
    st.session_state["last_result"] = result
    st.session_state["last_result_source"] = uploaded.name
    st.rerun()


def _render_workflow_result(result, source_name):
    counts = result.counts
    st.success(f"Menu Refresh complete for {source_name} — recognized as "
               f"{result.input_type.replace('_', ' ')}, market data from {result.market_source}.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Keep", counts.get("keep", 0))
    c2.metric("Refresh", counts.get("refresh", 0))
    c3.metric("Remove", counts.get("remove", 0))
    c4.metric("Total items", counts.get("total", 0))

    for w in result.warnings:
        st.warning(w)

    if result.replacement_briefs:
        with st.expander(f"🔁 {len(result.replacement_briefs)} replacement brief(s) for removed items", expanded=True):
            st.caption(
                "For every REMOVE decision: what gap it leaves and what a "
                "replacement item should target. The specific new dish "
                "(name/recipe/ingredients) still needs a human or assistant "
                "pass — this is the analytical brief, not a finished concept."
            )
            for b in result.replacement_briefs:
                st.markdown(f"**{b.removed_item}**")
                st.markdown(b.brief_text)
                st.markdown("---")

    if result.duplicate_candidates:
        with st.expander(f"⚠️ {len(result.duplicate_candidates)} near-duplicate candidates — review these", expanded=False):
            st.caption("Heuristic matches — check these before trusting them fully.")
            st.dataframe(pd.DataFrame([{'Item A': p.item_a, 'Item B': p.item_b, 'Similarity': p.score}
                                        for p in result.duplicate_candidates]), hide_index=True)

    st.markdown("#### Download your results")
    dl_cols = st.columns(3 if result.menu_intelligence_bytes else 2)
    with dl_cols[0]:
        st.download_button(
            "⬇ Menu Refresh workbook (.xlsx)",
            data=result.workbook_bytes.getvalue() if hasattr(result.workbook_bytes, 'getvalue') else result.workbook_bytes,
            file_name="Menu_Refresh_Analysis.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="mr_dl_workbook",
        )
    with dl_cols[1]:
        st.download_button(
            "⬇ Executive HTML report", data=result.report_html,
            file_name="Menu_Refresh_Report.html", mime="text/html", type="primary",
            key="mr_dl_report",
        )
    if result.menu_intelligence_bytes:
        with dl_cols[2]:
            st.download_button(
                "⬇ Menu Intelligence workbook (.xlsx)",
                data=result.menu_intelligence_bytes.getvalue(),
                file_name="Menu_Intelligence.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="mr_dl_intel",
            )

    with st.expander("Preview workbook", expanded=True):
        _preview_workbook(result.workbook_bytes, key_prefix="mr_wb")

    with st.expander("Preview report", expanded=True):
        st.components.v1.html(result.report_html, height=800, scrolling=True)


# ----------------------------------------------------------------------
# Menu Creation
# ----------------------------------------------------------------------
def render_menu_creation():
    st.title("📋 Menu Creation")

    if st.session_state.get("menu_creation_result") is not None:
        if st.button("🔄 Start a new Menu Creation", key="mc_start_new"):
            st.session_state["menu_creation_result"] = None
            st.session_state["menu_creation_zcta"] = None
            st.rerun()
        _render_menu_creation_result(
            st.session_state["menu_creation_result"],
            st.session_state.get("menu_creation_zcta", ""),
        )
        return
    
    st.markdown(
        "Upload a restaurant list, pick a ZCTA, and get net-new menu item "
        "recommendations built from a curated real-dish library and this "
        "ZIP's actual Census + commuter data -- no menu file needed, no LLM."
    )

    with st.expander("What happens automatically", expanded=False):
        st.markdown(
            "Only a restaurant list is needed. The app detects every ZCTA "
            "present and how many restaurants are in each. Once you pick "
            "one, it fetches Census economic data (income, age, household "
            "size, unemployment, income brackets) and LODES commuter flow "
            "for that ZCTA specifically.\n\n"
            "If you upload the optional comparable-data file, its price and "
            "quantity-sold columns are used to populate the comparable "
            "restaurant fields in the output workbook.\n\n"
            "The local restaurant category landscape (from this file's "
            "`cuisines` column) decides which categories new items map "
            "onto. Each item's ingredients and description come from a "
            "curated library of real, standard dishes for that category -- "
            "not invented per request, not sourced from an uploaded menu "
            "file. Premium and Premium Edge tiers get real upgrade "
            "ingredients added (e.g. applewood bacon, aged cheddar) so "
            "tiers are genuinely different dishes, not the same item "
            "re-priced. The final display name is generated from those real "
            "ingredients using an approved adjective vocabulary (Smokehouse, "
            "Nashville Hot, Fire-Grilled, etc.) -- never copied from any "
            "source text. Comparable restaurants, cost/profitability math, "
            "and market context are all computed the same deterministic way "
            "as before."
        )

    uploaded = st.file_uploader(
        "Restaurant list (.csv or .xlsx)", type=["csv", "xlsx"], key="mc_uploader",
    )
    comparable_uploaded = st.file_uploader(
        "Comparable data (.csv or .xlsx, optional)", type=["csv", "xlsx"], key="mc_comparable_uploader",
        help="One row per comparable restaurant, with restaurant name plus price and quantity-sold columns.",
    )

    if uploaded is None:
        st.info("Upload a restaurant list to begin.")
        return

    raw_df = pd.read_csv(uploaded) if uploaded.name.lower().endswith(".csv") else pd.read_excel(uploaded)
    comparable_raw_df = None
    if comparable_uploaded is not None:
        comparable_raw_df = (
            pd.read_csv(comparable_uploaded) if comparable_uploaded.name.lower().endswith(".csv")
            else pd.read_excel(comparable_uploaded)
        )

    try:
        norm = inorm.normalize_restaurant_dataframe(raw_df)
    except ValueError as e:
        st.error(str(e))
        return

    for w in norm.warnings:
        st.warning(w)
    with st.expander("Column mapping (verify once per new file format)"):
        st.json({canonical: original for canonical, original in norm.column_map.items()})

    zcta_counts = rmc.detect_zctas(norm.df)
    selected_zcta = st.selectbox(
        "Select a ZCTA to build a menu for",
        options=zcta_counts.index.tolist(),
        format_func=lambda z: f"{z}  ({zcta_counts[z]} restaurants in file)",
        key="mc_zcta_select",
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        state_abbr = st.text_input("State abbreviation", value="", max_chars=2, key="mc_state",
                                    help="Leave blank to auto-detect from the ZIP prefix.")
    with col2:
        city = st.text_input("City", value="", key="mc_city")
    with col3:
        county = st.text_input("County", value="", key="mc_county")

    col4, col5 = st.columns(2)
    with col4:
        area_sq_mi = st.number_input("ZCTA land area (sq mi)", min_value=0.1, value=10.0, step=0.1, key="mc_area")
    with col5:
        biz_residential_mix = st.selectbox(
            "Business/Residential mix", ["BALANCED", "RESIDENTIAL-heavy", "BUSINESS-heavy"], key="mc_mix",
        )

    has_anchor = st.checkbox("Tourist / office / school / hospital anchor present?", key="mc_anchor_flag")
    anchor_note = st.text_input("Anchor note", value="", key="mc_anchor_note") if has_anchor else ""

    n_items = st.number_input(
        "Number of new items to generate", min_value=1, max_value=50, value=7, step=1, key="mc_n_items",
    )

    if not st.button("🚀 Generate Menu", type="primary", use_container_width=True):
        return

    log_lines = []
    with st.status("Running Menu Creation…", expanded=True) as status:
        log_placeholder = st.empty()

        def progress(message, fraction=None):
            log_lines.append(message)
            log_placeholder.markdown("\n".join(f"- {m}" for m in log_lines))
            if fraction is not None:
                status.update(label=f"Running Menu Creation… ({int(fraction*100)}%)")

        try:
            result_df, issues = rmc.run(
                restaurant_df=norm.df, zcta=selected_zcta, city=city, county=county,
                area_sq_mi=area_sq_mi, biz_residential_mix=biz_residential_mix,
                has_anchor=has_anchor, anchor_note=anchor_note, n_items=n_items,
                state_abbr=state_abbr or None, progress_callback=progress,
                comparable_metrics_df=comparable_raw_df,
                already_normalized=True,
            )
        except rmc.MenuCreationError as e:
            status.update(label="Stopped", state="error")
            st.error(str(e))
            return
        except Exception as e:
            status.update(label="Stopped", state="error")
            st.error(f"Unexpected error: {e}")
            return

        status.update(label="Menu Creation complete", state="complete")

    # Persist issues alongside the result so they still show after a rerun.
    st.session_state["menu_creation_result"] = result_df
    st.session_state["menu_creation_zcta"] = selected_zcta
    st.session_state["menu_creation_issues"] = issues
    st.rerun()


def _render_menu_creation_result(result_df, zcta):
    for issue in st.session_state.get("menu_creation_issues", []):
        st.warning(issue)

    st.success(f"Generated {len(result_df)} items for ZCTA {zcta}.")

    st.markdown("#### Preview")
    st.dataframe(
        result_df[[
            "Recommended New Menu Item", "Recommended Category", "Recommended Price Band",
            "Recommended Menu Price ($)", "Profitability Value (%)", "Menu Item Confidence Score (1-5)",
        ]],
        use_container_width=True, hide_index=True,
    )

    st.markdown("#### Download")
    buf = io.BytesIO()
    result_df.to_excel(buf, index=False)
    st.download_button(
        "⬇ Menu Creation workbook (.xlsx)", data=buf.getvalue(),
        file_name=f"Menu_Creation_{zcta}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", key="mc_download",
    )

    with st.expander("Preview full workbook", expanded=False):
        st.dataframe(result_df, use_container_width=True, hide_index=True)


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
def render_settings():
    st.title("⚙️ Settings")

    st.subheader("Appearance")
    current = st.session_state.get("theme", "dark")
    choice = st.radio(
        "Theme",
        options=["dark", "light"],
        index=0 if current == "dark" else 1,
        format_func=lambda t: "🌙 Dark" if t == "dark" else "☀️ Light",
        horizontal=True,
        key="theme_radio",
    )
    if choice != current:
        st.session_state["theme"] = choice
        st.rerun()

    st.caption(
        "This applies a custom dark/light stylesheet on top of Streamlit "
        "and persists for this session. It's a lightweight override, not "
        "Streamlit's native theme engine (that one lives under the ⋮ menu "
        "→ Settings → Theme and requires a restart to change globally)."
    )


# ----------------------------------------------------------------------
# Route
# ----------------------------------------------------------------------
if page == "🏠 Overview":
    render_overview()
elif page == "🔄 Menu Refresh":
    render_menu_refresh()
elif page == "📋 Menu Creation":
    render_menu_creation()
elif page == "⚙️ Settings":
    render_settings()
