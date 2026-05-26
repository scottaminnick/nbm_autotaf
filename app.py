import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
import re
from datetime import datetime, timedelta, timezone

# --- PAGE SETUP ---
st.set_page_config(page_title="NBM Aviation Dashboard", layout="wide")
st.title("✈️ NBM Terminal Weather Dashboard")
st.markdown("Pulling live, unparsed 60-hour guidance directly from the NOAA NOMADS supercomputer.")

# ==========================================
# 1. PRESET LOCATIONS DICTIONARY
# ==========================================
# You can add as many presets as you want here! Just follow the "Name": "ICAO1, ICAO2" format.
PRESETS = {
    "Custom (Type your own)": "",
    "WC Site - All Sites": (
        "KATL, KFTY, KPDK, KMGE, KRYY, "
        "KBOS, KPVD, KMHT, KBED, "
        "KDFW, KDAL, KAFW, KFTW, KNFW, KGKY, "
        "KIAH, KHOU, KSGR, KEFD, KLBX, KGLS, KLVJ, KDWH, "
        "KMCI, KMKC, KIXD, KTOP, KFOE, "
        "KLAX, KBUR, KLGB, KSNA, KONT, KVNY, KSMO, KSLI, KSBD, KRIV, "
        "KMIA, KFLL, KFXE, KPBI, KOPF, KHWO, KTMB, KHST, "
        "KEWR, KJFK, KLGA, KTEB, KHPN, KISP, "
        "KPHL, KPNE, KILG, KTTN, "
        "KSFO, KSJC, KOAK, KHAF, KLVK, "
        "KSEA, KBFI, KPAE, KPWT, KTCM, KGRF, KOLM"
    ),
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
    # Simply add your other WC match sites below!
    # "WC Site - Miami": "KMIA, KFLL, KOPF, KTMB",
}

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def deg_to_cardinal(d):
    if not isinstance(d, int): return "VRB"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    ix = round(d / 22.5) % 16
    return dirs[ix]

def get_flight_category(cig_ft, vis_sm):
    if cig_ft < 500 or vis_sm < 1.0: return "LIFR"
    elif cig_ft < 1000 or vis_sm < 3.0: return "IFR"
    elif cig_ft <= 3000 or vis_sm <= 5.0: return "MVFR"
    else: return "VFR"

def get_impact_level(cig_ft, vis_sm, wx, wsp, gst, lls, trb):
    high_wx = ["FZRA", "FZDZ", "SQ", "TS", "VCTS", "TSRA", "SG", "SN", "-SN", "PL", "RAPL", "SNPL", "SHSN"]
    if (cig_ft < 1000 or vis_sm < 3 or wsp > 29 or gst > 34 or
            any(w in wx for w in high_wx) or lls > 40 or trb >= 4):
        return 3
    if (1000 <= cig_ft < 3000) or (3 <= vis_sm < 5) or (30 <= lls <= 40) or (2 <= trb <= 3):
        return 2
    if (3000 <= cig_ft < 5000) or (5 <= vis_sm <= 6) or (20 <= lls < 30) or (trb == 1):
        return 1
    return 0

def get_cloud_coverage(sky_pct, raw_cig, raw_lcb):
    sky = int(sky_pct) if sky_pct else 0
    cig = int(raw_cig) * 100 if raw_cig and not raw_cig.startswith('-') else 10000
    lcb = int(raw_lcb) * 100 if raw_lcb and not raw_lcb.startswith('-') else 10000

    if sky <= 5: return "CLR"
    elif sky <= 25: return f"FEW{int(lcb/100):03d}" if lcb != 10000 else "FEW"
    elif sky <= 50: return f"SCT{int(lcb/100):03d}" if lcb != 10000 else "SCT"
    elif sky <= 87: return f"BKN{int(cig/100):03d}" if cig != 10000 else "BKN250"
    else: return f"OVC{int(cig/100):03d}" if cig != 10000 else "OVC250"

