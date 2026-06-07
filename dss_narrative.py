"""
dss_narrative.py
----------------
Generates the Slide-1 narrative fields for the NBM Aviation Dashboard
(Top Aviation Concerns, Coordination Focus, suggested National Aviation
Concern) plus the Slide-2 'Coordination Need' product lookup.

DESIGN IN ONE BREATH:
  * Everything keys off ONE regional aggregation of your per-site summary_df.
  * Two formatters read that aggregation: Top Concerns -> bullets,
    Coordination Focus -> region:hazard lines. National Concern is a rollup.
  * Confidence and Watch List are deliberately LEFT BLANK for the forecaster:
    both need trend/ensemble judgment the NBS snapshot can't honestly supply.
  * The script proposes; the forecaster disposes. Every computed field is a
    starting draft, not an authority — hence the [bracketed] edit prompts.
"""

# ===========================================================================
# 1. STATIC MAPS  (edit these as your shop's conventions evolve)
# ===========================================================================

# Match-site metro  ->  AWC coordination region.
# NOTE: Kansas City is the ambiguous one — your briefings flip between
# "Midwest" and "Central Plains". Change the value below if you standardize.
METRO_TO_REGION = {
    "Atlanta":      "Southeast",
    "Miami":        "Southeast",
    "Boston":       "Northeast",
    "New York/NJ":  "Northeast",
    "Philadelphia": "Northeast",
    "Dallas":       "Southern Plains",
    "Houston":      "Southern Plains",
    "Kansas City":  "Midwest",       # <-- assumption; flip to "Central Plains" if needed
    "Los Angeles":  "West Coast",
    "Santa Clara":  "West Coast",
    "Seattle":      "Northwest",
}


def build_icao_region_map(presets, metro_to_region=METRO_TO_REGION):
    """Reverse the PRESETS dict into ICAO -> region, so the narrative can roll
    individual airports up to a coordination region. Reuses the metro groupings
    you already maintain in app.py, so there's no second list to keep in sync."""
    region_map = {}
    for key, icao_str in presets.items():
        if not key.startswith("WC Site - "):
            continue
        metro  = key.replace("WC Site - ", "")
        region = metro_to_region.get(metro)
        if region is None:               # e.g. "All Sites" — its airports are
            continue                     # already covered by the per-metro keys
        for icao in icao_str.split(","):
            region_map[icao.strip().upper()] = region
    return region_map


# ===========================================================================
# 2. HAZARD HELPERS
# ===========================================================================

def simplify_hazard(driver):
    """Collapse get_primary_driver()'s detailed labels (e.g. 'IFR/LIFR
    ceilings') into the plain briefing vocabulary used on the slides."""
    d = (driver or "").lower()
    if "thunder" in d:        return "Thunderstorms"
    if "freezing" in d:       return "Icing"
    if "wintry" in d:         return "Wintry precip"
    if "ceiling" in d:        return "Low ceilings"
    if "visibility" in d:     return "Low visibility"
    if "wind" in d:           return "Wind"
    return "None"


def coordination_products(driver):
    """Hazard -> required coordination products. This is a deterministic rule,
    not a judgment call: it mirrors the 'Coordination Need' column in your
    actual matrix (convection -> TCF added; ceilings/icing -> G-AIRMETs; etc.).
    NOTE: smoke/blowing dust aren't detectable from NBM NBS, so they never
    surface here and must be added by the forecaster."""
    d = (driver or "").lower()
    if "thunder" in d:                      return "TAF, TCF, G-AIRMETs"
    if "freezing" in d or "wintry" in d:    return "TAF, G-AIRMETs"   # icing
    if "ceiling" in d or "visibility" in d: return "TAF, G-AIRMETs"
    if "wind" in d:                         return "TAF"
    return "—"


# ===========================================================================
# 3. THE ONE AGGREGATION EVERYTHING READS
# ===========================================================================

