#!/usr/bin/env python3
"""
process_science.py  —  Vela Lab Science Export
===============================================
Intégration dans nmea_process.py :
  1. En tête (avec les imports) :
         from process_science import run_science_export

  2. En fin de boucle, après generate_pdf(...) :
         run_science_export(df, df_events, meta, log_dir, base)

Sortie : <log_dir>/<base>_science.xlsx
  Toutes les lignes dans un seul fichier :
    - 1 ligne HYP par jour (HYPERNET ON → OFF)
    - 1 ligne NET + 1 ligne BUCKET par event NET ON/OFF
      (BUCKET = event OTHER/BUCKET le plus proche dans la fenêtre NET)
"""

import os
import math
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ─────────────────────────────────────────────────────────────────────────────
# COMPTEURS GLOBAUX (persistants entre legs dans la même exécution)
# ─────────────────────────────────────────────────────────────────────────────
_HYP_COUNTER = 0
_BIO_COUNTER = 0


# ─────────────────────────────────────────────────────────────────────────────
# TIMESTAMP  — tz-safe
# ─────────────────────────────────────────────────────────────────────────────

def _to_ns(ts) -> int:
    """Timestamp quelconque → int64 ns UTC naïf."""
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is not None:
            t = t.tz_convert("UTC").tz_localize(None)
        return t.value
    except Exception:
        return 0


def _normalize_events(df_events):
    """
    Normalise la colonne timestamp en naive UTC datetime64[ns].
    nmea_process produit un mélange tz-aware / tz-naive → sort_values() planterait.
    """
    if df_events.empty or "timestamp" not in df_events.columns:
        return df_events
    df = df_events.copy()
    df["timestamp"] = df["timestamp"].apply(
        lambda x: pd.Timestamp(_to_ns(x)) if pd.notna(x) else pd.NaT
    )
    return df


def _series_to_ns(s: pd.Series) -> np.ndarray:
    s2 = pd.to_datetime(s)
    if s2.dt.tz is not None:
        s2 = s2.dt.tz_convert("UTC").dt.tz_localize(None)
    return s2.values.astype("datetime64[ns]").astype("int64")


# ─────────────────────────────────────────────────────────────────────────────
# FORMATAGE  (virgule comme séparateur décimal)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(value, decimals=6) -> str:
    if value is None or value == "":
        return ""
    try:
        v = float(value)
        if math.isnan(v):
            return ""
        return f"{v:.{decimals}f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value)

def _fmt2(v): return _fmt(v, 2)
def _fmt4(v): return _fmt(v, 4)
def _fmt6(v): return _fmt(v, 6)
def _fmt8(v): return _fmt(v, 8)


# ─────────────────────────────────────────────────────────────────────────────
# GÉOGRAPHIE
# ─────────────────────────────────────────────────────────────────────────────