def get_weather(pra, psn, pzr, t03):
    rain_prob = int(pra) if pra else 0
    snow_prob = int(psn) if psn else 0
    ice_prob = int(pzr) if pzr else 0
    ts_prob = int(t03) if t03 else 0

    if max(rain_prob, snow_prob, ice_prob, ts_prob) < 20: return "None", ""
    ts_prefix = "TS" if ts_prob >= 20 else ""

    if ice_prob >= rain_prob and ice_prob >= snow_prob:
        taf_wx = f"{ts_prefix}FZRA" if ts_prefix else "FZRA"
        return f"Freezing Rain ({taf_wx})", taf_wx
    elif snow_prob >= rain_prob:
        taf_wx = f"{ts_prefix}SN" if ts_prefix else "-SN"
        return f"Snow ({taf_wx})", taf_wx
    elif rain_prob >= 20:
        taf_wx = f"{ts_prefix}RA" if ts_prefix else "-RA"
        return f"Rain ({taf_wx})", taf_wx
    elif ts_prob >= 20:
        return "Thunderstorms (VCTS)", "VCTS"
    else: return "None", ""

def impact_label(level):
    labels = {
        0: "Little/No Impact",
        1: "Low Impact",
        2: "Medium Impact",
        3: "High Impact"
    }
    return labels.get(level, "Unknown")

def impact_color(level):
    colors = {
        0: "#2b2b2b",
        1: "#e5e500",
        2: "#ff9900",
        3: "#ff4c4c"
    }
    return colors.get(level, "#cccccc")

def get_primary_driver(cig_ft, vis_sm, taf_wx, wsp, gst, lls, trb):
    """
    Returns the main reason a time period is impactful.
    Priority is intentionally ops-driven, not purely meteorological.
    """
    drivers = []

    if "TS" in taf_wx or "VCTS" in taf_wx:
        drivers.append(("Thunderstorms", 4))
    if taf_wx in ["FZRA", "FZDZ", "TSFZRA"]:
        drivers.append(("Freezing precip", 4))
    if "SN" in taf_wx or "PL" in taf_wx:
        drivers.append(("Wintry precip", 3))
    if cig_ft < 1000:
        drivers.append(("IFR/LIFR ceilings", 4))
    elif cig_ft < 3000:
        drivers.append(("MVFR ceilings", 2))
    elif cig_ft < 5000:
        drivers.append(("Low VFR ceilings", 1))

    if vis_sm < 3:
        drivers.append(("IFR/LIFR visibility", 4))
    elif vis_sm < 5:
        drivers.append(("MVFR visibility", 2))

    if gst >= 35 or wsp >= 30:
        drivers.append(("Strong surface winds", 3))
    elif gst >= 25 or wsp >= 20:
        drivers.append(("Gusty winds", 2))

    if lls > 40:
        drivers.append(("Significant LLWS", 4))
    elif lls >= 30:
        drivers.append(("LLWS", 3))
    elif lls >= 20:
        drivers.append(("Marginal LLWS", 2))

    if trb >= 4:
        drivers.append(("Severe turbulence", 4))
    elif trb >= 2:
        drivers.append(("Moderate turbulence", 3))
    elif trb == 1:
        drivers.append(("Light turbulence", 1))

    if not drivers:
        return "None"

    drivers.sort(key=lambda x: x[1], reverse=True)
    return drivers[0][0]

def get_impact_level_v2(cig_ft, vis_sm, wx, wsp, gst, lls, trb):
    """
    Cleaner impact logic for WC outlook use.
    Avoids treating normal VFR visibility as an impact.
    """
    thunder = ("TS" in wx) or ("VCTS" in wx)
    freezing = any(token in wx for token in ["FZRA", "FZDZ"])
    winter = any(token in wx for token in ["SN", "PL", "RAPL", "SNPL", "SHSN", "SG"])

    if (
        cig_ft < 1000
        or vis_sm < 3
        or wsp >= 30
        or gst >= 35
        or freezing
        or winter
        or thunder
        or lls > 40
        or trb >= 4
    ):
        return 3

    if (
        1000 <= cig_ft < 3000
        or 3 <= vis_sm < 5
        or 20 <= wsp < 30
        or 25 <= gst < 35
        or 30 <= lls <= 40
        or 2 <= trb <= 3
    ):
        return 2

    if (
        3000 <= cig_ft < 5000
        or 20 <= lls < 30
        or trb == 1
    ):
        return 1

    return 0