def aggregate_regions(summary_df, region_map):
    """Roll the per-site summary up to regions.
    Returns {region: {'max_level': int, 'hazards': {hazard: worst_level}, 'sites': [...]}}."""
    regions = {}
    for _, row in summary_df.iterrows():
        station = row["Station"]
        level   = int(row["Max Impact Level"])
        hazard  = simplify_hazard(str(row["Primary Concern"]))
        region  = region_map.get(station, "Other")

        r = regions.setdefault(region, {"max_level": 0, "hazards": {}, "sites": []})
        r["max_level"] = max(r["max_level"], level)
        r["sites"].append(station)
        if level >= 1 and hazard != "None":
            # keep the worst level seen for each hazard within the region
            r["hazards"][hazard] = max(r["hazards"].get(hazard, 0), level)
    return regions


def _hazards_by_severity(hazards):
    """Hazard names ordered worst-first."""
    return [h for h, _ in sorted(hazards.items(), key=lambda kv: kv[1], reverse=True)]


# ===========================================================================
# 4. FORMATTERS (each just reads the aggregation)
# ===========================================================================

def format_top_concerns(regions):
    """Bullet sentences for regions at Moderate impact or worse, worst-first.
    e.g. '• Low ceilings and thunderstorms across the Southeast.'"""
    notable = sorted(
        [(r, v) for r, v in regions.items() if v["max_level"] >= 2],
        key=lambda kv: kv[1]["max_level"], reverse=True,
    )
    if not notable:
        return ["• Few or no significant aviation impacts expected this period."]

    lines = []
    for region, v in notable:
        hz = _hazards_by_severity(v["hazards"])
        if not hz:
            continue
        if len(hz) == 1:
            phrase = hz[0].lower()
        else:
            phrase = ", ".join(h.lower() for h in hz[:-1]) + f" and {hz[-1].lower()}"
        sentence = (f"{phrase} at additional sites" if region == "Other"
                    else f"{phrase} across the {region}")
        sentence = sentence[0].upper() + sentence[1:]
        lines.append(f"• {sentence}.")
    return lines


def format_coordination_focus(regions):
    """Region: hazard/hazard lines for regions at Minor impact or worse.
    e.g. 'Southeast: Low ceilings/Thunderstorms'"""
    notable = sorted(
        [(r, v) for r, v in regions.items() if v["max_level"] >= 1],
        key=lambda kv: kv[1]["max_level"], reverse=True,
    )
    lines = []
    for region, v in notable:
        hz = _hazards_by_severity(v["hazards"])
        if hz:
            lines.append(f"{region}: {'/'.join(hz)}")
    return lines or ["No regional coordination focus indicated."]


def suggest_national_concern(regions):
    """Rollup -> Low / Elevated / High.

    CALIBRATION WARNING: this leans MORE aggressive than your shop. Your own
    Day-2 matrix had Majors in four regions yet you called it 'Elevated', not
    'High'. So treat this as a starting suggestion and expect to dial it back —
    that's why it's marked [auto-suggested] in the output."""
    high = [r for r, v in regions.items() if v["max_level"] >= 3]
    med  = [r for r, v in regions.items() if v["max_level"] == 2]
    if len(high) >= 4:        return "High"
    if high or med:           return "Elevated"
    if any(v["max_level"] >= 1 for v in regions.values()): return "Low"
    return "Low"


# ===========================================================================
# 5. MAIN ENTRY POINT
# ===========================================================================

def build_dss_narrative(summary_df, region_map, selected_preset, time_window):
    """Assemble the full editable Slide-1 draft text."""
    site_name = selected_preset.replace("WC Site - ", "")
    if summary_df.empty:
        return "No significant aviation weather impacts in the selected NBM window."

    regions = aggregate_regions(summary_df, region_map)

    out = [
        f"{site_name} World Cup Aviation Outlook – {time_window}",
        "",
        f"National Aviation Concern: {suggest_national_concern(regions)}    [auto-suggested – adjust]",
        "Confidence: ______    [forecaster to set: Low / Medium / High]",
        "",
        "Top Aviation Concerns:",
        *format_top_concerns(regions),
        "",
        "Coordination Focus:",
        *format_coordination_focus(regions),
        "",
        "Watch List – Next Coordination Cycle:",
        "• ______    [forecaster to set – persistence/trend judgment]",
        "",
        "Operational Notes:",
        "• Draft generated from NBM NBS guidance; forecaster review required before dissemination.",
        "• National Concern is an auto-suggested starting value; Confidence and Watch List are manual.",
        "• Smoke/blowing dust are not detected by NBM NBS and must be added manually.",
    ]
    return "\n".join(out)