def _decimal_to_dms(decimal: float, is_lon: bool = False) -> str:
    """Décimal → °MM,SSSS'N/S/E/W (virgule, Google Sheets FR)."""
    if decimal is None or (isinstance(decimal, float) and math.isnan(decimal)):
        return ""
    direction = ("E" if decimal >= 0 else "W") if is_lon else ("N" if decimal >= 0 else "S")
    a = abs(decimal)
    deg = int(a)
    minutes = f"{(a - deg) * 60.0:.4f}".replace(".", ",")
    dw = 3 if is_lon else 2
    return f"{deg:0{dw}d}°{minutes}'{direction}"


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(phi1) * math.cos(phi2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ─────────────────────────────────────────────────────────────────────────────
# INTERPOLATION NMEA
# ─────────────────────────────────────────────────────────────────────────────

def _build_nmea_arrays(df_nmea):
    if df_nmea.empty or "datetime" not in df_nmea.columns:
        return np.array([]), np.array([]), np.array([]), np.array([]), False
    df_t = df_nmea.dropna(subset=["datetime", "lat_raw", "lon_raw"]).copy()
    df_t = df_t.sort_values("datetime").reset_index(drop=True)
    if df_t.empty:
        return np.array([]), np.array([]), np.array([]), np.array([]), False
    ts_ns = _series_to_ns(df_t["datetime"])
    lat   = df_t["lat_raw"].values.astype(float)
    lon   = df_t["lon_raw"].values.astype(float)
    sog   = (df_t["SOG_RMC"].values.astype(float)
             if "SOG_RMC" in df_t.columns else np.full(len(df_t), np.nan))
    return ts_ns, lat, lon, sog, True


def _interp(ts_ns_arr, lat_arr, lon_arr, sog_arr, t_ns):
    if len(ts_ns_arr) == 0:
        return np.nan, np.nan, np.nan
    idx = np.searchsorted(ts_ns_arr, t_ns, side="left")
    if idx == 0:
        return float(lat_arr[0]), float(lon_arr[0]), float(sog_arr[0])
    if idx >= len(ts_ns_arr):
        return float(lat_arr[-1]), float(lon_arr[-1]), float(sog_arr[-1])
    dt = ts_ns_arr[idx] - ts_ns_arr[idx - 1]
    if dt == 0:
        return float(lat_arr[idx]), float(lon_arr[idx]), float(sog_arr[idx])
    a = (t_ns - ts_ns_arr[idx - 1]) / dt
    lat = float(lat_arr[idx-1]) + a * (float(lat_arr[idx]) - float(lat_arr[idx-1]))
    lon = float(lon_arr[idx-1]) + a * (float(lon_arr[idx]) - float(lon_arr[idx-1]))
    sog = float(sog_arr[idx-1]) + a * (float(sog_arr[idx]) - float(sog_arr[idx-1]))
    return lat, lon, sog


def _mean_sog(ts_ns_arr, sog_arr, t0, t1) -> float:
    mask = (ts_ns_arr >= t0) & (ts_ns_arr <= t1)
    vals = sog_arr[mask]
    vals = vals[~np.isnan(vals)]
    return float(np.mean(vals)) if len(vals) > 0 else np.nan


# ─────────────────────────────────────────────────────────────────────────────
# COLONNES
# ─────────────────────────────────────────────────────────────────────────────

COLUMNS = [
    ("station_id",      "Stations id",           "Vela Lab station",               "auto"),
    ("station_type",    "Stations id",           "Vela Lab station type",          "auto"),
    ("cloud_coverage",  "Stations id",           "cloud_coverage (%)",             "manual"),
    ("date_start",      "Start",                 "date_start (YYYY-MM-DD)",        "auto"),
    ("time_start",      "Start",                 "time_start (UTC HH:MM:SS)",      "auto"),
    ("lat_start_dms",   "Start",                 "lat_start (°N)",                 "auto"),
    ("lon_start_dms",   "Start",                 "lon_start (°E)",                 "auto"),
    ("lat_start_dec",   "Start",                 "lat_start (decimal °N)",         "auto"),
    ("lon_start_dec",   "Start",                 "lon_start (decimal °E)",         "auto"),
    ("date_end",        "End",                   "date_end (YYYY-MM-DD)",          "auto"),
    ("time_end",        "End",                   "time_end (UTC HH:MM:SS)",        "auto"),
    ("lat_end_dms",     "End",                   "lat_end (°N)",                   "auto"),
    ("lon_end_dms",     "End",                   "lon_end (°E)",                   "auto"),
    ("lat_end_dec",     "End",                   "lat_end (decimal °N)",           "auto"),
    ("lon_end_dec",     "End",                   "lon_end (decimal °E)",           "auto"),
    ("device",          "End",                   "device",                         "manual"),
    ("mouth_diam",      "End",                   "mouth_Ø (cm)",                   "manual"),
    ("depth",           "Depth",                 "depth (m)",                      "manual"),
    ("size_min",        "Depth",                 "size_min (um)",                  "manual"),
    ("size_max",        "Comments",              "size_max (um)",                  "manual"),
    ("comment",         "Comments",              "comment about the sampling",     "manual"),
    ("duration_min",    "Duration & Distance",   "duration (min)",                 "auto"),
    ("dist_km",         "Duration & Distance",   "distance (km) Haversine",        "auto"),
    ("sog_haversine",   "Duration & Distance",   "SOG_moy (kts)",                  "auto"),
    ("sog_nmea",        "Duration & Distance",   "SOG_moy NMEA (kts)",             "auto"),
    ("surface_mouth",   "Volume",                "surface mouth (m²)",             "formula"),
    ("vol_net",         "Volume",                "vol_net theoric (m³)",           "formula"),
    ("vol_net_conc",    "Volume",                "vol_net_conc (ml)",              "manual"),
    ("planktospace_st", "PlanktoSpace station",  "PlanktoSpace station",           "manual"),
    ("barcode",         "PlanktoSpace station",  "barcode",                        "manual"),
    ("vol_lamprey",     "Lamprey analysis",      "vol_lamprey (ml)",               "manual"),
    ("time_filt_start", "Lamprey analysis",      "time_filtration_start (UTC)",    "manual"),
    ("time_filt_end",   "Lamprey analysis",      "time_filtration_end (UTC)",      "manual"),
    ("saturation",      "Lamprey analysis",      "saturation (Y/N)",               "manual"),
    ("vol_lamprey_fil", "Lamprey analysis",      "vol_lamprey_filtrate (ml)",      "manual"),
    ("vol_curiosity",   "QCuriosity analysis",   "vol_curiosity (ml)",             "manual"),
    ("conc_dil",        "QCuriosity analysis",   "conc_or_dilution",               "manual"),
    ("nb_images",       "QCuriosity analysis",   "nb_images",                      "manual"),
    ("vol_pscope",      "Planktoscope analysis", "vol_pscope (ml)",                "manual"),
    ("acq",             "Planktoscope analysis", "acq",                            "manual"),
    ("seg",             "Planktoscope analysis", "seg",                            "manual"),
    ("facteur_pscope",  "Planktoscope analysis", "facteur_pscope",                 "manual"),
    ("vol_pscope_eff",  "Planktoscope analysis", "vol_pscope_effectif (ml)",       "manual"),
    ("vol_imaged",      "Planktoscope analysis", "vol_imaged (ml)",                "manual"),
    ("acq_imaged_vol",  "Planktoscope analysis", "acq_imaged_volume (ml)",         "manual"),
    ("conc_filet",      "Planktoscope analysis", "conc_filet (m³/organisme)",      "manual"),
    ("time_turbi",      "Turbidometre",          "time_turbi (UTC HH:MM:SS)",      "manual"),
    ("turbi_1",         "Turbidometre",          "turbi_1",                        "manual"),
    ("turbi_2",         "Turbidometre",          "turbi_2",                        "manual"),
    ("turbi_3",         "Turbidometre",          "turbi_3",                        "manual"),
    ("secchi",          "Secchi Disk",           "Secchi Disk",                    "manual"),
]


# ─────────────────────────────────────────────────────────────────────────────
# STYLES
# ─────────────────────────────────────────────────────────────────────────────

_NAVY       = "1A3A5C"
_WHITE      = "FFFFFF"
_LGRAY      = "F0F4FA"
_LBLUE      = "D6EAF8"
_GREEN_PALE = "EAF6EE"
_YELLOW     = "FFF3CD"
_ORANGE     = "FFE0B2"

def _hfill(h):      return PatternFill("solid", fgColor=h)
def _font(bold=False, color="000000", size=9):
    return Font(bold=bold, color=color, size=size, name="Arial")
def _border():
    s = Side(style="thin", color="BBBBBB")
    return Border(left=s, right=s, top=s, bottom=s)
def _center():      return Alignment(horizontal="center", vertical="center", wrap_text=True)
def _left():        return Alignment(horizontal="left",   vertical="center", wrap_text=True)


# ─────────────────────────────────────────────────────────────────────────────
# ÉCRITURE XLSX
# ─────────────────────────────────────────────────────────────────────────────

def _write_xlsx(rows: list, out_path: str, leg_label: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Science"
    n_cols = len(COLUMNS)

    # Ligne 1 — groupes fusionnés
    groups, prev_g, start_c = [], None, 1
    for i, (_, g, _, _) in enumerate(COLUMNS):
        if g != prev_g:
            if prev_g is not None:
                groups.append((prev_g, start_c, i))
            prev_g, start_c = g, i + 1
    groups.append((prev_g, start_c, n_cols))

    for g_label, c_start, c_end in groups:
        cell = ws.cell(row=1, column=c_start, value=g_label)
        cell.fill = _hfill(_NAVY); cell.font = _font(bold=True, color=_WHITE, size=9)
        cell.alignment = _center(); cell.border = _border()
        if c_end > c_start:
            ws.merge_cells(start_row=1, start_column=c_start, end_row=1, end_column=c_end)

    # Ligne 2 — noms de colonnes
    for i, (_, _, col_label, origin) in enumerate(COLUMNS):
        bg = _LBLUE if origin == "auto" else (_YELLOW if origin == "formula" else _LGRAY)
        cell = ws.cell(row=2, column=i+1, value=col_label)
        cell.fill = _hfill(bg); cell.font = _font(bold=True, size=8)
        cell.alignment = _center(); cell.border = _border()

    # Indices pour formules
    mouth_ltr = get_column_letter(next(i+1 for i,(k,*_) in enumerate(COLUMNS) if k=="mouth_diam"))
    dist_ltr  = get_column_letter(next(i+1 for i,(k,*_) in enumerate(COLUMNS) if k=="dist_km"))
    surf_ltr  = get_column_letter(next(i+1 for i,(k,*_) in enumerate(COLUMNS) if k=="surface_mouth"))

    # Données
    for r_idx, session in enumerate(rows):
        row    = 3 + r_idx
        row_bg = session.get("_row_bg", None)

        for col_idx, (key, _, _, origin) in enumerate(COLUMNS):
            col = col_idx + 1
            if key == "surface_mouth":
                val = f'=IF({mouth_ltr}{row}<>"",PI()*(({mouth_ltr}{row}/100)/2)^2,"")'
            elif key == "vol_net":
                val = f'=IF(AND({surf_ltr}{row}<>"",{dist_ltr}{row}<>""),{surf_ltr}{row}*{dist_ltr}{row},"")'
            else:
                val = session.get(key, "")
                if val is None or val is np.nan:
                    val = ""

            cell = ws.cell(row=row, column=col, value=val)
            cell.border = _border(); cell.alignment = _left(); cell.font = _font(size=9)
            if origin == "formula":
                cell.fill = _hfill(_YELLOW)
            elif origin == "auto" and val not in (None, "", np.nan):
                cell.fill = _hfill(row_bg if row_bg else _GREEN_PALE)
            elif row_bg:
                cell.fill = _hfill(row_bg)

    # Légende
    lr = 3 + len(rows) + 2
    for i, (color, label) in enumerate([
        (_GREEN_PALE, "Rempli automatiquement depuis NMEA"),
        (_YELLOW,     "Calculé par formule Excel (saisir le Ø filet pour activer)"),
        (_ORANGE,     "Ligne Bucket (position = début du NET de la même station)"),
        (_WHITE,      "À remplir manuellement après la mesure"),
    ]):
        c = ws.cell(row=lr+i, column=1, value=label)
        c.fill = _hfill(color); c.font = _font(size=8); c.border = _border()

    ws.cell(row=lr+6, column=1,
            value=f"Généré par process_science.py — {leg_label} — "
                  f"{len(rows)} ligne(s). "
                  "Vérifier puis copier-coller dans Bio_sampling_Vela_Lab (Google Drive)."
            ).font = _font(size=8, color="666666")

    # Largeurs
    widths = {
        "station_id":22,"station_type":12,"cloud_coverage":10,
        "date_start":14,"time_start":14,
        "lat_start_dms":22,"lon_start_dms":22,
        "lat_start_dec":16,"lon_start_dec":16,
        "date_end":14,"time_end":14,
        "lat_end_dms":22,"lon_end_dms":22,
        "lat_end_dec":16,"lon_end_dec":16,
        "device":16,"mouth_diam":10,"depth":8,
        "size_min":9,"size_max":9,"comment":28,
        "duration_min":11,"dist_km":16,
        "sog_haversine":13,"sog_nmea":13,
        "surface_mouth":14,"vol_net":18,
    }
    for i, (key,*_) in enumerate(COLUMNS):
        ws.column_dimensions[get_column_letter(i+1)].width = widths.get(key, 11)

    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 42
    ws.freeze_panes = "A3"
    wb.save(out_path)


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION HYPERNET  (1 ligne par jour)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_hyp(ev, ts_ns_arr, lat_arr, lon_arr, sog_arr, has_nmea):
    global _HYP_COUNTER

    hyp = ev[ev["event_type"] == "HYPERNET"].reset_index(drop=True)
    if hyp.empty:
        return []

    # Regroupe par jour : premier ON → dernier OFF
    day_map = {}
    open_ns = open_ts = None
    for _, row in hyp.iterrows():
        detail = str(row["event_detail"]).strip().upper()
        t_ns   = _to_ns(row["timestamp"])
        ts     = pd.Timestamp(t_ns)
        day    = ts.date()
        if detail == "ON":
            if day not in day_map:
                day_map[day] = {"on_ns": t_ns, "on_ts": ts, "off_ns": None, "off_ts": None}
            open_ns, open_ts = t_ns, ts
        elif detail == "OFF" and open_ns is not None:
            if day in day_map:
                day_map[day]["off_ns"] = t_ns
                day_map[day]["off_ts"] = ts
            open_ns = None

    rows = []
    for day in sorted(day_map):
        d = day_map[day]
        if d["off_ns"] is None:
            continue
        _HYP_COUNTER += 1
        t0, t1 = d["on_ns"], d["off_ns"]
        ts_s, ts_e = d["on_ts"], d["off_ts"]
        dur = (t1 - t0) / 1e9 / 60.0

        if has_nmea:
            lat_s, lon_s, _ = _interp(ts_ns_arr, lat_arr, lon_arr, sog_arr, t0)
            lat_e, lon_e, _ = _interp(ts_ns_arr, lat_arr, lon_arr, sog_arr, t1)
            sog_m            = _mean_sog(ts_ns_arr, sog_arr, t0, t1)
        else:
            lat_s = lon_s = lat_e = lon_e = sog_m = np.nan

        dist = _haversine_km(lat_s, lon_s, lat_e, lon_e) if has_nmea and not any(
            math.isnan(x) for x in [lat_s, lon_s, lat_e, lon_e]) else np.nan
        sog_h = dist / 1.852 / (dur / 60.0) if not math.isnan(dist) and dur > 0 else np.nan

        rows.append({
            "station_id":    f"Vela_Lab_hyp_st{_HYP_COUNTER:02d}",
            "station_type":  "hyp",
            "date_start":    ts_s.strftime("%Y-%m-%d"),
            "time_start":    ts_s.strftime("%H:%M:%S"),
            "date_end":      ts_e.strftime("%Y-%m-%d"),
            "time_end":      ts_e.strftime("%H:%M:%S"),
            "lat_start_dms": _decimal_to_dms(lat_s),
            "lon_start_dms": _decimal_to_dms(lon_s, True),
            "lat_start_dec": _fmt8(lat_s),
            "lon_start_dec": _fmt8(lon_s),
            "lat_end_dms":   _decimal_to_dms(lat_e),
            "lon_end_dms":   _decimal_to_dms(lat_e, True),
            "lat_end_dec":   _fmt8(lat_e),
            "lon_end_dec":   _fmt8(lon_e),
            "duration_min":  _fmt2(dur),
            "dist_km":       _fmt6(dist),
            "sog_haversine": _fmt4(sog_h),
            "sog_nmea":      _fmt4(sog_m),
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION BIO  (NET + BUCKET)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_bio(ev, ts_ns_arr, lat_arr, lon_arr, sog_arr, has_nmea):
    global _BIO_COUNTER

    net_ev = ev[ev["event_type"] == "NET"].reset_index(drop=True)
    if net_ev.empty:
        return []

    # Tous les events BUCKET (OTHER avec detail contenant "BUCKET")
    bucket_ev = ev[
        (ev["event_type"] == "OTHER") &
        (ev["event_detail"].astype(str).str.upper().str.strip() == "BUCKET")
    ].reset_index(drop=True)

    rows = []
    open_ns = open_ts = None

    for _, row in net_ev.iterrows():
        detail = str(row["event_detail"]).strip().upper()
        t_ns   = _to_ns(row["timestamp"])
        ts     = pd.Timestamp(t_ns)

        if detail == "ON":
            open_ns, open_ts = t_ns, ts

        elif detail == "OFF" and open_ns is not None:
            _BIO_COUNTER += 1
            t0, t1 = open_ns, t_ns
            ts_s, ts_e = open_ts, ts
            open_ns = None
            dur = (t1 - t0) / 1e9 / 60.0

            if has_nmea:
                lat_s, lon_s, _ = _interp(ts_ns_arr, lat_arr, lon_arr, sog_arr, t0)
                lat_e, lon_e, _ = _interp(ts_ns_arr, lat_arr, lon_arr, sog_arr, t1)
                sog_m            = _mean_sog(ts_ns_arr, sog_arr, t0, t1)
            else:
                lat_s = lon_s = lat_e = lon_e = sog_m = np.nan

            dist = _haversine_km(lat_s, lon_s, lat_e, lon_e) if has_nmea and not any(
                math.isnan(x) for x in [lat_s, lon_s, lat_e, lon_e]) else np.nan
            sog_h = dist / 1.852 / (dur / 60.0) if not math.isnan(dist) and dur > 0 else np.nan

            base = f"Vela_Lab_bio_st{_BIO_COUNTER:02d}"

            # ── Ligne filet (NET) ──────────────────────────────────────────
            rows.append({
                "station_id":    f"{base}_1",
                "station_type":  "bio",
                "date_start":    ts_s.strftime("%Y-%m-%d"),
                "time_start":    ts_s.strftime("%H:%M:%S"),
                "date_end":      ts_e.strftime("%Y-%m-%d"),
                "time_end":      ts_e.strftime("%H:%M:%S"),
                "lat_start_dms": _decimal_to_dms(lat_s),
                "lon_start_dms": _decimal_to_dms(lon_s, True),
                "lat_start_dec": _fmt8(lat_s),
                "lon_start_dec": _fmt8(lon_s),
                "lat_end_dms":   _decimal_to_dms(lat_e),
                "lon_end_dms":   _decimal_to_dms(lon_e, True),
                "lat_end_dec":   _fmt8(lat_e),
                "lon_end_dec":   _fmt8(lon_e),
                "device":        "Coryphaena",
                "mouth_diam":    "6",
                "depth":         "1",
                "size_min":      "50",
                "size_max":      "200",
                "duration_min":  _fmt2(dur),
                "dist_km":       _fmt6(dist),
                "sog_haversine": _fmt4(sog_h),
                "sog_nmea":      _fmt4(sog_m),
            })

            # ── Lignes BUCKET : un par event OTHER/BUCKET dans la fenêtre NET ──
            buckets_in_window = bucket_ev[
                (bucket_ev["timestamp"] >= ts_s) &
                (bucket_ev["timestamp"] <= ts_e + pd.Timedelta(hours=2))
            ].reset_index(drop=True)

            for b_idx, b_row in buckets_in_window.iterrows():
                b_ns = _to_ns(b_row["timestamp"])
                b_ts = pd.Timestamp(b_ns)
                if has_nmea:
                    b_lat, b_lon, b_sog = _interp(ts_ns_arr, lat_arr, lon_arr, sog_arr, b_ns)
                else:
                    b_lat = b_lon = b_sog = np.nan

                rows.append({
                    "station_id":    f"{base}_{b_idx + 2}",
                    "station_type":  "bio",
                    "date_start":    b_ts.strftime("%Y-%m-%d"),
                    "time_start":    b_ts.strftime("%H:%M:%S"),
                    "lat_start_dms": _decimal_to_dms(b_lat),
                    "lon_start_dms": _decimal_to_dms(b_lon, True),
                    "lat_start_dec": _fmt8(b_lat),
                    "lon_start_dec": _fmt8(b_lon),
                    "device":        f"Bucket {b_idx + 1}",
                    "depth":         "0,3",
                    "sog_nmea":      _fmt4(b_sog),
                    "_row_bg":       _ORANGE,
                })

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE PUBLIC
# ─────────────────────────────────────────────────────────────────────────────

def run_science_export(df, df_events, meta, log_dir: str, base: str):
    departure   = meta.get("departure", "") or "?"
    destination = meta.get("destination", "") or "?"
    leg_label   = f"{departure} → {destination}"
    print(f"  [SCIENCE] Export science — {leg_label}")

    # Normalise df_events (mélange tz-aware/tz-naive → naive UTC)
    ev = _normalize_events(df_events)
    if ev.empty or "event_type" not in ev.columns:
        print("  [SCIENCE] Aucun événement trouvé.")
        return

    ev = ev.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # Prépare arrays NMEA
    df_nmea = pd.DataFrame()
    if not df.empty and "datetime" in df.columns:
        df_nmea = df.dropna(subset=["datetime"]).copy()
        df_nmea = df_nmea.sort_values("datetime").reset_index(drop=True)
    ts_ns_arr, lat_arr, lon_arr, sog_arr, has_nmea = _build_nmea_arrays(df_nmea)

    # Extrait les lignes
    hyp_rows = _extract_hyp(ev, ts_ns_arr, lat_arr, lon_arr, sog_arr, has_nmea)
    bio_rows = _extract_bio(ev, ts_ns_arr, lat_arr, lon_arr, sog_arr, has_nmea)

    all_rows = hyp_rows + bio_rows

    if not all_rows:
        print("  [SCIENCE] Aucune session HYPERNET ou NET détectée.")
        return

    out_path = os.path.join(log_dir, base + "_science.xlsx")
    _write_xlsx(all_rows, out_path, leg_label)

    n_hyp = len(hyp_rows)
    n_net = len([r for r in bio_rows if r["station_id"].endswith("_1")])
    n_bkt = len([r for r in bio_rows if not r["station_id"].endswith("_1")])
    print(f"  [SCIENCE] {n_hyp} ligne(s) HYP + {n_net} filet(s) + {n_bkt} bucket(s) → {out_path}")
    print( "  [SCIENCE] → Vérifier puis copier-coller dans Bio_sampling_Vela_Lab (Google Drive).")