def build_dss_summary(summary_df, selected_preset, time_window):
    if summary_df.empty:
        return "No significant aviation weather impacts are indicated in the selected NBM guidance window."

    high = summary_df[summary_df["Max Impact Level"] == 3]
    med = summary_df[summary_df["Max Impact Level"] == 2]
    low = summary_df[summary_df["Max Impact Level"] == 1]

    lines = []
    site_name = selected_preset.replace("WC Site - ", "")

    lines.append(f"{site_name} Aviation Outlook – {time_window}")
    lines.append("")
    lines.append("Key Messages:")

    if not high.empty:
        stations = ", ".join(high["Station"].tolist())
        hazards = ", ".join(high["Primary Concern"].unique().tolist())
        lines.append(f"• High aviation impacts are possible at {stations}, mainly due to {hazards}.")

    if not med.empty:
        stations = ", ".join(med["Station"].tolist())
        hazards = ", ".join(med["Primary Concern"].unique().tolist())
        lines.append(f"• Medium aviation impacts are possible at {stations}, mainly due to {hazards}.")

    if not low.empty and high.empty:
        stations = ", ".join(low["Station"].tolist())
        lines.append(f"• Low-end aviation impacts are possible at {stations}; monitor later guidance for trends.")

    if high.empty and med.empty and low.empty:
        lines.append("• Little to no aviation weather impacts are indicated in the selected period.")

    lines.append("")
    lines.append("Operational Notes:")
    lines.append("• This guidance is based on NBM terminal data and should be reviewed by a forecaster before partner dissemination.")
    lines.append("• Use CWSU/WFO coordination for final operational messaging, especially for convection and traffic-flow impacts.")

    return "\n".join(lines)

# ==========================================
# 2. AUTOMATIC DATA RETRIEVAL VIA NOMADS
# ==========================================
selected_preset = st.selectbox("Select a Preset Region:", list(PRESETS.keys()))

if selected_preset == "Custom (Type your own)":
    default_text = "KDEN, KORD, KJFK"
else:
    default_text = PRESETS[selected_preset]

user_input = st.text_input("Enter ICAO Codes (comma-separated):", default_text)
# This is our master order list!
icaos = [code.strip().upper() for code in user_input.split(",") if code.strip()]

