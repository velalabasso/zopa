#!/usr/bin/env python3
"""
process_science.py  —  Vela Lab Science Export
===============================================
Intégration dans nmea_process.py :
  1. En tête (avec les imports) :
         from process_science import run_science_export

  2. En fin de boucle, après generate_pdf(...) :
         run_science_export(df, df_events, meta, log_dir, base)

Sortie :  <log_dir>/<base>_science_hypernet.xlsx
"""

import os
import math
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def _to_ns(ts) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.value


def _series_to_ns(s: pd.Series) -> np.ndarray:
    s2 = pd.to_datetime(s)
    if s2.dt.tz is not None:
        s2 = s2.dt.tz_convert("UTC").dt.tz_localize(None)
    return s2.values.astype("datetime64[ns]").astype("int64")


def _decimal_to_dms(decimal: float, is_lon: bool = False) -> str:
    if decimal is None or (isinstance(decimal, float) and math.isnan(decimal)):
        return ""
    direction = ("E" if decimal >= 0 else "W") if is_lon else ("N" if decimal >= 0 else "S")
    a = abs(decimal)
    deg = int(a)
    minutes = (a - deg) * 60.0
    dw = 3 if is_lon else 2
    return f"{deg:0{dw}d}°{minutes:.4f}'{direction}"


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _interpolate_position(ts_ns_arr, lat_arr, lon_arr, sog_arr, t_ns):
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
    alpha = (t_ns - ts_ns_arr[idx - 1]) / dt
    lat = float(lat_arr[idx - 1]) + alpha * (float(lat_arr[idx]) - float(lat_arr[idx - 1]))
    lon = float(lon_arr[idx - 1]) + alpha * (float(lon_arr[idx]) - float(lon_arr[idx - 1]))
    sog = float(sog_arr[idx - 1]) + alpha * (float(sog_arr[idx]) - float(sog_arr[idx - 1]))
    return lat, lon, sog


def _mean_sog(ts_ns_arr, sog_arr, t0_ns, t1_ns) -> float:
    mask = (ts_ns_arr >= t0_ns) & (ts_ns_arr <= t1_ns)
    vals = sog_arr[mask]
    vals = vals[~np.isnan(vals)]
    return float(np.mean(vals)) if len(vals) > 0 else np.nan


