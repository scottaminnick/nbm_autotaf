import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import re
import os
import hmac
from datetime import datetime, timedelta, timezone

# Slide-1 narrative + Slide-2 coordination-need helpers live in dss_narrative.py
# (must be deployed alongside this file — commit it to your repo).
from dss_narrative import build_dss_narrative, build_icao_region_map, coordination_products

# --- PAGE SETUP ---
st.set_page_config(page_title="NBM Aviation Dashboard", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #1e3050; }
    h1, h2, h3, .stMarkdown p, label, .stCaption { color: #d6e4f0 !important; }
    .stDataFrame { background-color: #162840; }
    .stTextArea textarea { background-color: #162840; color: #d6e4f0; }
    .stCode { background-color: #162840; }
    div[data-testid="stSelectbox"] label,
    div[data-testid="stCheckbox"] label { color: #d6e4f0 !important; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# ACCESS GATE — shared password (tier 1)
# ==========================================
def check_password():
    """Gate the whole app behind one shared password.

    WHY each piece:
      * Expected password comes from the APP_PASSWORD environment variable
        (set it in Railway > Variables) — never hardcoded. A hardcoded secret
        pushed to GitHub lives in the repo history forever, even if deleted.
      * hmac.compare_digest() compares in constant time, so an attacker can't
        learn the password character-by-character from response timing.
      * On success we DELETE the typed password from session_state so the
        secret isn't retained in memory after the check.
      * Fails CLOSED: if APP_PASSWORD isn't set, nobody gets in (including you).
        Intentional — better locked than wide open.
      * Reads os.environ, NOT st.secrets: st.secrets needs a .streamlit/
        secrets.toml file that exists on Streamlit Cloud but not on Railway.
    """
    def password_entered():
        expected = os.environ.get("APP_PASSWORD", "")
        typed    = st.session_state.get("password", "")
        if expected and hmac.compare_digest(typed, expected):
            st.session_state["password_correct"] = True
            del st.session_state["password"]          # don't retain the secret
        else:
            st.session_state["password_correct"] = False

    # Already authenticated this session? Let them through.
    if st.session_state.get("password_correct", False):
        return True

    # Otherwise, show the prompt.
    st.caption("🔒 Internal access only")
    if not os.environ.get("APP_PASSWORD"):
        st.warning("App password isn't configured on the server yet.")
    st.text_input("Password", type="password",
                  on_change=password_entered, key="password")
    if st.session_state.get("password_correct") is False:
        st.error("😕 Incorrect password")
    return False

if not check_password():
    st.stop()   # halt here — nothing below renders until the password is right

st.title("✈️ NBM Terminal Weather Dashboard")
st.markdown("Pulling live NBS text guidance (~72 hours) directly from the NOAA NOMADS supercomputer.")

# ==========================================
# 1. PRESET LOCATIONS
# ==========================================
PRESETS = {
    "Custom (Type your own)": "",
    "WC Site - All Sites": "KBOS, KEWR, KPHL, KATL, KMIA, KMCI, KDFW, KIAH, KLAX, KSFO, KSEA",
    "WC Site - Atlanta": "KATL, KFTY, KPDK, KMGE, KRYY",
    "WC Site - Boston": "KBOS, KPVD, KMHT, KBED",
    "WC Site - Dallas": "KDFW, KDAL, KAFW, KFTW, KNFW, KGKY",
    "WC Site - Houston": "KIAH, KHOU, KSGR, KEFD, KLBX, KGLS, KLVJ, KDWH",
    "WC Site - Kansas City": "KMCI, KMKC, KIXD, KTOP, KFOE",
    "WC Site - Los Angeles": "KLAX, KBUR, KLGB, KSNA, KONT, KVNY, KSMO, KSLI, KSBD, KRIV",
    "WC Site - Miami": "KMIA, KFLL, KFXE, KPBI, KOPF, KHWO, KTMB, KHST",
    "WC Site - New York/NJ": "KEWR, KJFK, KLGA, KTEB, KHPN, KISP",
    "WC Site - Philadelphia": "KPHL, KPNE, KILG, KTTN",
    "WC Site - Santa Clara": "KSFO, KSJC, KOAK, KHAF, KLVK",
    "WC Site - Seattle": "KSEA, KBFI, KPAE, KPWT, KTCM, KGRF, KOLM",
}

# Build ICAO -> coordination-region lookup once from the presets above.
REGION_MAP = build_icao_region_map(PRESETS)

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def deg_to_cardinal(d):
    if not isinstance(d, int): return "VRB"
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(d / 22.5) % 16]

def get_flight_category(cig_ft, vis_sm, sky_pct=100):
    # A ceiling requires BKN (>50%) or OVC sky coverage.
    # SCT/FEW layers are NOT ceilings — flight category for those periods is vis-only.
    sky = int(sky_pct) if sky_pct else 100
    has_ceiling = sky > 50

    if has_ceiling:
        if cig_ft < 500  or vis_sm < 1.0: return "LIFR"
        if cig_ft < 1000 or vis_sm < 3.0: return "IFR"
        if cig_ft <= 3000 or vis_sm <= 5.0: return "MVFR"
    else:
        # No ceiling — evaluate on visibility alone
        if vis_sm < 1.0:  return "LIFR"
        if vis_sm < 3.0:  return "IFR"
        if vis_sm <= 5.0: return "MVFR"
    return "VFR"

def get_cloud_coverage(sky_pct, raw_cig, raw_lcb):
    sky = int(sky_pct) if sky_pct else 0

    # 888 is NBM's "unlimited / no ceiling" sentinel; negatives = missing
    if raw_cig and not raw_cig.startswith('-') and raw_cig != '888':
        cig = int(raw_cig) * 100
    else:
        cig = 99999

    lcb = int(raw_lcb) * 100 if raw_lcb and not raw_lcb.startswith('-') else 99999

    if sky <= 5:  return "CLR"
    elif sky <= 25: return f"FEW{int(lcb/100):03d}" if lcb < 99000 else "FEW"
    elif sky <= 50: return f"SCT{int(lcb/100):03d}" if lcb < 99000 else "SCT"
    elif sky <= 87: return f"BKN{int(cig/100):03d}" if cig < 99000 else "BKN250"
    else:           return f"OVC{int(cig/100):03d}" if cig < 99000 else "OVC250"

def get_weather(pra, psn, pzr, t03, p06="0"):
    # IMPORTANT: PRA/PSN/PZR are CONDITIONAL precipitation-type probabilities.
    # They express the probability of *type given that precip is occurring*, not
    # the absolute chance of precip. Without gating on PoP (P06), PRA often reads
    # 100 even on bone-dry hours, which would paint rain on every cell.
    # Gate on P06 >= 20% before trusting any type probability.
    pop       = int(p06) if p06 and p06.strip('-').isdigit() else 0
    rain_prob = int(pra) if pra else 0
    snow_prob = int(psn) if psn else 0
    ice_prob  = int(pzr) if pzr else 0
    ts_prob   = int(t03) if t03 else 0

    # Thunder probability (T03) is absolute — evaluate independently of PoP
    ts_prefix = "TS" if ts_prob >= 20 else ""

    # Only assign a precip type when PoP is meaningful AND at least one type prob fires
    if pop >= 20 and max(rain_prob, snow_prob, ice_prob) >= 20:
        if ice_prob >= rain_prob and ice_prob >= snow_prob:
            taf_wx = f"{ts_prefix}FZRA" if ts_prefix else "FZRA"
            return f"Freezing Rain ({taf_wx})", taf_wx
        elif snow_prob >= rain_prob:
            taf_wx = f"{ts_prefix}SN" if ts_prefix else "-SN"
            return f"Snow ({taf_wx})", taf_wx
        elif rain_prob >= 20:
            taf_wx = f"{ts_prefix}RA" if ts_prefix else "-RA"
            return f"Rain ({taf_wx})", taf_wx

    # Thunder can occur without significant PoP (VCTS scenario)
    if ts_prob >= 20:
        return "Thunderstorms (VCTS)", "VCTS"

    return "None", ""

def impact_label(level):
    # Aligned to the briefing's slide vocabulary (None / Minor / Moderate / Major).
    return {0:"None", 1:"Minor", 2:"Moderate", 3:"Major"}.get(level, "Unknown")

def impact_color(level):
    return {0:"#27ae60", 1:"#e5e500", 2:"#ff9900", 3:"#ff4c4c"}.get(level, "#cccccc")

def get_primary_driver(cig_ft, vis_sm, taf_wx, wsp, gst, sky_pct=100):
    """Returns the top aviation impact driver for a given forecast period."""
    sky = int(sky_pct) if sky_pct else 100
    has_ceiling = sky > 50

    drivers = []
    if "TS" in taf_wx or "VCTS" in taf_wx:  drivers.append(("Thunderstorms", 4))
    if any(t in taf_wx for t in ["FZRA","FZDZ"]): drivers.append(("Freezing precip", 4))
    if any(t in taf_wx for t in ["SN","PL"]):     drivers.append(("Wintry precip", 3))

    if has_ceiling:
        if   cig_ft < 1000: drivers.append(("IFR/LIFR ceilings", 4))
        elif cig_ft < 3000: drivers.append(("MVFR ceilings", 2))
        elif cig_ft < 5000: drivers.append(("Low VFR ceilings", 1))
    else:
        # SCT/FEW layer at a concerning height — flag as a watch, not a ceiling impact
        if cig_ft < 3000:   drivers.append(("Near-ceiling SCT layer", 1))

    if   vis_sm < 3:                 drivers.append(("IFR/LIFR visibility", 4))
    elif vis_sm < 5:                 drivers.append(("MVFR visibility", 2))

    if   gst >= 35 or wsp >= 30:    drivers.append(("Strong surface winds", 3))
    elif gst >= 25 or wsp >= 20:    drivers.append(("Gusty winds", 2))

    if not drivers: return "None"
    drivers.sort(key=lambda x: x[1], reverse=True)
    return drivers[0][0]

def get_impact_level(cig_ft, vis_sm, wx, wsp, gst, sky_pct=100):
    """Impact level 0–3. Ceiling-based levels require BKN/OVC (a true ceiling).
    SCT/FEW layers at concerning heights are capped at level 1 — yellow watch —
    because they're one step from becoming a ceiling but aren't one yet."""
    sky = int(sky_pct) if sky_pct else 100
    has_ceiling = sky > 50   # BKN or OVC

    thunder  = ("TS" in wx) or ("VCTS" in wx)
    freezing = any(t in wx for t in ["FZRA","FZDZ"])
    winter   = any(t in wx for t in ["SN","PL","RAPL","SNPL","SHSN","SG"])

    # Level 3 — weather/wind triggers are independent of ceiling type
    if thunder or freezing or winter:       return 3
    if has_ceiling and cig_ft < 1000:       return 3
    if vis_sm < 3:                          return 3
    if wsp >= 30 or gst >= 35:             return 3

    # Level 2
    if has_ceiling and 1000 <= cig_ft < 3000: return 2
    if 3 <= vis_sm < 5:                       return 2
    if 20 <= wsp < 30 or 25 <= gst < 35:     return 2

    # Level 1
    if has_ceiling and 3000 <= cig_ft < 5000: return 1
    # SCT/FEW layer at a height that would trigger MVFR or worse if BKN → watch
    if not has_ceiling and cig_ft < 3000:     return 1

    return 0

# ==========================================
# 2. DATA RETRIEVAL
# ==========================================
selected_preset = st.selectbox("Select a Preset Region:", list(PRESETS.keys()))
default_text    = "" if selected_preset == "Custom (Type your own)" else PRESETS[selected_preset]
user_input      = st.text_input("Enter ICAO Codes (comma-separated):", default_text)
icaos           = [c.strip().upper() for c in user_input.split(",") if c.strip()]

if st.button("Generate Dashboard"):
    if not icaos:
        st.warning("Please enter at least one valid ICAO code.")
    else:
        with st.spinner("Connecting to NOAA NOMADS..."):
            headers       = {"User-Agent": "NBM_Aviation_Dashboard (your@email.com)"}
            now           = datetime.now(timezone.utc)
            full_nbm_text = None
            init_dt       = None

            for i in range(24):
                check_time = now - timedelta(hours=i)
                date_str   = check_time.strftime("%Y%m%d")
                hour_str   = check_time.strftime("%H")
                url = (f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/"
                       f"blend.{date_str}/{hour_str}/text/blend_nbstx.t{hour_str}z")
                try:
                    if requests.head(url, headers=headers, timeout=5).status_code == 200:
                        full_nbm_text = requests.get(url, headers=headers, timeout=15).text
                        init_dt       = datetime.strptime(f"{date_str} {hour_str}00", "%Y%m%d %H%M")
                        break
                except requests.RequestException:
                    continue

            if not full_nbm_text:
                st.error("Could not locate recent NBM text data on the NOMADS server.")
                st.stop()

            all_data    = []
            valid_icaos = []

            for ICAO in icaos:
                station_regex = re.compile(
                    rf"(?m)^\s*{ICAO}\s+NBM\s+V[\s\S]*?(?=^\s*\S+\s+NBM\s+V|\Z)"
                )
                match = station_regex.search(full_nbm_text)
                if not match:
                    st.warning(f"⚠️ Could not find data for {ICAO}. Skipping...")
                    continue

                valid_icaos.append(ICAO)
                raw_nbm_text = match.group(0)

                # Fixed-width column parser for NBS text bulletin
                parsed_data = {}
                for line in raw_nbm_text.split('\n'):
                    if len(line.strip()) < 5: continue
                    key = line.split()[0]
                    if len(key) <= 4 and key.isalnum():
                        start  = line.find(key) + 4
                        values = [line[j:j+3].strip() for j in range(start, len(line), 3)]
                        while values and values[-1] == '': values.pop()
                        parsed_data[key] = values

                fhr_list = parsed_data.get('FHR', [])
                cig_list = parsed_data.get('CIG', [])
                lcb_list = parsed_data.get('LCB', [])
                sky_list = parsed_data.get('SKY', [])
                vis_list = parsed_data.get('VIS', [])
                wdr_list = parsed_data.get('WDR', [])
                wsp_list = parsed_data.get('WSP', [])
                gst_list = parsed_data.get('GST', [])
                pra_list = parsed_data.get('PRA', [])
                psn_list = parsed_data.get('PSN', [])
                pzr_list = parsed_data.get('PZR', [])
                t03_list = parsed_data.get('T03', [])
                # P06 gates the conditional type probs — must be parsed alongside them
                p06_list = parsed_data.get('P06', [])

                for i in range(len(fhr_list)):
                    fhr = fhr_list[i]

                    if init_dt:
                        valid_time = init_dt + timedelta(hours=int(fhr))
                        zulu_str   = valid_time.strftime("%d/%H00Z")
                        fm_str     = valid_time.strftime("%d%H00")
                    else:
                        valid_time, zulu_str, fm_str = None, f"+{fhr}h", fhr

                    # --- Ceiling ---
                    raw_cig   = cig_list[i] if i < len(cig_list) else ""
                    raw_lcb   = lcb_list[i] if i < len(lcb_list) else ""
                    sky_pct   = sky_list[i] if i < len(sky_list) else ""
                    cloud_str = get_cloud_coverage(sky_pct, raw_cig, raw_lcb)

                    # 888 = NBM unlimited sentinel; treat as no ceiling (99999 ft)
                    if raw_cig and not raw_cig.startswith('-') and raw_cig != '888':
                        cig_ft = int(raw_cig) * 100
                    else:
                        cig_ft = 99999

                    # --- Visibility ---
                    raw_vis = vis_list[i] if i < len(vis_list) else ""
                    vis_sm  = round(int(raw_vis) / 10.0, 2) if raw_vis else 10.0
                    taf_vis = ("P6SM" if vis_sm > 6
                               else f"{int(vis_sm) if float(vis_sm).is_integer() else vis_sm}SM")

                    # --- Precip: type probs gated on P06 PoP ---
                    pra = pra_list[i] if i < len(pra_list) else ""
                    psn = psn_list[i] if i < len(psn_list) else ""
                    pzr = pzr_list[i] if i < len(pzr_list) else ""
                    t03 = t03_list[i] if i < len(t03_list) else ""
                    p06 = p06_list[i] if i < len(p06_list) else "0"

                    wx_desc, taf_wx = get_weather(pra, psn, pzr, t03, p06)

                    # --- Wind ---
                    wdr = int(wdr_list[i]) * 10 if i < len(wdr_list) and wdr_list[i] else "VRB"
                    wsp = int(wsp_list[i])       if i < len(wsp_list) and wsp_list[i] else 0
                    gst = int(gst_list[i])       if i < len(gst_list) and gst_list[i] else 0

                    cardinal = deg_to_cardinal(wdr)
                    if wsp == 0:
                        taf_wind, cell_wind = "00000KT", "calm"
                    else:
                        wdr_str  = f"{wdr:03d}" if isinstance(wdr, int) else "VRB"
                        taf_wind = (f"{wdr_str}{wsp:02d}G{gst:02d}KT" if gst
                                    else f"{wdr_str}{wsp:02d}KT")
                        cell_wind = f"{cardinal} {wsp}G{gst}" if gst else f"{cardinal} {wsp}"

                    # --- Impact and flight category ---
                    flight_cat     = get_flight_category(cig_ft, vis_sm, sky_pct)
                    impact_lvl     = get_impact_level(cig_ft, vis_sm, taf_wx, wsp, gst, sky_pct)
                    primary_driver = get_primary_driver(cig_ft, vis_sm, taf_wx, wsp, gst, sky_pct)

                    cell_wx   = taf_wx if taf_wx else "--"
                    cell_text = f"<b>{flight_cat}</b><br>{cell_wx}<br>{cell_wind}"

                    all_data.append({
                        "Station":         ICAO,
                        "Zulu Time":       zulu_str,
                        "FM Time":         fm_str,
                        "Clouds":          cloud_str,
                        "Visibility":      f"{vis_sm} SM",
                        "Weather":         wx_desc,
                        "Wind":            f"{wdr}° @ {wsp} kts" + (f" G{gst}" if gst else ""),
                        "Impact Level":    impact_lvl,
                        "Flight Category": flight_cat,
                        "Cell Text":       cell_text,
                        "TAF_Wind":        taf_wind,
                        "TAF_Vis":         taf_vis,
                        "TAF_WX":          taf_wx,
                        "Primary Driver":  primary_driver,
                        "Valid Datetime":  valid_time,
                        "Forecast Hour":   int(fhr),
                    })

            if not all_data:
                st.error("No valid data found for the provided ICAOs.")
                st.stop()

            st.session_state['df']          = pd.DataFrame(all_data)
            st.session_state['valid_icaos'] = valid_icaos
            st.session_state['init_dt']     = init_dt

# ==========================================
# 3. CONTROLS & VISUALIZATION
# ==========================================
if 'df' in st.session_state:
    df          = st.session_state['df']
    valid_icaos = st.session_state['valid_icaos']
    init_dt     = st.session_state['init_dt']

    st.divider()
    st.subheader("World Cup Aviation Outlook Controls")

    col1, col2, col3 = st.columns(3)
    with col1:
        time_window = st.selectbox(
            "Outlook Window",
            ["Full 72-hr NBM", "Day 1: 0–24 hr", "Day 2 (12Z–12Z)", "Day 3 (12Z–12Z)"]
        )
    with col2:
        min_display_impact = st.selectbox(
            "Minimum Impact to Highlight", [0, 1, 2, 3],
            format_func=lambda x: impact_label(x)
        )
    with col3:
        show_only_impacts = st.checkbox("Show only impacted airports", value=False)

    window_start_hr, window_end_hr = None, None
    if time_window in ("Day 2 (12Z–12Z)", "Day 3 (12Z–12Z)"):
        default_start, default_end = (24, 48) if time_window.startswith("Day 2") else (48, 72)
        hr_col1, hr_col2, hr_col3 = st.columns([1, 1, 2])
        with hr_col1:
            window_start_hr = st.number_input(
                "Start (Forecast Hour)",
                min_value=0, max_value=72, value=default_start, step=1,
                key=f"{time_window}_start"
            )
        with hr_col2:
            window_end_hr = st.number_input(
                "End (Forecast Hour)",
                min_value=0, max_value=72, value=default_end, step=1,
                key=f"{time_window}_end"
            )
        with hr_col3:
            if init_dt:
                start_valid = (init_dt + timedelta(hours=int(window_start_hr))).strftime("%b %d, %H00Z")
                end_valid   = (init_dt + timedelta(hours=int(window_end_hr))).strftime("%b %d, %H00Z")
            else:
                start_valid = f"FHR {int(window_start_hr)}"
                end_valid   = f"FHR {int(window_end_hr)}"
            st.markdown(
                f"<div style='padding-top:28px; color:#d6e4f0;'>"
                f"<span style='font-size:0.8rem; opacity:0.7;'>Selected Window</span><br>"
                f"<span style='font-size:1rem; font-weight:600;'>{start_valid} → {end_valid}</span>"
                f"</div>",
                unsafe_allow_html=True
            )

    df_view = df.copy()
    if time_window == "Day 1: 0–24 hr":
        df_view = df_view[(df_view["Forecast Hour"] >= 0) & (df_view["Forecast Hour"] <= 24)]
    elif time_window in ("Day 2 (12Z–12Z)", "Day 3 (12Z–12Z)"):
        df_view = df_view[(df_view["Forecast Hour"] >= window_start_hr) & (df_view["Forecast Hour"] <= window_end_hr)]

    if show_only_impacts:
        impacted = (
            df_view.groupby("Station")["Impact Level"]
            .max().loc[lambda s: s >= min_display_impact].index.tolist()
        )
        df_view = df_view[df_view["Station"].isin(impacted)]
    elif min_display_impact > 0:
        df_view = df_view[df_view["Impact Level"] >= min_display_impact]

    if df_view.empty:
        st.info("No periods meet the selected impact/filter criteria.")
        st.stop()

    # --- Summary table ---
    st.subheader("Match-Site Aviation Impact Summary")
    summary_rows = []
    for station, g in df_view.groupby("Station", sort=False):
        max_impact     = int(g["Impact Level"].max())
        worst          = g[g["Impact Level"] == max_impact]
        primary_driver = worst["Primary Driver"].mode().iloc[0] if not worst.empty else "None"
        start_time     = worst["Zulu Time"].iloc[0]  if not worst.empty else "-"
        end_time       = worst["Zulu Time"].iloc[-1] if not worst.empty else "-"
        summary_rows.append({
            "Station":          station,
            "Max Impact":       impact_label(max_impact),
            "Primary Concern":  primary_driver,
            "Peak Window":      f"{start_time}–{end_time}",
            "Max Impact Level": max_impact,
        })
    summary_df = pd.DataFrame(summary_rows)
    # Deterministic hazard -> coordination products (Slide-2 'Coordination Need').
    summary_df["Coordination Need"] = summary_df["Primary Concern"].apply(coordination_products)
    st.dataframe(
        summary_df.drop(columns=["Max Impact Level"]),
        use_container_width=True, hide_index=True
    )

    # --- DSS Builder text (Slide-1 narrative draft) ---
    if window_start_hr is not None:
        time_window_label = f"{time_window} [FHR {int(window_start_hr)}–{int(window_end_hr)}]"
    else:
        time_window_label = time_window
    dss_text = build_dss_narrative(summary_df, REGION_MAP, selected_preset, time_window_label)
    st.subheader("DSS Builder Text Draft")
    st.text_area("Copy/edit this text for DSS Builder:", dss_text, height=320)

    # --- Impact matrix heatmap ---
    st.subheader("Terminal Impact Matrix (NBS Guidance)")

    valid_view_icaos = [i for i in valid_icaos if i in df_view["Station"].unique()]
    ordered_times    = df_view["Zulu Time"].drop_duplicates().tolist()

    impact_data = (
        df_view.pivot(index="Station", columns="Zulu Time", values="Impact Level")
        .reindex(valid_view_icaos).reindex(columns=ordered_times)
    )
    cell_text_data = (
        df_view.pivot(index="Station", columns="Zulu Time", values="Cell Text")
        .reindex(valid_view_icaos).reindex(columns=ordered_times)
    )
    hover_data = (
        df_view.pivot(index="Station", columns="Zulu Time", values="Flight Category")
        .reindex(valid_view_icaos).reindex(columns=ordered_times)
    )
    for _, row in df_view.iterrows():
        hover_data.loc[row["Station"], row["Zulu Time"]] = (
            f"<b>Valid: {row['Zulu Time']}</b><br>"
            f"<b>{row['Flight Category']}</b><br>"
            f"Clouds: {row['Clouds']}<br>"
            f"Vis: {row['Visibility']}<br>"
            f"WX: {row['Weather']}<br>"
            f"Wind: {row['Wind']}<br>"
            f"Primary Driver: {row['Primary Driver']}"
        )

    colorscale  = [[0.0,"#27ae60"],[0.33,"#e5e500"],[0.66,"#ff9900"],[1.0,"#ff4c4c"]]
    plot_height = max(350, len(valid_view_icaos) * 120 + 100)

    fig = go.Figure(data=go.Heatmap(
        z=impact_data.values, x=impact_data.columns, y=impact_data.index,
        text=cell_text_data.values, texttemplate="%{text}", textfont={"size": 11},
        hovertext=hover_data.values, hoverinfo="text",
        colorscale=colorscale, showscale=False, zmin=0, zmax=3, xgap=2, ygap=2
    ))
    # Legend labels aligned to the briefing vocabulary (None / Minor / Moderate / Major).
    for label, color in {"None":"#27ae60","Minor":"#e5e500","Moderate":"#ff9900","Major":"#ff4c4c"}.items():
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=15, color=color, symbol="square"), name=label
        ))
    fig.update_layout(
        xaxis=dict(side="top"),
        yaxis=dict(autorange="reversed"),
        plot_bgcolor="#162840", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#d6e4f0"), height=plot_height,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(
            title="<b>Impact Level</b>", yanchor="top", y=1,
            xanchor="left", x=1.02, bgcolor="rgba(255,255,255,0.8)",
            bordercolor="black", borderwidth=1,
        )
    )
    # Highlight Day 2 and Day 3 columns when the full 72-hr window is displayed.
    # Each box is a transparent-fill rect with a colored border so cell colors
    # still read through. Day 2 = blue, Day 3 = gold.
    if time_window == "Full 72-hr NBM":
        time_fhr_map = (
            df_view[["Zulu Time", "Forecast Hour"]]
            .drop_duplicates("Zulu Time")
            .set_index("Zulu Time")["Forecast Hour"]
            .to_dict()
        )
        for _label, fhr_min, fhr_max, color in [
            ("Day 2", 24, 48, "rgba(100,180,255,0.9)"),
            ("Day 3", 48, 72, "rgba(255,200,50,0.9)"),
        ]:
            indices = [
                i for i, t in enumerate(ordered_times)
                if fhr_min < time_fhr_map.get(t, 0) <= fhr_max
            ]
            if not indices:
                continue
            fig.add_shape(
                type="rect",
                xref="x", yref="paper",
                x0=min(indices) - 0.5,
                x1=max(indices) + 0.5,
                y0=0, y1=1,
                line=dict(color=color, width=2.5),
                fillcolor="rgba(0,0,0,0)",
                layer="above",
            )

    st.plotly_chart(fig, use_container_width=True)

    # --- Downloads ---
    st.subheader("Downloads")
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button(
            "Download Full Parsed Data CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="nbm_terminal_guidance_full.csv", mime="text/csv"
        )
    with col_dl2:
        st.download_button(
            "Download Summary CSV",
            data=summary_df.to_csv(index=False).encode("utf-8"),
            file_name="wc_aviation_impact_summary.csv", mime="text/csv"
        )

    # --- Experimental TAF (suppressed for All Sites) ---
    if selected_preset != "WC Site - All Sites":
        st.subheader("Experimental NBM Terminal Trend Guidance")
        st.caption("Not an official TAF. Use for situational awareness and forecaster review only.")

        issue_time     = init_dt.strftime("%d%H%M") + "Z" if init_dt else "UNKNOWN"
        taf_output_str = ""

        for ICAO in valid_icaos:
            station_df = df[df["Station"] == ICAO]
            if station_df.empty: continue
            taf_output_str += f"TAF {ICAO} {issue_time}\n"
            prev = ""
            for _, row in station_df.iterrows():
                cond = " ".join(
                    f"{row['TAF_Wind']} {row['TAF_Vis']} {row['TAF_WX']} {row['Clouds']}".split()
                )
                if cond != prev:
                    taf_output_str += f"  FM{row['FM Time']} {cond}\n"
                    prev = cond
            taf_output_str += "\n"

        st.code(taf_output_str, language="text")