if st.button("Generate Dashboard"):
    if not icaos:
        st.warning("Please enter at least one valid ICAO code.")
    else:
        with st.spinner("Connecting to NOAA NOMADS..."):
            headers = {"User-Agent": "NBM_Aviation_Dashboard (your@email.com)"}
            now = datetime.now(timezone.utc)
            full_nbm_text = None
            init_dt = None

            for i in range(24):
                check_time = now - timedelta(hours=i)
                date_str = check_time.strftime("%Y%m%d")
                hour_str = check_time.strftime("%H")

                url = f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/blend/prod/blend.{date_str}/{hour_str}/text/blend_nbstx.t{hour_str}z"

                try:
                    response = requests.head(url, headers=headers, timeout=5)
                    if response.status_code == 200:
                        data_response = requests.get(url, headers=headers, timeout=15)
                        full_nbm_text = data_response.text
                        init_dt = datetime.strptime(f"{date_str} {hour_str}00", "%Y%m%d %H%M")
                        break
                except requests.RequestException:
                    continue

            if not full_nbm_text:
                st.error("Could not locate recent NBM text data on the NOMADS server.")
                st.stop()

            all_data = []

            # We use the raw list so data is appended in the exact order requested
            # Keep track of valid ICAOs so if one is missing from NOMADS, it doesn't break our formatting
            valid_icaos = []

            for ICAO in icaos:
                station_regex = re.compile(rf"(?m)^\s*{ICAO}\s+NBM\s+V[\s\S]*?(?=^\s*\S+\s+NBM\s+V|\Z)")
                match = station_regex.search(full_nbm_text)

                if not match:
                    st.warning(f"⚠️ Could not find data for {ICAO}. Skipping...")
                    continue

                valid_icaos.append(ICAO)
                raw_nbm_text = match.group(0)

                parsed_data = {}
                for line in raw_nbm_text.split('\n'):
                    if len(line.strip()) < 5: continue
                    key = line.split()[0]
                    if len(key) <= 4 and key.isalnum():
                        key_idx = line.find(key)
                        start_idx = key_idx + 4
                        values = [line[i:i+3].strip() for i in range(start_idx, len(line), 3)]
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

                lls_list = parsed_data.get('LLS', [])
                lld_list = parsed_data.get('LLD', [])
                llh_list = parsed_data.get('LLH', [])
                trb_list = parsed_data.get('LLT', parsed_data.get('TRB', []))

                for i in range(len(fhr_list)):
                    fhr = fhr_list[i]

                    if init_dt:
                        valid_time = init_dt + timedelta(hours=int(fhr))
                        zulu_str = valid_time.strftime("%d/%H00Z")
                        fm_str = valid_time.strftime("%d%H00")
                    else:
                        valid_time = None
                        zulu_str = f"+{fhr}h"
                        fm_str = fhr

                    raw_cig = cig_list[i] if i < len(cig_list) else ""
                    raw_lcb = lcb_list[i] if i < len(lcb_list) else ""
                    sky_pct = sky_list[i] if i < len(sky_list) else ""
                    cloud_str = get_cloud_coverage(sky_pct, raw_cig, raw_lcb)
                    cig_ft = int(raw_cig) * 100 if raw_cig and not raw_cig.startswith('-') else 10000

                    raw_vis = vis_list[i] if i < len(vis_list) else ""
                    vis_sm = round(int(raw_vis) / 10.0, 2) if raw_vis else 10.0
                    taf_vis = "P6SM" if vis_sm > 6 else f"{int(vis_sm) if float(vis_sm).is_integer() else vis_sm}SM"

                    pra = pra_list[i] if i < len(pra_list) else ""
                    psn = psn_list[i] if i < len(psn_list) else ""
                    pzr = pzr_list[i] if i < len(pzr_list) else ""
                    t03 = t03_list[i] if i < len(t03_list) else ""

                    wx_desc, taf_wx = get_weather(pra, psn, pzr, t03)

                    wdr = int(wdr_list[i])*10 if i < len(wdr_list) and wdr_list[i] else "VRB"
                    wsp = int(wsp_list[i]) if i < len(wsp_list) and wsp_list[i] else 0
                    gst = int(gst_list[i]) if i < len(gst_list) and gst_list[i] else 0

                    lls_raw = lls_list[i] if i < len(lls_list) else ""
                    lls = int(lls_raw) if lls_raw and lls_raw.strip('-').isdigit() else 0

                    lld_raw = lld_list[i] if i < len(lld_list) else ""
                    lld = f"{int(lld_raw)*10:03d}" if lld_raw and lld_raw.strip('-').isdigit() else "VRB"

                    llh_raw = llh_list[i] if i < len(llh_list) else ""
                    llh = int(llh_raw) * 100 if llh_raw and llh_raw.strip('-').isdigit() else 0

                    trb_raw = trb_list[i] if i < len(trb_list) else ""
                    trb = int(trb_raw) if trb_raw and trb_raw.strip('-').isdigit() else 0

                    cardinal = deg_to_cardinal(wdr)
                    if wsp == 0:
                        taf_wind = "00000KT"
                        cell_wind = "calm"
                    else:
                        wdr_str = f"{wdr:03d}" if isinstance(wdr, int) else "VRB"
                        taf_wind = f"{wdr_str}{wsp:02d}G{gst:02d}KT" if gst else f"{wdr_str}{wsp:02d}KT"
                        cell_wind = f"{cardinal} {wsp}G{gst}" if gst else f"{cardinal} {wsp}"

                    flight_cat = get_flight_category(cig_ft, vis_sm)
                    impact_lvl = get_impact_level_v2(cig_ft, vis_sm, taf_wx, wsp, gst, lls, trb)
                    primary_driver = get_primary_driver(cig_ft, vis_sm, taf_wx, wsp, gst, lls, trb)

                    llws_text = f"<br>LLWS: {lld} @ {lls}KT ({llh}ft)" if lls >= 20 else ""
                    turb_text = ""
                    if trb == 4: turb_text = "<br>Turb: SVR"
                    elif trb >= 2: turb_text = "<br>Turb: MOD"
                    elif trb == 1: turb_text = "<br>Turb: LGT"

                    cell_wx = taf_wx if taf_wx else "--"
                    cell_text = f"<b>{flight_cat}</b><br>{cell_wx}<br>{cell_wind}{llws_text}{turb_text}"

                    all_data.append({
                        "Station": ICAO,
                        "Zulu Time": zulu_str,
                        "FM Time": fm_str,
                        "Clouds": cloud_str,
                        "Visibility": f"{vis_sm} SM",
                        "Weather": wx_desc,
                        "Wind": f"{wdr}° @ {wsp} kts" + (f" G{gst}" if gst else ""),
                        "LLWS": f"{lld} @ {lls}KT ({llh}ft)" if lls >= 20 else "None",
                        "Turbulence": "Severe" if trb == 4 else ("Moderate" if trb >= 2 else ("Light" if trb == 1 else "None")),
                        "Impact Level": impact_lvl,
                        "Flight Category": flight_cat,
                        "Cell Text": cell_text,
                        "TAF_Wind": taf_wind,
                        "TAF_Vis": taf_vis,
                        "TAF_WX": taf_wx,
                        "Primary Driver": primary_driver,
                        "Valid Datetime": valid_time,
                        "Forecast Hour": int(fhr),
                    })

            if not all_data:
                st.error("No valid data found for the provided ICAOs.")
                st.stop()

            st.session_state['df'] = pd.DataFrame(all_data)
            st.session_state['valid_icaos'] = valid_icaos
            st.session_state['init_dt'] = init_dt

