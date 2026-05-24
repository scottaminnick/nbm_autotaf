import requests
import pandas as pd
import plotly.graph_objects as go
import re
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. USER INPUT - ENTER YOUR AIRPORTS HERE
# ==========================================
# Simply add or remove ICAO codes from this list!
ICAOS = ["KBOS", "KBED", "KORH", "KPVD", "KFMH"]

headers = {"User-Agent": "NBM_Aviation_Dashboard (your@email.com)"}

# ==========================================
# 2. AUTOMATIC DATA RETRIEVAL VIA NOMADS
# ==========================================
print("Connecting to NOAA NOMADS to locate the latest NBM run...")

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
            print(f"Latest run found: {date_str} at {hour_str}00Z. Downloading master file...")
            data_response = requests.get(url, headers=headers, timeout=15)
            full_nbm_text = data_response.text
            
            # Save the initialization time for the TAF header
            init_dt = datetime.strptime(f"{date_str} {hour_str}00", "%Y%m%d %H%M")
            break
    except requests.RequestException:
        continue

if not full_nbm_text:
    raise ValueError("Could not locate recent NBM text data on the NOMADS server.")

print("Master file downloaded! Parsing individual terminals...\n")

# ==========================================
# 3. NBM PARSER & AVIATION LOGIC
# ==========================================
def get_flight_category(cig_ft, vis_sm):
    if cig_ft < 500 or vis_sm < 1.0: return 0, "LIFR"   
    elif cig_ft < 1000 or vis_sm < 3.0: return 1, "IFR"    
    elif cig_ft <= 3000 or vis_sm <= 5.0: return 2, "MVFR"   
    else: return 3, "VFR"    

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
    else:
        return "None", ""

all_data = []

# LOOP THROUGH EACH AIRPORT IN THE LIST
for ICAO in ICAOS:
    ICAO = ICAO.upper()
    station_regex = re.compile(rf"(?m)^\s*{ICAO}\s+NBM\s+V[\s\S]*?(?=^\s*\S+\s+NBM\s+V|\Z)")
    match = station_regex.search(full_nbm_text)
    
    if not match:
        print(f"⚠️ Warning: Could not find data for {ICAO}. Skipping...")
        continue
        
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
    
    for i in range(len(fhr_list)):
        fhr = fhr_list[i]
        
        if init_dt:
            valid_time = init_dt + timedelta(hours=int(fhr))
            zulu_str = valid_time.strftime("%d/%H00Z")
            fm_str = valid_time.strftime("%d%H00") 
        else:
            zulu_str = f"+{fhr}h"
            fm_str = fhr
        
        raw_cig = cig_list[i] if i < len(cig_list) else ""
        raw_lcb = lcb_list[i] if i < len(lcb_list) else ""
        sky_pct = sky_list[i] if i < len(sky_list) else ""
        cloud_str = get_cloud_coverage(sky_pct, raw_cig, raw_lcb)
        cig_ft = int(raw_cig) * 100 if raw_cig and not raw_cig.startswith('-') else 10000

        raw_vis = vis_list[i] if i < len(vis_list) else ""
        vis_sm = int(raw_vis) / 10.0 if raw_vis else 10.0
        taf_vis = "P6SM" if vis_sm > 6 else f"{int(vis_sm) if vis_sm.is_integer() else vis_sm}SM"
        
        pra = pra_list[i] if i < len(pra_list) else ""
        psn = psn_list[i] if i < len(psn_list) else ""
        pzr = pzr_list[i] if i < len(pzr_list) else ""
        t03 = t03_list[i] if i < len(t03_list) else ""
        
        wx_desc, taf_wx = get_weather(pra, psn, pzr, t03)
        
        wdr = f"{int(wdr_list[i])*10:03d}" if i < len(wdr_list) and wdr_list[i] else "VRB"
        wsp = int(wsp_list[i]) if i < len(wsp_list) and wsp_list[i] else 0
        gst = int(gst_list[i]) if i < len(gst_list) and gst_list[i] else 0
        wind_desc = f"{wdr}° @ {wsp} kts" + (f" G{gst}" if gst else "")
        
        if wsp == 0:
            taf_wind = "00000KT"
        else:
            taf_wind = f"{wdr}{wsp:02d}G{gst:02d}KT" if gst else f"{wdr}{wsp:02d}KT"

        cat_value, cat_name = get_flight_category(cig_ft, vis_sm)
        
        all_data.append({
            "Station": ICAO,
            "Zulu Time": zulu_str,
            "FM Time": fm_str,
            "Clouds": cloud_str,
            "Visibility": f"{vis_sm} SM",
            "Weather": wx_desc,
            "Wind": wind_desc,
            "Category Value": cat_value,
            "Flight Category": cat_name,
            "TAF_Wind": taf_wind,
            "TAF_Vis": taf_vis,
            "TAF_WX": taf_wx
        })

