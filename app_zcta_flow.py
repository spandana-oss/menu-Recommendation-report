"""
app_zcta_flow.py — Streamlit page: type any ZCTA, get the real
inflow/outflow/stay-local commuter chart, computed live from LODES via
market_data.fetch_commuter_flows(). No sample/hardcoded data.

Run with: streamlit run app_zcta_flow.py
Requires market_data.py in the same directory (or on the Python path).
"""

import streamlit as st
import streamlit.components.v1 as components

from market_data import fetch_commuter_flows, MarketDataError, zcta_to_state

st.set_page_config(page_title="ZCTA commuter flow", layout="centered")
st.title("ZCTA commuter flow")

zcta_input = st.text_input("ZCTA", value="", max_chars=5, placeholder="e.g. 38103, 90210, 38637")
state_override = st.text_input(
    "State override (optional — only needed if the ZCTA is on a state border and gets misdetected)",
    value=""
)

FLOW_SVG_TEMPLATE = """
<div style="max-width:680px">
<svg width="100%" viewBox="0 0 680 300" role="img">
<title>Commuter inflow and outflow for ZCTA {zcta}</title>
<desc>A dark green arrow flowing into a circle representing the selected ZCTA and a light green arrow flowing out of it, with a center pin marking the ZCTA location</desc>

<circle cx="340" cy="150" r="78" fill="#97C459" fill-opacity="0.28" stroke="#639922" stroke-width="2"/>

<path d="M30,120 L250,120 L285,150 L250,180 L30,180 Z" fill="#27500A"/>
<text x="145" y="150" text-anchor="middle" dominant-baseline="central"
      font-size="24" font-weight="500" fill="#ffffff">{inflow}</text>

<path d="M395,120 L615,120 L650,150 L615,180 L395,180 Z" fill="#97C459"/>
<text x="530" y="150" text-anchor="middle" dominant-baseline="central"
      font-size="24" font-weight="500" fill="#173404">{outflow}</text>

<circle cx="340" cy="150" r="7" fill="#D14B2C" stroke="#ffffff" stroke-width="1.5"/>
<text x="340" y="177" text-anchor="middle" font-size="13" font-weight="500" fill="#0b0b0b">{zcta}</text>

<text x="145" y="215" text-anchor="middle" font-size="12" fill="#52514e">Workers commute in</text>
<text x="530" y="215" text-anchor="middle" font-size="12" fill="#52514e">Residents commute out</text>
<text x="340" y="252" text-anchor="middle" font-size="13" font-weight="500" fill="#0b0b0b">{interior} live &amp; work here</text>
</svg>
</div>
"""


def render_flow_chart(zcta: str, inflow: int, outflow: int, interior: int):
    html = FLOW_SVG_TEMPLATE.format(
        zcta=zcta,
        inflow=f"{inflow:,}",
        outflow=f"{outflow:,}",
        interior=f"{interior:,}",
    )
    components.html(html, height=280)


if zcta_input:
    if not (zcta_input.isdigit() and len(zcta_input) == 5):
        st.warning("Enter a 5-digit ZCTA to fetch its commuter flow data.")
        st.stop()

    detected_state = state_override.strip().lower() or zcta_to_state(zcta_input)
    if detected_state:
        st.caption(f"Detected state: {detected_state.upper()}")

    progress_bar = st.progress(0.0)
    status_text = st.empty()

    def progress_callback(message, pct):
        status_text.text(message)
        progress_bar.progress(min(max(pct, 0.0), 1.0))

    try:
        with st.spinner("Fetching commuter flow data..."):
            flows = fetch_commuter_flows(
                zcta_input,
                state_abbr=state_override.strip().lower() or None,
                progress_callback=progress_callback,
            )
        progress_bar.empty()
        status_text.empty()

        render_flow_chart(
            zcta_input,
            int(flows["worker_inflow"]),
            int(flows["resident_outflow"]),
            int(flows["stay_local"]),
        )

        st.caption(f"Source: {flows['source']}")

        with st.expander("Income and age breakdown of inflow workforce"):
            st.metric("High income (>$3,333/mo)", f"{flows['pct_income_high']:.1f}%")
            st.metric("Low income (<=$1,250/mo)", f"{flows['pct_income_low']:.1f}%")
            st.metric("Age 30-54", f"{flows['pct_age_mid']:.1f}%")
            st.metric("Age 55+", f"{flows['pct_age_senior']:.1f}%")

    except MarketDataError as e:
        progress_bar.empty()
        status_text.empty()
        st.error(str(e))
        