def extract_hypernet_sessions(df_events, df_nmea):
    if df_events.empty or "event_type" not in df_events.columns:
        return []

    ev = (
        df_events[df_events["event_type"] == "HYPERNET"]
        .dropna(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    if ev.empty:
        return []

    has_nmea = False
    ts_ns_arr = lat_arr = lon_arr = sog_arr = np.array([])
    if not df_nmea.empty and "datetime" in df_nmea.columns:
        df_t = df_nmea.dropna(subset=["datetime", "lat_raw", "lon_raw"]).copy()
        df_t = df_t.sort_values("datetime").reset_index(drop=True)
        if not df_t.empty:
            ts_ns_arr = _series_to_ns(df_t["datetime"])
            lat_arr   = df_t["lat_raw"].values.astype(float)
            lon_arr   = df_t["lon_raw"].values.astype(float)
            sog_arr   = (df_t["SOG_RMC"].values.astype(float)
                         if "SOG_RMC" in df_t.columns
                         else np.full(len(df_t), np.nan))
            has_nmea  = True

    sessions = []
    open_ns  = None
    open_ts  = None

    for _, row in ev.iterrows():
        detail = str(row.get("event_detail", "")).strip().upper()
        t_ns   = _to_ns(row["timestamp"])
        ts     = pd.Timestamp(t_ns)

        if detail == "ON":
            open_ns = t_ns
            open_ts = ts
        elif detail == "OFF" and open_ns is not None:
            t0_ns, t1_ns   = open_ns, t_ns
            ts_start, ts_end = open_ts, ts
            open_ns = None

            duration_min = (t1_ns - t0_ns) / 1e9 / 60.0

            if has_nmea:
                lat_s, lon_s, _ = _interpolate_position(ts_ns_arr, lat_arr, lon_arr, sog_arr, t0_ns)
                lat_e, lon_e, _ = _interpolate_position(ts_ns_arr, lat_arr, lon_arr, sog_arr, t1_ns)
                sog_moy         = _mean_sog(ts_ns_arr, sog_arr, t0_ns, t1_ns)
            else:
                lat_s = lon_s = lat_e = lon_e = sog_moy = np.nan

            dist_km = np.nan
            if not any(math.isnan(x) for x in [lat_s, lon_s, lat_e, lon_e]):
                dist_km = _haversine_km(lat_s, lon_s, lat_e, lon_e)

            sog_hyp = (dist_km / 1.852 / (duration_min / 60.0)
                       if not math.isnan(dist_km) and duration_min > 0
                       else np.nan)

            sessions.append({
                "date_start":    ts_start.strftime("%Y-%m-%d"),
                "time_start":    ts_start.strftime("%H:%M:%S"),
                "date_end":      ts_end.strftime("%Y-%m-%d"),
                "time_end":      ts_end.strftime("%H:%M:%S"),
                "lat_start_dec": round(lat_s, 8) if not math.isnan(lat_s) else "",
                "lon_start_dec": round(lon_s, 8) if not math.isnan(lon_s) else "",
                "lat_end_dec":   round(lat_e, 8) if not math.isnan(lat_e) else "",
                "lon_end_dec":   round(lon_e, 8) if not math.isnan(lon_e) else "",
                "lat_start_dms": _decimal_to_dms(lat_s, is_lon=False),
                "lon_start_dms": _decimal_to_dms(lon_s, is_lon=True),
                "lat_end_dms":   _decimal_to_dms(lat_e, is_lon=False),
                "lon_end_dms":   _decimal_to_dms(lon_e, is_lon=True),
                "duration_min":  round(duration_min, 2),
                "dist_km":       round(dist_km, 6) if not math.isnan(dist_km) else "",
                "sog_haversine": round(sog_hyp, 4) if not math.isnan(sog_hyp) else "",
                "sog_nmea":      round(sog_moy, 4) if not math.isnan(sog_moy) else "",
            })

    return sessions


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
]

_NAVY       = "1A3A5C"
_WHITE      = "FFFFFF"
_LGRAY      = "F0F4FA"
_LBLUE      = "D6EAF8"
_GREEN_PALE = "EAF6EE"
_YELLOW     = "FFF3CD"

def _hfill(h):
    return PatternFill("solid", fgColor=h)

def _font(bold=False, color="000000", size=9):
    return Font(bold=bold, color=color, size=size, name="Arial")

def _border_thin():
    s = Side(style="thin", color="BBBBBB")
    return Border(left=s, right=s, top=s, bottom=s)

def _center():
    return Alignment(horizontal="center", vertical="center", wrap_text=True)

def _left():
    return Alignment(horizontal="left", vertical="center", wrap_text=True)


def _write_xlsx(sessions, out_path, leg_label):
    wb = Workbook()
    ws = wb.active
    ws.title = "Hypernet"
    n_cols = len(COLUMNS)

    groups, prev_g, start_c = [], None, 1
    for i, (_, g, _, _) in enumerate(COLUMNS):
        if g != prev_g:
            if prev_g is not None:
                groups.append((prev_g, start_c, i))
            prev_g, start_c = g, i + 1
    groups.append((prev_g, start_c, n_cols))

    for g_label, c_start, c_end in groups:
        cell = ws.cell(row=1, column=c_start, value=g_label)
        cell.fill = _hfill(_NAVY)
        cell.font = _font(bold=True, color=_WHITE, size=9)
        cell.alignment = _center()
        cell.border = _border_thin()
        if c_end > c_start:
            ws.merge_cells(start_row=1, start_column=c_start, end_row=1, end_column=c_end)

    for i, (_, _, col_label, origin) in enumerate(COLUMNS):
        col = i + 1
        bg = _LBLUE if origin == "auto" else (_YELLOW if origin == "formula" else _LGRAY)
        cell = ws.cell(row=2, column=col, value=col_label)
        cell.fill = _hfill(bg)
        cell.font = _font(bold=True, size=8)
        cell.alignment = _center()
        cell.border = _border_thin()

    mouth_ltr = get_column_letter(next(i+1 for i,(k,*_) in enumerate(COLUMNS) if k=="mouth_diam"))
    dist_ltr  = get_column_letter(next(i+1 for i,(k,*_) in enumerate(COLUMNS) if k=="dist_km"))
    surf_ltr  = get_column_letter(next(i+1 for i,(k,*_) in enumerate(COLUMNS) if k=="surface_mouth"))

    for s_idx, session in enumerate(sessions):
        row = 3 + s_idx
        session["station_id"]   = f"Vela_Lab_hyp_st{s_idx + 1:02d}"
        session["station_type"] = "hyp"

        for col_idx, (key, _, _, origin) in enumerate(COLUMNS):
            col = col_idx + 1
            if key == "surface_mouth":
                val = f'=IF({mouth_ltr}{row}<>"",PI()*(({mouth_ltr}{row}/100)/2)^2,"")'
            elif key == "vol_net":
                val = f'=IF(AND({surf_ltr}{row}<>"",{dist_ltr}{row}<>""),{surf_ltr}{row}*{dist_ltr}{row},"")'
            else:
                val = session.get(key, "")

            cell = ws.cell(row=row, column=col, value="" if val is np.nan else val)
            cell.border = _border_thin()
            cell.alignment = _left()
            cell.font = _font(size=9)
            if origin == "auto" and val not in (None, "", np.nan):
                cell.fill = _hfill(_GREEN_PALE)
            elif origin == "formula":
                cell.fill = _hfill(_YELLOW)

    lr = 3 + len(sessions) + 2
    for i, (color, label) in enumerate([
        (_GREEN_PALE, "Rempli automatiquement depuis NMEA"),
        (_YELLOW,     "Calculé par formule Excel (saisir le Ø filet pour activer)"),
        (_WHITE,      "À remplir manuellement après la mesure"),
    ]):
        c = ws.cell(row=lr + i, column=1, value=label)
        c.fill = _hfill(color)
        c.font = _font(size=8)
        c.border = _border_thin()

    ws.cell(row=lr + 5, column=1,
            value=f"Généré par process_science.py — {leg_label} — "
                  f"{len(sessions)} session(s) Hypernet. "
                  "Vérifier puis copier-coller dans Bio_sampling_Vela_Lab (Google Drive)."
            ).font = _font(size=8, color="666666")

    widths = {
        "station_id": 22, "station_type": 12, "cloud_coverage": 10,
        "date_start": 14, "time_start": 14,
        "lat_start_dms": 20, "lon_start_dms": 20,
        "lat_start_dec": 14, "lon_start_dec": 14,
        "date_end": 14, "time_end": 14,
        "lat_end_dms": 20, "lon_end_dms": 20,
        "lat_end_dec": 14, "lon_end_dec": 14,
        "device": 14, "mouth_diam": 10, "depth": 8,
        "size_min": 9, "size_max": 9, "comment": 24,
        "duration_min": 11, "dist_km": 14,
        "sog_haversine": 12, "sog_nmea": 12,
        "surface_mouth": 14, "vol_net": 16,
    }
    for i, (key, *_) in enumerate(COLUMNS):
        ws.column_dimensions[get_column_letter(i + 1)].width = widths.get(key, 11)

    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 40
    ws.freeze_panes = "A3"
    wb.save(out_path)


def run_science_export(df, df_events, meta, log_dir: str, base: str):
    departure   = meta.get("departure", "") or "?"
    destination = meta.get("destination", "") or "?"
    leg_label   = f"{departure} → {destination}"
    print(f"  [SCIENCE] Extraction sessions Hypernet — {leg_label}")

    df_nmea = pd.DataFrame()
    if not df.empty and "datetime" in df.columns:
        df_nmea = df.dropna(subset=["datetime"]).copy()
        df_nmea = df_nmea.sort_values("datetime").reset_index(drop=True)

    sessions = extract_hypernet_sessions(df_events, df_nmea)

    if not sessions:
        print("  [SCIENCE] Aucune session Hypernet ON/OFF complète détectée.")
        return

    out_path = os.path.join(log_dir, base + "_science_hypernet.xlsx")
    _write_xlsx(sessions, out_path, leg_label)
    print(f"  [SCIENCE] {len(sessions)} session(s) → {out_path}")
    print( "  [SCIENCE] → Vérifier puis copier-coller dans Bio_sampling_Vela_Lab (Google Drive).")