df = pd.DataFrame(all_data)

# ==========================================
# 4. PLOTLY VISUALIZATION
# ==========================================
# The pivot automatically builds rows for every airport in your dataset!
heatmap_data = df.pivot(index="Station", columns="Zulu Time", values="Category Value")
heatmap_data = heatmap_data[df["Zulu Time"].unique().tolist()] 

hover_text = df.pivot(index="Station", columns="Zulu Time", values="Flight Category")
hover_text = hover_text[df["Zulu Time"].unique().tolist()] 

for i, row in df.iterrows():
    hover_text.loc[row["Station"], row["Zulu Time"]] = (
        f"<b>Valid: {row['Zulu Time']}</b><br>"
        f"<b>{row['Flight Category']}</b><br>"
        f"Clouds: {row['Clouds']}<br>"
        f"Vis: {row['Visibility']}<br>"
        f"WX: {row['Weather']}<br>"
        f"Wind: {row['Wind']}"
    )

colorscale = [[0.0, "magenta"], [0.33, "red"], [0.66, "blue"], [1.0, "green"]]

# Set dynamic height based on the number of airports
plot_height = max(300, len(ICAOS) * 60 + 150)

fig = go.Figure(data=go.Heatmap(
    z=heatmap_data.values,
    x=heatmap_data.columns, 
    y=heatmap_data.index,
    text=hover_text.values,
    hoverinfo="text",
    colorscale=colorscale,
    showscale=False,
    zmin=0, zmax=3, 
    xgap=2, ygap=2  
))

legend_items = {"VFR": "green", "MVFR": "blue", "IFR": "red", "LIFR": "magenta"}

for label, color in legend_items.items():
    fig.add_trace(go.Scatter(
        x=[None], y=[None], mode="markers",
        marker=dict(size=15, color=color, symbol="square"), name=label
    ))

fig.update_layout(
    title="<b>NBM Terminal Dashboard (60+ Hour Outlook)</b>",
    xaxis_title="Valid Time (Zulu)", yaxis_title="Terminal",
    plot_bgcolor="white", height=plot_height, margin=dict(l=10, r=10, t=60, b=10),
    legend=dict(yanchor="top", y=1, xanchor="left", x=1.02, bgcolor="rgba(255,255,255,0.8)", bordercolor="black", borderwidth=1)
)

fig.show()

# ==========================================
# 5. AUTOMATED TAF GENERATOR (MULTI-SITE)
# ==========================================
issue_time = init_dt.strftime("%d%H%M") + "Z" if init_dt else "UNKNOWN"

for ICAO in ICAOS:
    # Filter the dataframe for just this specific airport
    station_df = df[df['Station'] == ICAO]
    if station_df.empty:
        continue
        
    print("\n" + "="*50)
    print(f" AUTOMATED NBM-DERIVED TAF FOR {ICAO}")
    print("="*50)
    print(f"TAF {ICAO} {issue_time}")

    prev_taf_line = ""

    for i, row in station_df.iterrows():
        conditions = f"{row['TAF_Wind']} {row['TAF_Vis']} {row['TAF_WX']} {row['Clouds']}".strip()
        conditions = " ".join(conditions.split())
        
        if conditions != prev_taf_line:
            print(f"  FM{row['FM Time']} {conditions}")
            prev_taf_line = conditions

    print("="*50)