# ==========================================
# 3. CONTROLS & VISUALIZATION
# Runs on every script re-run once data has been loaded,
# so changing any control immediately updates the display.
# ==========================================
if 'df' in st.session_state:
    df = st.session_state['df']
    valid_icaos = st.session_state['valid_icaos']
    init_dt = st.session_state['init_dt']

    st.divider()

    st.subheader("World Cup Aviation Outlook Controls")

    col1, col2, col3 = st.columns(3)

    with col1:
        time_window = st.selectbox(
            "Outlook Window",
            ["Full 60-hr NBM", "Day 1: 0–24 hr", "Day 2: 24–48 hr", "Day 3: 48–60 hr"]
        )

    with col2:
        min_display_impact = st.selectbox(
            "Minimum Impact to Highlight",
            [0, 1, 2, 3],
            format_func=lambda x: impact_label(x)
        )

    with col3:
        show_only_impacts = st.checkbox("Show only impacted airports", value=False)

    df_view = df.copy()

    if time_window == "Day 1: 0–24 hr":
        df_view = df_view[(df_view["Forecast Hour"] >= 0) & (df_view["Forecast Hour"] <= 24)]
    elif time_window == "Day 2: 24–48 hr":
        df_view = df_view[(df_view["Forecast Hour"] > 24) & (df_view["Forecast Hour"] <= 48)]
    elif time_window == "Day 3: 48–60 hr":
        df_view = df_view[(df_view["Forecast Hour"] > 48) & (df_view["Forecast Hour"] <= 60)]

    if show_only_impacts:
        impacted_stations = (
            df_view.groupby("Station")["Impact Level"]
            .max()
            .loc[lambda s: s >= min_display_impact]
            .index
            .tolist()
        )
        df_view = df_view[df_view["Station"].isin(impacted_stations)]
    else:
        df_view = df_view[df_view["Impact Level"] >= min_display_impact] if min_display_impact > 0 else df_view

    if df_view.empty:
        st.info("No periods meet the selected impact/filter criteria.")
        st.stop()

    st.subheader("Match-Site Aviation Impact Summary")

    summary_rows = []

    for station, g in df_view.groupby("Station", sort=False):
        max_impact = int(g["Impact Level"].max())
        worst = g[g["Impact Level"] == max_impact]

        drivers = (
            worst["Primary Driver"]
            .value_counts()
            .reset_index()
            .rename(columns={"index": "Driver", "Primary Driver": "Count"})
        )

        primary_driver = worst["Primary Driver"].mode().iloc[0] if not worst.empty else "None"
        start_time = worst["Zulu Time"].iloc[0] if not worst.empty else "-"
        end_time = worst["Zulu Time"].iloc[-1] if not worst.empty else "-"

        summary_rows.append({
            "Station": station,
            "Max Impact": impact_label(max_impact),
            "Primary Concern": primary_driver,
            "Peak Window": f"{start_time}–{end_time}",
            "Max Impact Level": max_impact
        })

    summary_df = pd.DataFrame(summary_rows)

    st.dataframe(
        summary_df.drop(columns=["Max Impact Level"]),
        use_container_width=True,
        hide_index=True
    )

    dss_text = build_dss_summary(summary_df, selected_preset, time_window)

    st.subheader("DSS Builder Text Draft")
    st.text_area("Copy/edit this text for DSS Builder:", dss_text, height=220)

    # --- PLOTLY VISUALIZATION ---
    st.subheader("Terminal Impact Matrix (Experimental NBM Guidance)")

    valid_view_icaos = [icao for icao in valid_icaos if icao in df_view["Station"].unique()]
    ordered_times = df_view["Zulu Time"].drop_duplicates().tolist()

    impact_data = (
        df_view.pivot(index="Station", columns="Zulu Time", values="Impact Level")
        .reindex(valid_view_icaos)
        .reindex(columns=ordered_times)
    )

    cell_text_data = (
        df_view.pivot(index="Station", columns="Zulu Time", values="Cell Text")
        .reindex(valid_view_icaos)
        .reindex(columns=ordered_times)
    )

    hover_data = (
        df_view.pivot(index="Station", columns="Zulu Time", values="Flight Category")
        .reindex(valid_view_icaos)
        .reindex(columns=ordered_times)
    )

    for _, row in df_view.iterrows():
        hover_data.loc[row["Station"], row["Zulu Time"]] = (
            f"<b>Valid: {row['Zulu Time']}</b><br>"
            f"<b>{row['Flight Category']}</b><br>"
            f"Clouds: {row['Clouds']}<br>"
            f"Vis: {row['Visibility']}<br>"
            f"WX: {row['Weather']}<br>"
            f"Wind: {row['Wind']}<br>"
            f"LLWS: {row['LLWS']}<br>"
            f"Turb: {row['Turbulence']}<br>"
            f"Primary Driver: {row['Primary Driver']}"
        )

    colorscale = [
        [0.0, "#2b2b2b"],
        [0.33, "#e5e500"],
        [0.66, "#ff9900"],
        [1.0, "#ff4c4c"],
    ]

    plot_height = max(350, len(valid_view_icaos) * 120 + 100)

    fig = go.Figure(data=go.Heatmap(
        z=impact_data.values,
        x=impact_data.columns,
        y=impact_data.index,
        text=cell_text_data.values,
        texttemplate="%{text}",
        textfont={"size": 11},
        hovertext=hover_data.values,
        hoverinfo="text",
        colorscale=colorscale,
        showscale=False,
        zmin=0,
        zmax=3,
        xgap=2,
        ygap=2
    ))

    legend_items = {
        "None": "#2b2b2b",
        "Low": "#e5e500",
        "Medium": "#ff9900",
        "High": "#ff4c4c",
    }

    for label, color in legend_items.items():
        fig.add_trace(go.Scatter(
            x=[None],
            y=[None],
            mode="markers",
            marker=dict(size=15, color=color, symbol="square"),
            name=label
        ))

    fig.update_layout(
        yaxis=dict(autorange="reversed"),
        plot_bgcolor="white",
        height=plot_height,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(
            title="<b>Impact Level</b>",
            yanchor="top",
            y=1,
            xanchor="left",
            x=1.02,
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="black",
            borderwidth=1,
        )
    )

    st.plotly_chart(fig, use_container_width=True)

    # --- DOWNLOADS ---
    st.subheader("Downloads")

    col_dl1, col_dl2 = st.columns(2)

    with col_dl1:
        st.download_button(
            label="Download Full Parsed Data CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="nbm_terminal_guidance_full.csv",
            mime="text/csv"
        )

    with col_dl2:
        st.download_button(
            label="Download Summary CSV",
            data=summary_df.to_csv(index=False).encode("utf-8"),
            file_name="wc_aviation_impact_summary.csv",
            mime="text/csv"
        )

    # --- AUTOMATED TAF GENERATOR (not shown for All Sites) ---
    if selected_preset != "WC Site - All Sites":
        st.subheader("Experimental NBM Terminal Trend Guidance")
        st.caption("Not an official TAF. Use for situational awareness and forecaster review only.")

        issue_time = init_dt.strftime("%d%H%M") + "Z" if init_dt else "UNKNOWN"
        taf_output_str = ""

        for ICAO in valid_icaos:
            station_df = df[df["Station"] == ICAO]
            if station_df.empty:
                continue

            taf_output_str += f"TAF {ICAO} {issue_time}\n"
            prev_taf_line = ""

            for _, row in station_df.iterrows():
                conditions = f"{row['TAF_Wind']} {row['TAF_Vis']} {row['TAF_WX']} {row['Clouds']}".strip()
                conditions = " ".join(conditions.split())

                if conditions != prev_taf_line:
                    taf_output_str += f"  FM{row['FM Time']} {conditions}\n"
                    prev_taf_line = conditions

            taf_output_str += "\n"

        st.code(taf_output_str, language="text")
