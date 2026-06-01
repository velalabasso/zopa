#!/usr/bin/env python3

import pandas as pd
import numpy as np
import os
import gzip
import io
import math

from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, Image,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# =========================================================
# LOCAL MAP DATA
# =========================================================

MAP_DIR  = os.path.expanduser("~/science/mapdata")
_MAPDATA = {}

def _load_mapdata():
    if not os.path.isdir(MAP_DIR):
        return
    for name in ["coastline","land","ocean","countries","lakes",
                 "rivers","bathy_200","bathy_500","bathy_1000",
                 "bathy_2000","bathy_3000","bathy_4000","bathy_5000"]:
        path = os.path.join(MAP_DIR, name + ".npy")
        if os.path.exists(path):
            try:
                _MAPDATA[name] = np.load(path, allow_pickle=False)
            except Exception as e:
                print(f"  [WARN] map {name}: {e}")

_load_mapdata()
HAS_MAPDATA = len(_MAPDATA) > 0
if not HAS_MAPDATA:
    print("  [INFO] No local map data — run download_mapdata.py for offline charts")

# =========================================================
# CONFIG
# =========================================================

base_folder = "/home/zopa/science/nmea_logs"
VESSEL_NAME = "ZOPA"
MMSI        = "227909880"

# =========================================================
# UTILS
# =========================================================

def safe_float(x):
    try:    return float(x)
    except: return np.nan

def safe_int(x):
    try:    return int(x)
    except: return np.nan


def nmea_to_decimal(coord, direction):
    try:
        if coord == "" or coord is None: return np.nan
        val     = float(coord)
        deg     = int(val // 100)
        minutes = val - deg * 100
        dec     = deg + minutes / 60.0
        if direction in ["S", "W"]: dec *= -1
        return dec
    except:
        return np.nan


def decimal_to_deg_min(decimal, is_lon=False):
    if decimal is None:
        return "---"
    try:
        if np.isnan(float(decimal)):
            return "---"
    except:
        return "---"
    direction = ("E" if decimal >= 0 else "W") if is_lon else ("N" if decimal >= 0 else "S")
    dw  = 3 if is_lon else 2
    a   = abs(float(decimal))
    deg = int(a)
    mn  = (a - deg) * 60.0
    return f"{deg:0{dw}d}\u00b0{mn:07.4f}'{direction}"


def elapsed_dhms(td):
    total_s = int(td.total_seconds())
    days  = total_s // 86400; rem = total_s % 86400
    hours = rem // 3600;      rem = rem % 3600
    mins  = rem // 60;        secs = rem % 60
    parts = []
    if days  > 0: parts.append(f"{days} d")
    if hours > 0: parts.append(f"{hours} h")
    if mins  > 0: parts.append(f"{mins} m")
    parts.append(f"{secs} s")
    return " ".join(parts)


def engine_hhmm(total_minutes):
    """Format engine time as  Xh YYmin  from total minutes (float)."""
    if total_minutes is None or np.isnan(float(total_minutes)):
        return "-"
    total_m = int(round(float(total_minutes)))
    h = total_m // 60
    m = total_m % 60
    if h == 0:
        return f"{m}min"
    return f"{h}h{m:02d}"


def circ_mean(angles_deg):
    r = np.radians(angles_deg.dropna())
    if len(r) == 0: return np.nan
    return float(np.degrees(np.arctan2(np.mean(np.sin(r)), np.mean(np.cos(r)))) % 360)


# =========================================================
# DISTANCE
# =========================================================

def haversine_nm(lat1, lon1, lat2, lon2):
    R    = 6371000
    phi1 = np.radians(lat1); phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1); dlon = np.radians(lon2 - lon1)
    a    = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlon/2)**2
    return (2 * R * np.arctan2(np.sqrt(a), np.sqrt(1-a))) / 1852.0


def haversine_vec(lat1, lon1, lat2, lon2):
    R    = 6371000
    phi1 = np.radians(lat1); phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1); dlon = np.radians(lon2 - lon1)
    a    = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlon/2)**2
    return (2 * R * np.arctan2(np.sqrt(a), np.sqrt(1-a))) / 1852.0


# =========================================================
# EVENT CLASSIFIER
# =========================================================

def classify_event(raw):
    # Strip optional timestamp prefix  "2024-06-01T12:34:56Z | ENGINE ON"
    if " | " in raw:
        raw = raw.split(" | ", 1)[1].strip()

    raw = raw.strip()
    if raw.startswith("ENGINE"):           return "ENGINE",        raw.replace("ENGINE","").strip()
    elif raw.startswith("MAIN ON"):        return "MAIN",          ("ON " + raw.replace("MAIN ON","").strip()).strip()
    elif raw == "MAIN OFF":                return "MAIN",          "OFF"
    elif raw == "JIB ON":                  return "JIB",           "ON"
    elif raw == "JIB OFF":                 return "JIB",           "OFF"
    elif raw == "STAYSAIL ON":             return "STAYSAIL",      "ON"
    elif raw == "STAYSAIL OFF":            return "STAYSAIL",      "OFF"
    elif raw == "STORMJIB ON":             return "STORMJIB",      "ON"
    elif raw == "STORMJIB OFF":            return "STORMJIB",      "OFF"
    elif raw == "SPINNAKER ON":            return "SPINNAKER",     "ON"
    elif raw == "SPINNAKER OFF":           return "SPINNAKER",     "OFF"
    elif raw == "DESSAL ON":               return "DESSAL",        "ON"
    elif raw == "DESSAL OFF":              return "DESSAL",        "OFF"
    elif raw.startswith("SEA"):            return "SEA",           raw.replace("SEA","").strip()
    elif raw == "HYPERNET ON":             return "HYPERNET",      "ON"
    elif raw == "HYPERNET OFF":            return "HYPERNET",      "OFF"
    elif raw == "NET ON":                  return "NET",           "ON"
    elif raw == "NET OFF":                 return "NET",           "OFF"
    elif raw == "INLINE ON":               return "INLINE",        "ON"
    elif raw == "INLINE OFF":              return "INLINE",        "OFF"
    elif raw == "CTD KEEL ON":             return "CTD_KEEL",      "ON"
    elif raw == "CTD KEEL OFF":            return "CTD_KEEL",      "OFF"
    elif raw == "CTD PROFILE ON":          return "CTD_PROFILE",   "ON"
    elif raw == "CTD PROFILE OFF":         return "CTD_PROFILE",   "OFF"
    elif raw == "CTD INTERCOMP ON":        return "CTD_INTERCOMP", "ON"
    elif raw == "CTD INTERCOMP OFF":       return "CTD_INTERCOMP", "OFF"
    elif "NM COMPLETED" in raw or "NM REMAINING" in raw: return "SKIP", raw
    elif raw == "DESTINATION REACHED":     return "ARRIVAL",       "DESTINATION REACHED"
    elif raw.startswith("ARRIVAL"):        return "ARRIVAL",       raw.replace("ARRIVAL :","").strip()
    elif raw.startswith("TOTAL TRAVELED"): return "ARRIVAL",       raw
    elif raw.startswith("NAV :"):          return "NAV_COMMENT",   raw[4:].strip()
    elif raw.startswith("SCI :"):          return "SCI_COMMENT",   raw[4:].strip()
    elif raw.startswith("COMMENT :"):      return "NAV_COMMENT",   raw.replace("COMMENT :","").strip()
    else:                                  return "OTHER",          raw


# Science event types set (used for routing to the right table)
SCIENCE_EVENT_TYPES = {"HYPERNET","NET","INLINE","CTD_KEEL","CTD_PROFILE","CTD_INTERCOMP","SCI_COMMENT"}
NAV_EVENT_TYPES     = {"ENGINE","MAIN","JIB","STAYSAIL","STORMJIB","SPINNAKER","DESSAL",
                       "SEA","ARRIVAL","OTHER","NAV_COMMENT"}


# =========================================================
# PARSER NMEA + EVENTS
# =========================================================

def parse_nmea(file_path):

    records = []
    current = {}
    events  = []

    last_datetime  = None
    first_datetime = None
    last_lat = np.nan; last_lon = np.nan

    meta = {
        "skipper":"", "crew":"", "fuel_pct":"",
        "departure":"", "destination":"",
        "destination_lat":"", "destination_lon":"",
        "initial_dist_nm":"", "engine_start":"",
        "dessal_start":"", "sea_start":"", "ctd_keel_start":"",
        "date_depart":"", "arrival":"",
        # boat check fields
        "bilges_dry":"", "portholes_closed":"", "seacocks_closed":"", "boat_stowed":"",
        # engine check fields
        "last_fill_l":"", "engine_hours_since_fill":"",
        "fuel_est1_l":"", "fuel_est1_pct":"",
        "gauge_pct":"", "fuel_est2_l":"", "fuel_est2_pct":"",
        "fuel_pre_filter":"", "seawater_filter":"", "engine_bilge":"",
        "oil_level":"", "coolant":"", "belt":"", "seacock":"", "priming_bulb":"",
        # end of trip control
        "arrival_port":"", "total_traveled":"",
        "hull_clean":"", "engine_bilge_end":"", "fore_bilge_end":"",
        "route_type":"",
    }

    # -----------------------------------------------------------------
    # FIX 1 — HEADER_KEYS mapping
    # Keys must match EXACTLY the text written before " : " in the log,
    # after stripping leading/trailing whitespace from the key part.
    # The parser below does raw_key.strip() before comparing, so extra
    # spaces in the log file (e.g. "ESTIMATE 1 (engine hours)   : ")
    # are handled automatically.
    # -----------------------------------------------------------------
    HEADER_KEYS = {
        "SKIPPER"                               : "skipper",
        "CREW"                                  : "crew",
        "FUEL"                                  : "fuel_pct",
        "DEPARTURE"                             : "departure",
        "DESTINATION LAT"                       : "destination_lat",
        "DESTINATION LON"                       : "destination_lon",
        "DESTINATION"                           : "destination",
        "INITIAL DISTANCE"                      : "initial_dist_nm",
        "ENGINE"                                : "engine_start",
        "DESSAL"                                : "dessal_start",
        "SEA"                                   : "sea_start",
        "CTD KEEL"                              : "ctd_keel_start",
        "DIRECT LEG"                            : "route_type",
        "ROUTE TYPE"                            : "route_type",
        # Boat check
        "BILGES DRY"                            : "bilges_dry",
        "PORTHOLES CLOSED"                      : "portholes_closed",
        "SEA COCKS CLOSED"                      : "seacocks_closed",
        "BOAT STOWED"                           : "boat_stowed",
        # Engine check — fuel fields
        "LAST FILL (L)"                         : "last_fill_l",
        "ENGINE HOURS SINCE LAST FILL (h)"      : "engine_hours_since_fill",
        "ESTIMATE 1 (engine hours)"             : "fuel_est1_l",
        "GAUGE READING"                         : "gauge_pct",
        "ESTIMATE 2 (gauge)"                    : "fuel_est2_l",
        # Engine check — mechanical
        "FUEL PRE-FILTER"                       : "fuel_pre_filter",
        "SEA WATER FILTER"                      : "seawater_filter",
        "SEAWATER FILTER"                       : "seawater_filter",
        # FIX: disambiguate ENGINE BILGE variants — most specific first
        "ENGINE BILGE (AFT)"                    : "engine_bilge",
        "ENGINE BILGE (FWD)"                    : "engine_bilge_fwd",
        # "ENGINE BILGE" alone → end-of-trip (matched last, after the (AFT)/(FWD) variants)
        "OIL"                                   : "oil_level",
        "OIL LEVEL"                             : "oil_level",
        "COOLANT"                               : "coolant",
        "BELT"                                  : "belt",
        "BELT TENSION"                          : "belt",
        "PRIMING BULB"                          : "priming_bulb",
        "SEA COCK + IGNITION"                   : "seacock",
        "SEACOCK + IGNITION"                    : "seacock",
        # End of trip control
        "ARRIVAL PORT"                          : "arrival_port",
        "TOTAL DISTANCE"                        : "total_traveled",
        "HULL CLEAN"                            : "hull_clean",
        "FORE BILGE"                            : "fore_bilge_end",
    }

    # Separate entry for plain "ENGINE BILGE" → end-of-trip (lowest priority)
    # We handle this after the loop so it doesn't shadow ENGINE BILGE (AFT).
    ENGINE_BILGE_PLAIN_KEY = "ENGINE BILGE"

    cur_engine="OFF"; cur_dessal="OFF"; cur_main="OFF"
    cur_jib="OFF"; cur_staysail="OFF"; cur_stormjib="OFF"; cur_spinnaker="OFF"
    cur_sea="0"; cur_hypernet="OFF"; cur_net="OFF"; cur_inline="OFF"
    cur_ctd_keel="OFF"; cur_ctd_profile="OFF"; cur_ctd_intercomp="OFF"

    # Engine time tracking — accumulate in MINUTES for precision
    engine_on_since       = None   # datetime of last ENGINE ON
    engine_minutes_cum    = 0.0    # cumulative completed sessions (minutes)

    open_func = gzip.open if file_path.endswith(".gz") else open

    with open_func(file_path, "rt") as f:
        all_lines = f.readlines()

    for line in all_lines:
        line = line.strip()

        # -----------------------------------------------------------------
        # FIX 2 — Header parsing: strip whitespace from the key side so
        # that "ESTIMATE 1 (engine hours)            : value" is matched
        # correctly regardless of padding.
        # -----------------------------------------------------------------
        if " : " in line and not line.startswith("$") and not line.startswith("# EVENT"):
            raw_key, _, raw_val = line.partition(" : ")
            normalized_key = raw_key.strip()
            matched = False
            for hk, mk in HEADER_KEYS.items():
                if normalized_key == hk:
                    meta[mk] = raw_val.strip()
                    matched = True
                    break
            # Plain "ENGINE BILGE" (end-of-trip) — only if no specific variant matched
            if not matched and normalized_key == ENGINE_BILGE_PLAIN_KEY:
                meta["engine_bilge_end"] = raw_val.strip()

        # Events
        if line.startswith("# EVENT"):
            import re
            m_ts = re.match(r"# EVENT \[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\] : (.*)", line)
            if m_ts:
                try:
                    ev_timestamp = pd.to_datetime(m_ts.group(1))
                except:
                    ev_timestamp = last_datetime
                raw = m_ts.group(2).strip()
            elif line.startswith("# EVENT :"):
                ev_timestamp = last_datetime
                raw = line.replace("# EVENT :", "").strip()
            else:
                continue

            etype, edetail = classify_event(raw)

            if etype == "SKIP":
                continue

            if etype == "ENGINE":
                if "ON" in edetail:
                    cur_engine = "ON"
                    engine_on_since = ev_timestamp
                else:
                    cur_engine = "OFF"
                    if engine_on_since is not None and ev_timestamp is not None:
                        try:
                            delta_min = (ev_timestamp - engine_on_since).total_seconds() / 60.0
                            if 0 < delta_min < 1440:
                                engine_minutes_cum += delta_min
                        except:
                            pass
                    engine_on_since = None

            elif etype == "DESSAL":    cur_dessal    = edetail
            elif etype == "MAIN":      cur_main      = "ON" if "ON" in edetail else "OFF"
            elif etype == "JIB":       cur_jib       = edetail
            elif etype == "STAYSAIL":  cur_staysail  = edetail
            elif etype == "STORMJIB":  cur_stormjib  = edetail
            elif etype == "SPINNAKER": cur_spinnaker = edetail
            elif etype == "SEA":       cur_sea       = edetail
            elif etype == "HYPERNET":  cur_hypernet  = edetail
            elif etype == "NET":       cur_net       = edetail
            elif etype == "INLINE":    cur_inline    = edetail
            elif etype == "CTD_KEEL":       cur_ctd_keel      = edetail
            elif etype == "CTD_PROFILE":    cur_ctd_profile   = edetail
            elif etype == "CTD_INTERCOMP":  cur_ctd_intercomp = edetail
            elif etype == "ARRIVAL":
                rd = edetail.strip()
                is_port = (rd != "DESTINATION REACHED"
                           and not rd.startswith("TOTAL TRAVELED") and rd != "")
                ts_str = last_datetime.strftime("%d/%m/%Y %H:%M") if last_datetime else ""
                if is_port:
                    meta["arrival"] = f"{rd}  ({ts_str})" if ts_str else rd
                elif meta["arrival"] == "":
                    meta["arrival"] = f"DESTINATION REACHED  ({ts_str})" if ts_str else rd

            sails = []
            if cur_main      == "ON": sails.append("MAIN")
            if cur_jib       == "ON": sails.append("JIB")
            if cur_staysail  == "ON": sails.append("STAYSAIL")
            if cur_stormjib  == "ON": sails.append("STORM JIB")
            if cur_spinnaker == "ON": sails.append("SPINNAKER")

            engine_minutes_now = engine_minutes_cum
            if cur_engine == "ON" and engine_on_since is not None and ev_timestamp is not None:
                try:
                    delta_min = (ev_timestamp - engine_on_since).total_seconds() / 60.0
                    if 0 < delta_min < 1440:
                        engine_minutes_now += delta_min
                except:
                    pass

            is_retro = (m_ts is not None)

            events.append({
                "timestamp":ev_timestamp, "lat":last_lat, "lon":last_lon,
                "is_retrodate": is_retro,
                "event_raw":raw, "event_type":etype, "event_detail":edetail,
                "engine":cur_engine, "sails":"+".join(sails) if sails else "-",
                "main":cur_main, "jib":cur_jib, "staysail":cur_staysail,
                "stormjib":cur_stormjib, "spinnaker":cur_spinnaker,
                "dessal":cur_dessal, "sea":cur_sea,
                "hypernet":cur_hypernet, "net":cur_net, "inline":cur_inline,
                "ctd_keel":cur_ctd_keel, "ctd_profile":cur_ctd_profile,
                "ctd_intercomp":cur_ctd_intercomp,
                "engine_minutes": engine_minutes_now,
            })
            continue

        if not line.startswith("$"):
            continue

        parts = line.split(",")

        if parts and "*" in parts[-1]:
            parts[-1] = parts[-1].split("*")[0]

        msg = parts[0]

        if msg == "$GPRMC":
            if current:
                records.append(current.copy())
                current = {}
            if len(parts) < 10:
                continue
            dt  = pd.to_datetime(parts[9]+parts[1], format="%d%m%y%H%M%S", errors="coerce")
            lat = nmea_to_decimal(parts[3], parts[4])
            lon = nmea_to_decimal(parts[5], parts[6])
            sog = safe_float(parts[7])
            current.update({"datetime":dt, "lat_raw":lat, "lon_raw":lon,
                             "SOG_RMC":sog, "COG_RMC":safe_float(parts[8])})
            if not pd.isnull(dt):
                last_datetime = dt
                if first_datetime is None: first_datetime = dt
                if meta["date_depart"] == "": meta["date_depart"] = dt.strftime("%d/%m/%Y")
            if not np.isnan(lat): last_lat = lat
            if not np.isnan(lon): last_lon = lon

        elif msg == "$GPGGA":
            if len(parts) > 9:
                current["gps_fix_quality"] = safe_int(parts[6])
                current["satellites_used"] = safe_int(parts[7])
                current["hdop"]            = safe_float(parts[8])
                current["altitude"]        = safe_float(parts[9])

        elif msg == "$GPVTG":
            if len(parts) > 5:
                current["COG_true"] = safe_float(parts[1])
                current["SOG_VTG"]  = safe_float(parts[5])

        elif msg == "$IIHDG":
            if len(parts) > 1:
                current["HDG"] = safe_float(parts[1])

        elif msg == "$WIMWV":
            if len(parts) > 4 and parts[2] == "R":
                current["AWA"] = safe_float(parts[1])
                current["AWS"] = safe_float(parts[3])

        elif msg == "$WIMWD":
            if len(parts) > 5:
                current["TWD"] = safe_float(parts[1])
                current["TWS"] = safe_float(parts[5])

        elif msg == "$SDDPT":
            if len(parts) > 1:
                current["depth_m"] = safe_float(parts[1])

        elif msg == "$SDDBT":
            if len(parts) > 3:
                current["depth_ft"]    = safe_float(parts[1])
                current["depth_m_dbt"] = safe_float(parts[3])

        elif msg == "$IIXDR":
            for i in range(1, len(parts)-1, 4):
                try:
                    val  = safe_float(parts[i+1]); name = parts[i+3]
                    if   name == "AIRTEMP": current["air_temp"]    = val
                    elif name == "HEEL":    current["heel"]         = val
                    elif name == "TRIM":    current["trim"]         = val
                    elif name == "BARO":    current["pressure"]     = val
                    elif name == "RUDDER":  current["rudder_angle"] = val
                except: pass

        elif msg == "$SDVLW":
            if len(parts) > 4:
                current["log_total_nm"] = safe_float(parts[2])
                current["log_trip_nm"]  = safe_float(parts[4])

    if current: records.append(current)

    df = pd.DataFrame(records)
    if not df.empty:
        awa  = np.radians(df.get("AWA",    pd.Series(np.nan, index=df.index)).values)
        aws  = df.get("AWS",    pd.Series(np.nan, index=df.index)).values
        sog  = df.get("SOG_RMC",pd.Series(np.nan, index=df.index)).values
        hdg  = np.radians(df.get("HDG",    pd.Series(np.nan, index=df.index)).values)
        twx  = aws * np.cos(awa) + sog * np.cos(hdg)
        twy  = aws * np.sin(awa) + sog * np.sin(hdg)
        twa  = np.degrees(np.arctan2(twy, twx)) % 360
        bad  = (np.isnan(aws) | np.isnan(sog) |
                np.isnan(np.degrees(awa)) | np.isnan(np.degrees(hdg)))
        twa[bad] = np.nan
        df["TWA"] = twa

        a = twa % 360
        a = np.where(a > 180, 360 - a, a)
        df["allure"] = np.select(
            [np.isnan(twa), a < 55, a < 70, a < 110, a < 150],
            ["-", "close-hauled", "close reach", "beam reach", "broad reach"],
            default="run"
        )

    df_events = pd.DataFrame(events)

    if records and not df_events.empty and "is_retrodate" in df_events.columns:
        _recs_with_pos = [(r["datetime"], r["lat_raw"], r["lon_raw"])
                          for r in records
                          if "datetime" in r and "lat_raw" in r and "lon_raw" in r
                          and r["datetime"] is not None
                          and not (isinstance(r.get("lat_raw"), float) and np.isnan(r["lat_raw"]))]
        if _recs_with_pos:
            _rts  = np.array([pd.Timestamp(t).value for t, _, __ in _recs_with_pos])
            _rlat = np.array([la for _, la, __ in _recs_with_pos], dtype=float)
            _rlon = np.array([lo for _, __, lo in _recs_with_pos], dtype=float)

            retro_mask = df_events["is_retrodate"].fillna(False)
            for idx in df_events.index[retro_mask]:
                ts = df_events.at[idx, "timestamp"]
                if ts is None:
                    continue
                try:
                    t_ns = pd.Timestamp(ts).value
                    pos  = np.searchsorted(_rts, t_ns, side="left")
                    if pos == 0:
                        best = 0
                    elif pos >= len(_rts):
                        best = len(_rts) - 1
                    else:
                        best = pos if abs(_rts[pos]-t_ns) <= abs(_rts[pos-1]-t_ns) else pos-1
                    df_events.at[idx, "lat"] = float(_rlat[best])
                    df_events.at[idx, "lon"] = float(_rlon[best])
                except Exception:
                    pass

    meta["_first_datetime"] = first_datetime
    return df, df_events, meta


# =========================================================
# CSV 0.1 NM RESAMPLE
# =========================================================

def resample_every_0_1nm(df, df_events=None):
    SCIENCE_COLS = ["hypernet","net","inline","ctd_keel","ctd_profile","ctd_intercomp"]
    science_events = []

    if df_events is not None and not df_events.empty and "timestamp" in df_events.columns:
        ev  = df_events.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        cur = {c:"OFF" for c in SCIENCE_COLS}
        mapping = {"HYPERNET":"hypernet","NET":"net","INLINE":"inline",
                   "CTD_KEEL":"ctd_keel","CTD_PROFILE":"ctd_profile","CTD_INTERCOMP":"ctd_intercomp"}
        for _, row in ev.iterrows():
            et = row.get("event_type",""); ed = row.get("event_detail","")
            ts = pd.Timestamp(row["timestamp"])
            if et in mapping:
                cur[mapping[et]] = ed
                science_events.append((ts, dict(cur)))

    df = df.copy()
    lats = df["lat_raw"].values; lons = df["lon_raw"].values
    valid = ~(np.isnan(lats) | np.isnan(lons))
    dists = np.zeros(len(df))
    if valid.any():
        d = np.zeros(len(df))
        d[1:] = np.where(
            valid[:-1] & valid[1:],
            haversine_vec(lats[:-1], lons[:-1], lats[1:], lons[1:]),
            0.0
        )
        d = np.where(d > 1, 0.0, d)
        dists = np.cumsum(d)

    df["dist_nm"]  = dists
    df["bin_01nm"] = (df["dist_nm"] / 0.1).astype(int)
    out = df.groupby("bin_01nm").first().reset_index()
    out = out.rename(columns={"lon_raw":"longitude","lat_raw":"latitude","datetime":"timestamp",
                               "SOG_RMC":"sog","TWS":"tws","TWD":"twd","HDG":"heading"})
    out = out[["longitude","latitude","timestamp","sog","tws","twd","heading","allure"]]
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    for col in SCIENCE_COLS: out[col] = "OFF"

    if science_events:
        ev_ts_ns = np.array([t.value for t, _ in science_events])
        ev_states = [s for _, s in science_events]
        ts_ns = out["timestamp"].values.astype("int64")
        for idx, t in enumerate(ts_ns):
            pos = np.searchsorted(ev_ts_ns, t, side="right") - 1
            if pos >= 0:
                for col in SCIENCE_COLS:
                    out.at[idx, col] = ev_states[pos][col]

    out["timestamp"] = out["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


# =========================================================
# TRACK CHART
# =========================================================

def _clip(arr, lon_min, lon_max, lat_min, lat_max, margin=1.5):
    if len(arr) == 0: return arr
    lo = arr[:,0]; la = arr[:,1]
    mask = (
        (lo >= lon_min-margin) & (lo <= lon_max+margin) &
        (la >= lat_min-margin) & (la <= lat_max+margin)
    ) | (np.isnan(lo) | np.isnan(la))
    return arr[mask]


def build_track_map(df, meta):
    dft = df.dropna(subset=["lat_raw","lon_raw"]).copy()
    if dft.empty: return None

    lats = dft["lat_raw"].values
    lons = dft["lon_raw"].values

    if len(lats) > 1000:
        step      = len(lats) // 1000
        idx       = np.concatenate([np.arange(0, len(lats), step), [len(lats)-1]])
        lats_plot = lats[idx]; lons_plot = lons[idx]
    else:
        lats_plot = lats; lons_plot = lons

    dep_label = meta.get("departure","")   or "Departure"
    arr_label = meta.get("destination","") or "Arrival"
    dist_str  = meta.get("initial_dist_nm","")

    span_lat = max(lats.max() - lats.min(), 0.1)
    span_lon = max(lons.max() - lons.min(), 0.1)
    pad_lat  = span_lat * 0.15
    pad_lon  = span_lon * 0.15

    lon_min = lons.min() - pad_lon;  lon_max = lons.max() + pad_lon
    lat_min = lats.min() - pad_lat;  lat_max = lats.max() + pad_lat

    FIG_W_IN = 26.0 / 2.54
    FIG_H_IN = 17.0 / 2.54
    DPI      = 220

    fig, ax = plt.subplots(figsize=(FIG_W_IN, FIG_H_IN), dpi=DPI, facecolor="white")

    lat_c  = (lat_min + lat_max) / 2.0
    merc   = 1.0 / math.cos(math.radians(lat_c))

    span_data_lon = lon_max - lon_min
    span_data_lat = lat_max - lat_min

    fig_ratio  = FIG_W_IN / FIG_H_IN
    data_ratio = span_data_lon / (span_data_lat * merc)

    if data_ratio > fig_ratio:
        needed_lat = span_data_lon / (fig_ratio * merc)
        extra = (needed_lat - span_data_lat) / 2.0
        lat_min -= extra; lat_max += extra
    else:
        needed_lon = span_data_lat * merc * fig_ratio
        extra = (needed_lon - span_data_lon) / 2.0
        lon_min -= extra; lon_max += extra

    ax.set_facecolor("#d0e8f5")

    bathy_layers = [
        ("bathy_5000", "#6a9fb5", 1.0),
        ("bathy_4000", "#7aaec4", 0.9),
        ("bathy_3000", "#8abdd3", 0.85),
        ("bathy_2000", "#9acce0", 0.8),
        ("bathy_1000", "#aad8e8", 0.75),
        ("bathy_500",  "#b8e0ee", 0.7),
        ("bathy_200",  "#c8e8f4", 0.65),
    ]
    for name, color, alpha in bathy_layers:
        if name in _MAPDATA:
            arr = _clip(_MAPDATA[name], lon_min, lon_max, lat_min, lat_max)
            if len(arr):
                ax.fill(arr[:,0], arr[:,1], color=color, alpha=alpha, linewidth=0, zorder=1)

    if "land" in _MAPDATA:
        arr = _clip(_MAPDATA["land"], lon_min, lon_max, lat_min, lat_max)
        if len(arr):
            ax.fill(arr[:,0], arr[:,1], color="#e8e0cc", linewidth=0, zorder=2)

    if "lakes" in _MAPDATA:
        arr = _clip(_MAPDATA["lakes"], lon_min, lon_max, lat_min, lat_max)
        if len(arr):
            ax.fill(arr[:,0], arr[:,1], color="#d0e8f5", linewidth=0, zorder=3)

    if "rivers" in _MAPDATA:
        arr = _clip(_MAPDATA["rivers"], lon_min, lon_max, lat_min, lat_max)
        if len(arr):
            ax.plot(arr[:,0], arr[:,1], color="#7ab4d4", linewidth=0.4,
                    solid_capstyle="round", zorder=4)

    if "countries" in _MAPDATA:
        arr = _clip(_MAPDATA["countries"], lon_min, lon_max, lat_min, lat_max)
        if len(arr):
            ax.plot(arr[:,0], arr[:,1], color="#999999", linewidth=0.35,
                    linestyle=(0, (4, 3)), zorder=5)

    if "coastline" in _MAPDATA:
        arr = _clip(_MAPDATA["coastline"], lon_min, lon_max, lat_min, lat_max)
        if len(arr):
            ax.plot(arr[:,0], arr[:,1], color="#aaaaaa", linewidth=1.4,
                    solid_capstyle="round", zorder=6)
            ax.plot(arr[:,0], arr[:,1], color="#333333", linewidth=0.8,
                    solid_capstyle="round", zorder=7)

    ax.plot(lons_plot, lats_plot, color="white", linewidth=3.5,
            solid_capstyle="round", zorder=8)
    ax.plot(lons_plot, lats_plot, color="#c0392b", linewidth=1.8,
            solid_capstyle="round", zorder=9)

    ax.plot(lons[0], lats[0], "o", color="white", markersize=10, zorder=10)
    ax.plot(lons[0], lats[0], "o", color="#27ae60", markersize=7, zorder=11)
    ax.plot(lons[-1], lats[-1], "s", color="white", markersize=10, zorder=10)
    ax.plot(lons[-1], lats[-1], "s", color="#c0392b", markersize=7, zorder=11)

    ax.annotate(f" {dep_label}", (lons[0], lats[0]),
        fontsize=6.5, color="#1a7a40", fontweight="bold",
        va="bottom", xytext=(4, 4), textcoords="offset points", zorder=12,
        bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.7))
    ax.annotate(f" {arr_label}", (lons[-1], lats[-1]),
        fontsize=6.5, color="#922b21", fontweight="bold",
        va="bottom", xytext=(4, 4), textcoords="offset points", zorder=12,
        bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.7))

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect("auto")
    ax.grid(True, linewidth=0.25, color="#666666", alpha=0.3, linestyle="--", zorder=0)

    from matplotlib.ticker import FuncFormatter
    def fmt_lon(x, _):
        d = int(abs(x)); m = (abs(x) - d) * 60
        return f"{d}°{m:04.1f}'{'E' if x>=0 else 'W'}"
    def fmt_lat(y, _):
        d = int(abs(y)); m = (abs(y) - d) * 60
        return f"{d}°{m:04.1f}'{'N' if y>=0 else 'S'}"

    ax.xaxis.set_major_formatter(FuncFormatter(fmt_lon))
    ax.yaxis.set_major_formatter(FuncFormatter(fmt_lat))
    ax.tick_params(axis="both", labelsize=5.5, length=3, width=0.5)

    for spine in ax.spines.values():
        spine.set_linewidth(0.8); spine.set_edgecolor("#333333")

    title = f"Track — {VESSEL_NAME}  |  {dep_label} → {arr_label}"
    if dist_str:
        title += f"  |  {dist_str} nm"
    ax.set_title(title, fontsize=8, fontweight="bold", color="#1a3a5c", pad=6)

    legend_handles = [
        mpatches.Patch(facecolor="#27ae60", edgecolor="#1a5c2a", linewidth=0.5, label="Departure"),
        mpatches.Patch(facecolor="#c0392b", edgecolor="#7b1a10", linewidth=0.5, label="Arrival / track"),
    ]
    bathy_present = [b for b, _, __ in bathy_layers if b in _MAPDATA]
    if bathy_present:
        legend_handles += [
            mpatches.Patch(facecolor="#6a9fb5", label="> 5000 m"),
            mpatches.Patch(facecolor="#9acce0", label="> 2000 m"),
            mpatches.Patch(facecolor="#c8e8f4", label="> 200 m"),
        ]
    ax.legend(handles=legend_handles, loc="lower right", fontsize=5.5,
              framealpha=0.9, edgecolor="#cccccc", fancybox=True, borderpad=0.6)

    plt.tight_layout(pad=0.3)

    DPI_OUT = 300
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI_OUT, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)

    from PIL import Image as PILImage
    pil_img  = PILImage.open(buf)
    px_w, px_h = pil_img.size
    buf.seek(0)

    pt_per_px = 72.0 / DPI_OUT
    rl_w = px_w * pt_per_px
    rl_h = px_h * pt_per_px

    max_w = 26.5 * cm
    max_h = 17.0 * cm

    if rl_w > max_w or rl_h > max_h:
        scale = min(max_w / rl_w, max_h / rl_h)
        rl_w *= scale; rl_h *= scale

    buf.seek(0)
    return Image(buf, width=rl_w, height=rl_h)


# =========================================================
# EVENT COLOURS
# =========================================================

COLOR_NAV        = colors.HexColor("#cce5ff")
COLOR_SCIENCE    = colors.HexColor("#d4edda")
COLOR_ENGINE     = colors.HexColor("#f8d7da")
COLOR_NAV_CMT    = colors.white
COLOR_SCI_CMT    = colors.white
COLOR_OTHER      = colors.white
COLOR_EVENT_TEXT = colors.black

def event_color(etype):
    if etype == "ENGINE":                  return COLOR_ENGINE
    if etype in SCIENCE_EVENT_TYPES:       return COLOR_SCIENCE
    if etype == "NAV_COMMENT":             return COLOR_NAV_CMT
    if etype == "SCI_COMMENT":             return COLOR_SCI_CMT
    return COLOR_NAV


# =========================================================
# PDF HELPERS
# =========================================================

HEADER_COLOR    = colors.HexColor("#1a3a5c")
ALT_COLOR       = colors.HexColor("#eaf0f8")
COLOR_HIGHLIGHT = colors.HexColor("#fff9c4")
COLOR_ENG_LAST  = colors.HexColor("#f8d7da")


def base_table_style(n_rows):
    style = [
        ("BACKGROUND",    (0,0), (-1,0),  HEADER_COLOR),
        ("TEXTCOLOR",     (0,0), (-1,0),  colors.white),
        ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
        ("ALIGN",         (0,0), (-1,0),  "CENTER"),
        ("FONTNAME",      (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,0), (-1,-1), 7),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#bbbbbb")),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]
    for i in range(1, n_rows):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0,i), (-1,i), ALT_COLOR))
    return style


def fmt(val, decimals=1):
    try:
        v = float(val)
        if np.isnan(v): return "-"
        return f"{v:.{decimals}f}"
    except:
        return str(val) if val is not None else "-"


def fmt_ts(ts, show_date=False):
    try:
        dt = pd.to_datetime(ts)
        return dt.strftime("%d/%m %H:%M") if show_date else dt.strftime("%H:%M")
    except:
        return "-"


def build_hourly_slots(first_dt, last_dt):
    if first_dt is None or last_dt is None: return []
    slots = []; t = pd.Timestamp(first_dt); end = pd.Timestamp(last_dt)
    while t <= end:
        slots.append(t); t += pd.Timedelta(hours=1)
    last_ts = pd.Timestamp(last_dt)
    if slots and slots[-1] < last_ts:
        slots.append(last_ts)
    return slots


def science_str_from_row(r):
    sci = []
    if str(r.get("hypernet","-"))      == "ON": sci.append("HYPERNET")
    if str(r.get("net","-"))           == "ON": sci.append("NET")
    if str(r.get("inline","-"))        == "ON": sci.append("INLINE")
    if str(r.get("ctd_keel","-"))      == "ON": sci.append("CTD KEEL")
    if str(r.get("ctd_profile","-"))   == "ON": sci.append("CTD PROF")
    if str(r.get("ctd_intercomp","-")) == "ON": sci.append("CTD INTER")
    return "+".join(sci) if sci else "-"


# =========================================================
# PRE-COMPUTE SLOT STATS
# =========================================================

def precompute_slot_stats(df2, hourly_slots):
    if df2.empty or not hourly_slots:
        return {}

    ts_ns     = df2["datetime"].values.astype("int64")
    slots_ns  = np.array([pd.Timestamp(s).value for s in hourly_slots])
    half_min  = int(30e9)
    one_hour  = int(3600e9)

    sog  = df2["SOG_RMC"].values if "SOG_RMC" in df2.columns else np.full(len(df2), np.nan)
    tws  = df2["TWS"].values     if "TWS"     in df2.columns else np.full(len(df2), np.nan)
    twd  = df2["TWD"].values     if "TWD"     in df2.columns else np.full(len(df2), np.nan)
    hdg  = df2["HDG"].values     if "HDG"     in df2.columns else np.full(len(df2), np.nan)
    lats = df2["lat_raw"].values
    lons = df2["lon_raw"].values

    twd_rad  = np.radians(twd)
    sin_twd  = np.sin(twd_rad)
    cos_twd  = np.cos(twd_rad)

    result = {}

    for slot, slot_ns in zip(hourly_slots, slots_ns):
        i_lo = np.searchsorted(ts_ns, slot_ns - half_min, side="left")
        i_hi = np.searchsorted(ts_ns, slot_ns + half_min, side="right")

        if i_lo < i_hi:
            sl   = slice(i_lo, i_hi)
            sog_m = np.nanmean(sog[sl])
            tws_m = np.nanmean(tws[sl])
            hdg_m = np.nanmean(hdg[sl])
            sin_m = np.nanmean(sin_twd[sl]); cos_m = np.nanmean(cos_twd[sl])
            twd_m = float(np.degrees(np.arctan2(sin_m, cos_m)) % 360) if not (np.isnan(sin_m) or np.isnan(cos_m)) else np.nan
        else:
            sog_m = tws_m = hdg_m = twd_m = np.nan

        i_pos = np.searchsorted(ts_ns, slot_ns, side="left")
        if i_pos >= len(df2): i_pos = len(df2) - 1
        lat_s = lats[i_pos]; lon_s = lons[i_pos]

        i_h0 = np.searchsorted(ts_ns, slot_ns - one_hour, side="left")
        i_h1 = np.searchsorted(ts_ns, slot_ns, side="right")

        if i_h0 < i_h1:
            sh      = slice(i_h0, i_h1)
            tws_moy = np.nanmean(tws[sh])
            sog_moy = np.nanmean(sog[sh])
            sog_mx  = np.nanmax(sog[sh])
            tws_mx  = np.nanmax(tws[sh])
        else:
            tws_moy = sog_moy = sog_mx = tws_mx = np.nan

        result[slot] = {
            "lat":     lat_s,  "lon":     lon_s,
            "sog_m":   sog_m,  "tws_m":   tws_m,
            "twd_m":   twd_m,  "hdg_m":   hdg_m,
            "tws_moy": tws_moy,"sog_moy": sog_moy,
            "sog_max": sog_mx, "tws_max": tws_mx,
        }

    return result


def precompute_event_instants(df2, ev_timestamps):
    if df2.empty or not ev_timestamps:
        n = len(ev_timestamps)
        return pd.DataFrame({
            "sog_i": [np.nan]*n, "tws_i": [np.nan]*n,
            "twd_i": [np.nan]*n, "hdg_i": [np.nan]*n,
        })

    ts_ns = df2["datetime"].values.astype("int64")
    sog   = df2["SOG_RMC"].values if "SOG_RMC" in df2.columns else np.full(len(df2), np.nan)
    tws   = df2["TWS"].values     if "TWS"     in df2.columns else np.full(len(df2), np.nan)
    twd   = df2["TWD"].values     if "TWD"     in df2.columns else np.full(len(df2), np.nan)
    hdg   = df2["HDG"].values     if "HDG"     in df2.columns else np.full(len(df2), np.nan)

    sog_i = []; tws_i = []; twd_i = []; hdg_i = []

    for ts in ev_timestamps:
        if ts is None:
            sog_i.append(np.nan); tws_i.append(np.nan)
            twd_i.append(np.nan); hdg_i.append(np.nan)
            continue
        t_ns = pd.Timestamp(ts).value
        idx  = np.searchsorted(ts_ns, t_ns, side="left")
        if idx == 0:
            best = 0
        elif idx >= len(ts_ns):
            best = len(ts_ns) - 1
        else:
            best = idx if abs(ts_ns[idx]-t_ns) <= abs(ts_ns[idx-1]-t_ns) else idx-1
        sog_i.append(sog[best]); tws_i.append(tws[best])
        twd_i.append(twd[best]); hdg_i.append(hdg[best])

    return pd.DataFrame({"sog_i":sog_i, "tws_i":tws_i, "twd_i":twd_i, "hdg_i":hdg_i})


# =========================================================
# ELAPSED TIME
# =========================================================

def compute_navigation_elapsed(df2):
    if df2.empty or "SOG_RMC" not in df2.columns:
        return "-", "-"
    try:
        sailing = df2[df2["SOG_RMC"] > 1.0].dropna(subset=["datetime"])
        if sailing.empty:
            return "-", "-"
        t_start = sailing["datetime"].min()
        t_end   = df2["datetime"].max()
        if pd.isnull(t_start) or pd.isnull(t_end) or t_end <= t_start:
            return "-", "-"
        elapsed = t_end - t_start
        elapsed_str = elapsed_dhms(elapsed)
        avg_sog = sailing["SOG_RMC"].mean()
        avg_sog_str = f"{avg_sog:.2f} kn" if not np.isnan(avg_sog) else "-"
        return elapsed_str, avg_sog_str
    except Exception as e:
        print(f"  [WARN] elapsed time: {e}")
        return "-", "-"


# =========================================================
# PDF LOGBOOK
# =========================================================

def generate_pdf(df, df_events, meta, out_pdf, in_progress=False):

    doc = SimpleDocTemplate(
        out_pdf,
        pagesize=landscape(A4),
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm,  bottomMargin=1.5*cm,
    )
    styles = getSampleStyleSheet()

    style_section = ParagraphStyle("Section", parent=styles["Heading2"],
        fontSize=10, textColor=HEADER_COLOR, spaceAfter=4, spaceBefore=10)
    style_cell = ParagraphStyle("Cell", parent=styles["Normal"],
        fontSize=7, leading=9, wordWrap="LTR")

    content = []

    # --- TITLE ---
    title = "NAVIGATION LOGBOOK" + ("  —  LOG IN PROGRESS" if in_progress else "")
    content.append(Paragraph(title, styles["Title"]))
    content.append(Spacer(1, 6))
    content.append(Paragraph(
        f"Vessel: <b>{VESSEL_NAME}</b> &nbsp;|&nbsp; MMSI: <b>{MMSI}</b>",
        styles["Normal"]))
    content.append(Spacer(1, 10))

    # -------------------------------------------------------
    # VOYAGE SUMMARY CARD
    # -------------------------------------------------------
    arrival_str = meta.get("arrival","") or ("IN PROGRESS" if in_progress else "—")
    route_type_str = meta.get("route_type","") or "—"

    fiche_data = [
        ["DATE",        meta.get("date_depart","—"),  "SKIPPER",      meta.get("skipper","—")],
        ["CREW",        meta.get("crew","—"),          "",             ""],
        ["DEPARTURE",   meta.get("departure","—"),     "LAT. DEST.",   meta.get("destination_lat","—")],
        ["ARRIVAL",     arrival_str,                   "LON. DEST.",   meta.get("destination_lon","—")],
        ["DESTINATION", meta.get("destination","—"),   "INIT. DIST.",
         (meta.get("initial_dist_nm","—") or "—").replace("nm","").strip() + " nm"],
        ["DIRECT LEG",  route_type_str,                "",             ""],
    ]

    style_fiche_lbl = ParagraphStyle("FicheLbl", parent=styles["Normal"],
        fontSize=8, textColor=colors.white, fontName="Helvetica-Bold")
    style_fiche_val = ParagraphStyle("FicheVal", parent=styles["Normal"], fontSize=8)

    DEP_ROW = 2; ARR_ROW = 3

    dep_bg = colors.HexColor("#d5f5e3"); arr_bg = colors.HexColor("#f5c6cb")

    fiche_display = [
        [Paragraph(f"<b>{r[0]}</b>", style_fiche_lbl) if r[0] else "",
         Paragraph(str(r[1]), style_fiche_val),
         Paragraph(f"<b>{r[2]}</b>", style_fiche_lbl) if r[2] else "",
         Paragraph(str(r[3]), style_fiche_val)]
        for r in fiche_data
    ]

    fiche_style = TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), colors.white),
        ("BACKGROUND",    (0,0), (0,-1),  HEADER_COLOR),
        ("BACKGROUND",    (2,0), (2,-1),  HEADER_COLOR),
        ("BACKGROUND",    (1,DEP_ROW), (1,DEP_ROW), dep_bg),
        ("BACKGROUND",    (1,ARR_ROW), (1,ARR_ROW), arr_bg),
        ("FONTNAME",      (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,0), (-1,-1), 8),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#c0cfe0")),
        ("LEFTPADDING",   (0,0), (-1,-1), 6), ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("TOPPADDING",    (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ])

    COL_WIDTHS_FICHE = [3.0*cm, 8.5*cm, 3.0*cm, 8.5*cm]
    tf = Table(fiche_display, colWidths=COL_WIDTHS_FICHE)
    tf.setStyle(fiche_style)
    content.append(tf)
    content.append(Spacer(1, 8))

    # -------------------------------------------------------
    # CHECKUP
    # -------------------------------------------------------
    content.append(Paragraph("CHECKUP", style_section))

    def _ok(val):
        return str(val) if val else "—"

    boat_checks = [
        ("Bilges dry",       _ok(meta.get("bilges_dry",""))),
        ("Portholes closed", _ok(meta.get("portholes_closed",""))),
        ("Sea cocks closed", _ok(meta.get("seacocks_closed",""))),
        ("Boat stowed",      _ok(meta.get("boat_stowed",""))),
    ]

    engine_checks = [
        ("Last fill (L)",                    _ok(meta.get("last_fill_l",""))),
        ("Engine hours since last fill (h)", _ok(meta.get("engine_hours_since_fill",""))),
        ("Estimate 1 (engine hrs) L rem.",   _ok(meta.get("fuel_est1_l",""))),
        ("Fuel gauge (%)",                   _ok(meta.get("gauge_pct",""))),
        ("Estimate 2 (gauge) L remaining",   _ok(meta.get("fuel_est2_l",""))),
        ("Fuel pre-filter",                  _ok(meta.get("fuel_pre_filter",""))),
        ("Sea water filter",                 _ok(meta.get("seawater_filter",""))),
        ("Engine bilge (aft)",               _ok(meta.get("engine_bilge",""))),
        ("Coolant level",                    _ok(meta.get("coolant",""))),
        ("Belt tension",                     _ok(meta.get("belt",""))),
        ("Priming bulb",                     _ok(meta.get("priming_bulb",""))),
        ("Sea cock + ignition",              _ok(meta.get("seacock",""))),
    ]

    eot_checks = [
        ("Arrival port",          _ok(meta.get("arrival_port",""))),
        ("Total distance (nm)",   _ok(meta.get("total_traveled",""))),
        ("Hull clean",            _ok(meta.get("hull_clean",""))),
        ("Engine bilge dry",      _ok(meta.get("engine_bilge_end",""))),
        ("Fore bilge dry",        _ok(meta.get("fore_bilge_end",""))),
    ]

    style_chk_lbl = ParagraphStyle("ChkLbl", parent=styles["Normal"],
        fontSize=7, textColor=colors.white, fontName="Helvetica-Bold")
    style_chk_val = ParagraphStyle("ChkVal", parent=styles["Normal"], fontSize=7)
    style_chk_hdr = ParagraphStyle("ChkHdr", parent=styles["Normal"],
        fontSize=8, textColor=colors.white, fontName="Helvetica-Bold", alignment=1)

    CW_LBL = 3.5*cm
    CW_VAL = 8.0*cm
    COL_WIDTHS_CHK = [CW_LBL, CW_VAL, CW_LBL, CW_VAL]

    left_seq = [("HDR", "BOAT CHECK")]
    for lbl, val in boat_checks:
        left_seq.append(("ROW", lbl, val))
    left_seq.append(("HDR", "ENGINE CHECK"))
    for lbl, val in engine_checks:
        left_seq.append(("ROW", lbl, val))

    right_seq = [("HDR", "END OF TRIP CONTROL")]
    for lbl, val in eot_checks:
        right_seq.append(("ROW", lbl, val))

    n_rows = max(len(left_seq), len(right_seq))
    while len(left_seq)  < n_rows: left_seq.append(("BLANK",))
    while len(right_seq) < n_rows: right_seq.append(("BLANK",))

    def _cell_lbl(text):
        return Paragraph(f"<b>{text}</b>", style_chk_lbl) if text else ""

    def _cell_val(text):
        return Paragraph(str(text), style_chk_val) if text else ""

    def _cell_hdr(text):
        return Paragraph(f"<b>{text}</b>", style_chk_hdr)

    checkup_rows = []
    style_cmds   = []

    for ri, (L, R) in enumerate(zip(left_seq, right_seq)):
        if L[0] == "HDR":
            lc0 = _cell_hdr(L[1]); lc1 = ""
            style_cmds += [
                ("SPAN",       (0, ri), (1, ri)),
                ("BACKGROUND", (0, ri), (1, ri), HEADER_COLOR),
                ("ALIGN",      (0, ri), (1, ri), "CENTER"),
            ]
        elif L[0] == "ROW":
            lc0 = _cell_lbl(L[1]); lc1 = _cell_val(L[2])
            style_cmds.append(("BACKGROUND", (0, ri), (0, ri), HEADER_COLOR))
        else:
            lc0 = ""; lc1 = ""

        if R[0] == "HDR":
            rc0 = _cell_hdr(R[1]); rc1 = ""
            style_cmds += [
                ("SPAN",       (2, ri), (3, ri)),
                ("BACKGROUND", (2, ri), (3, ri), HEADER_COLOR),
                ("ALIGN",      (2, ri), (3, ri), "CENTER"),
            ]
        elif R[0] == "ROW":
            rc0 = _cell_lbl(R[1]); rc1 = _cell_val(R[2])
            style_cmds.append(("BACKGROUND", (2, ri), (2, ri), HEADER_COLOR))
        else:
            rc0 = ""; rc1 = ""

        checkup_rows.append([lc0, lc1, rc0, rc1])

    base_chk_style = [
        ("FONTNAME",      (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 7),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BACKGROUND",    (0, 0), (-1, -1), colors.white),
    ]
    for ri, (L, R) in enumerate(zip(left_seq, right_seq)):
        if L[0] == "ROW" and ri % 2 == 0:
            base_chk_style.append(("BACKGROUND", (1, ri), (1, ri), ALT_COLOR))
        if R[0] == "ROW" and ri % 2 == 0:
            base_chk_style.append(("BACKGROUND", (3, ri), (3, ri), ALT_COLOR))

    all_chk_cmds = base_chk_style + style_cmds
    checkup_tbl = Table(checkup_rows, colWidths=COL_WIDTHS_CHK)
    checkup_tbl.setStyle(TableStyle(all_chk_cmds))
    content.append(checkup_tbl)
    content.append(Spacer(1, 14))

    # --- sorted df2 ---
    df2 = pd.DataFrame()
    if "datetime" in df.columns and not df.empty:
        df2 = df.dropna(subset=["datetime"]).copy()
        df2["datetime"] = pd.to_datetime(df2["datetime"])
        df2 = df2.sort_values("datetime").reset_index(drop=True)

    first_dt     = meta.get("_first_datetime")
    last_dt      = df2["datetime"].max() if not df2.empty else None
    hourly_slots = build_hourly_slots(first_dt, last_dt)

    try:
        init_dist_nm = float(str(meta.get("initial_dist_nm","") or "").replace("nm","").strip() or "nan")
    except:
        init_dist_nm = np.nan

    # Cumulative distance
    miles_per_slot = {}
    if not df2.empty and hourly_slots:
        lats_v = df2["lat_raw"].values;  lons_v = df2["lon_raw"].values
        valid  = ~(np.isnan(lats_v) | np.isnan(lons_v))
        d_arr  = np.zeros(len(df2))
        if valid.any():
            seg = np.where(
                valid[:-1] & valid[1:],
                haversine_vec(lats_v[:-1], lons_v[:-1], lats_v[1:], lons_v[1:]),
                0.0
            )
            seg = np.where(seg > 1, 0.0, seg)
            d_arr[1:] = seg
        cum_nm = np.cumsum(d_arr)
        ts_ns_v  = df2["datetime"].values.astype("int64")
        slots_ns = np.array([pd.Timestamp(s).value for s in hourly_slots])
        for slot, slot_ns in zip(hourly_slots, slots_ns):
            idx = np.searchsorted(ts_ns_v, slot_ns, side="right") - 1
            if idx >= 0:
                miles_per_slot[slot] = float(cum_nm[idx])

    engine_minutes_by_slot = {}
    if not df_events.empty and "timestamp" in df_events.columns and "engine_minutes" in df_events.columns:
        ev_eh = df_events.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        eh_ts_ns = ev_eh["timestamp"].apply(lambda x: pd.Timestamp(x).value).values
        eh_vals  = ev_eh["engine_minutes"].values.astype(float)
        for slot, slot_ns in zip(hourly_slots,
                                  [pd.Timestamp(s).value for s in hourly_slots]):
            idx = np.searchsorted(eh_ts_ns, slot_ns, side="right") - 1
            engine_minutes_by_slot[slot] = float(eh_vals[idx]) if idx >= 0 else 0.0

    equipment_by_slot = {}
    if not df_events.empty and "timestamp" in df_events.columns:
        ev = df_events.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        cur_e="OFF"; cur_m="OFF"; cur_j="OFF"; cur_st="OFF"; cur_sm="OFF"; cur_sp="OFF"
        cur_sea_s="0"; cur_h="OFF"; cur_n="OFF"; cur_i="OFF"
        cur_ck="OFF"; cur_cp="OFF"; cur_ci="OFF"; ev_idx=0
        ev_ts_ns2 = ev["timestamp"].apply(lambda x: pd.Timestamp(x).value).values
        for slot, slot_ns in zip(hourly_slots,
                                  [pd.Timestamp(s).value for s in hourly_slots]):
            while ev_idx < len(ev) and ev_ts_ns2[ev_idx] <= slot_ns:
                row=ev.iloc[ev_idx]; et=row["event_type"]; ed=row["event_detail"]
                if et=="ENGINE":    cur_e  = "ON" if "ON" in ed else "OFF"
                elif et=="MAIN":    cur_m  = "ON" if "ON" in ed else "OFF"
                elif et=="JIB":     cur_j  = ed
                elif et=="STAYSAIL":cur_st = ed
                elif et=="STORMJIB":cur_sm = ed
                elif et=="SPINNAKER":cur_sp= ed
                elif et=="SEA":     cur_sea_s = ed
                elif et=="HYPERNET":cur_h  = ed
                elif et=="NET":     cur_n  = ed
                elif et=="INLINE":  cur_i  = ed
                elif et=="CTD_KEEL":     cur_ck = ed
                elif et=="CTD_PROFILE":  cur_cp = ed
                elif et=="CTD_INTERCOMP":cur_ci = ed
                ev_idx += 1
            sails = [x for x,s in [("MAIN",cur_m),("JIB",cur_j),("STAYSAIL",cur_st),
                                    ("STORM JIB",cur_sm),("SPINNAKER",cur_sp)] if s=="ON"]
            sci   = [x for x,s in [("HYPERNET",cur_h),("NET",cur_n),("INLINE",cur_i),
                                    ("CTD KEEL",cur_ck),("CTD PROF",cur_cp),
                                    ("CTD INTER",cur_ci)] if s=="ON"]
            equipment_by_slot[slot] = {
                "engine": cur_e,
                "sails":  "+".join(sails) if sails else "-",
                "sea":    cur_sea_s,
                "science":"+".join(sci)   if sci   else "-",
            }

    slot_stats = precompute_slot_stats(df2, hourly_slots)

    # -------------------------------------------------------
    # TABLE 1 : HOURLY LOG
    # -------------------------------------------------------
    content.append(Paragraph("HOURLY LOG", style_section))

    CW = [
        1.8*cm, 2.3*cm, 2.3*cm, 1.3*cm, 2.4*cm, 0.8*cm, 2.6*cm,
        1.2*cm, 1.2*cm, 1.2*cm, 1.2*cm, 1.4*cm, 1.9*cm, 1.6*cm,
        1.4*cm, 1.4*cm, 1.4*cm, 1.4*cm,
    ]

    HIGHLIGHT_COLS = [14, 15, 16, 17]
    ENG_H_COL      = 13

    if not df2.empty and hourly_slots:

        header_h = [
            "TIME\n(UTC)", "LAT", "LON",
            "ENGINE", "SAILS", "SEA", "SCIENCE",
            "SOG\n(kn)", "TWS\n(kn)", "TWD\n(°)", "HDG\n(°)",
            "DIST\ncum(nm)", "DIST\nrem / %",
            "ENGINE\ncum",
            "TWS\nmoy(kn)", "SOG\nmoy(kn)", "SOG\nmax(kn)", "TWS\nmax(kn)",
        ]

        table_h  = [header_h]
        prev_date = None
        col_values = {c: [] for c in HIGHLIGHT_COLS}
        data_rows  = []

        for slot in hourly_slots:
            equip = equipment_by_slot.get(slot, {})
            miles = miles_per_slot.get(slot, 0.0)
            em    = engine_minutes_by_slot.get(slot, 0.0)
            st    = slot_stats.get(slot, {})

            if not np.isnan(init_dist_nm):
                remaining = max(init_dist_nm - miles, 0.0)
                pct_done  = min(miles / init_dist_nm * 100.0, 100.0) if init_dist_nm > 0 else 0.0
                rem_str   = f"{remaining:.1f}\n({pct_done:.0f}%)"
            else:
                rem_str = "—"

            slot_date = slot.date()
            show_date = (prev_date is None or slot_date != prev_date)
            prev_date = slot_date

            row_vals = {
                14: safe_float(st.get("tws_moy")),
                15: safe_float(st.get("sog_moy")),
                16: safe_float(st.get("sog_max")),
                17: safe_float(st.get("tws_max")),
            }
            for c, v in row_vals.items():
                col_values[c].append(v)

            data_rows.append({
                "slot": slot,
                "row": [
                    fmt_ts(slot, show_date=show_date),
                    decimal_to_deg_min(st.get("lat"), is_lon=False),
                    decimal_to_deg_min(st.get("lon"), is_lon=True),
                    equip.get("engine","-"),
                    equip.get("sails","-"),
                    equip.get("sea","-"),
                    equip.get("science","-"),
                    fmt(st.get("sog_m")), fmt(st.get("tws_m")),
                    fmt(st.get("twd_m"), 0), fmt(st.get("hdg_m"), 0),
                    f"{miles:.1f}",
                    rem_str,
                    engine_hhmm(em),
                    fmt(st.get("tws_moy")), fmt(st.get("sog_moy")),
                    fmt(st.get("sog_max")), fmt(st.get("tws_max")),
                ],
                "em": em,
                "row_vals": row_vals,
            })

        col_max = {}
        for c in HIGHLIGHT_COLS:
            vals = [v for v in col_values[c] if not np.isnan(v)]
            col_max[c] = max(vals) if vals else None

        for dr in data_rows:
            table_h.append(dr["row"])

        t_h = Table(table_h, colWidths=CW, repeatRows=1)
        ts_style = base_table_style(len(table_h))

        last_data_row_idx = len(data_rows)

        for i, dr in enumerate(data_rows):
            ri = i + 1
            for c in HIGHLIGHT_COLS:
                v = dr["row_vals"].get(c, np.nan)
                if col_max.get(c) is not None and not np.isnan(v) and v == col_max[c]:
                    ts_style.append(("BACKGROUND", (c, ri), (c, ri), COLOR_HIGHLIGHT))
            if ri == last_data_row_idx:
                ts_style.append(("BACKGROUND", (ENG_H_COL, ri), (ENG_H_COL, ri), COLOR_ENG_LAST))

        t_h.setStyle(TableStyle(ts_style))
        content.append(t_h)

    else:
        content.append(Paragraph("No GPS data available.", styles["Normal"]))

    content.append(Spacer(1, 16))

    # -------------------------------------------------------
    # TABLE 2a : EVENT LOG — NAVIGATION
    # -------------------------------------------------------
    content.append(Paragraph("EVENT LOG — NAVIGATION", style_section))

    if not df_events.empty:
        ev_sorted_all = df_events.sort_values("timestamp").reset_index(drop=True) \
                        if "timestamp" in df_events.columns else df_events.copy()

        ev_nav = ev_sorted_all[ev_sorted_all["event_type"].isin(NAV_EVENT_TYPES)].reset_index(drop=True)
        ev_sci = ev_sorted_all[ev_sorted_all["event_type"].isin(SCIENCE_EVENT_TYPES)].reset_index(drop=True)

        ev_header = [
            "TIME\n(UTC)", "LAT", "LON",
            "ENGINE", "SAILS", "SEA",
            "SOG\n(kn)", "TWS\n(kn)", "TWD\n(°)", "HDG\n(°)",
            "TYPE", "DETAIL / COMMENT",
        ]
        cw_e = [1.8*cm, 2.3*cm, 2.3*cm, 1.3*cm, 2.4*cm, 0.8*cm,
                1.2*cm, 1.2*cm, 1.2*cm, 1.2*cm, 2.2*cm, 5.1*cm]

        def build_event_table(ev_df, show_science_col=False):
            if ev_df.empty:
                return None

            ev_instants = precompute_event_instants(
                df2, list(ev_df.get("timestamp", pd.Series([])))
            )

            hdr = ev_header[:]
            cw  = cw_e[:]
            if show_science_col:
                hdr = ev_header[:6] + ["SCIENCE"] + ev_header[6:]
                cw  = cw_e[:6] + [2.6*cm] + cw_e[6:]

            ev_data    = [hdr]
            row_colors = []
            prev_date_ev = None

            for idx, r in ev_df.iterrows():
                et  = str(r.get("event_type",  "-"))
                ed  = str(r.get("event_detail","-"))

                detail_para = Paragraph(ed, style_cell)

                try:
                    ev_date   = pd.Timestamp(r.get("timestamp")).date()
                    show_date = (prev_date_ev is None or ev_date != prev_date_ev)
                    prev_date_ev = ev_date
                except:
                    show_date = False

                ii = list(ev_df.index).index(idx)
                inst = ev_instants.iloc[ii] if ii < len(ev_instants) else {}

                row_colors.append(event_color(et))
                row = [
                    fmt_ts(r.get("timestamp"), show_date=show_date),
                    decimal_to_deg_min(r.get("lat"), is_lon=False),
                    decimal_to_deg_min(r.get("lon"), is_lon=True),
                    str(r.get("engine","-")),
                    str(r.get("sails", "-")),
                    str(r.get("sea",   "-")),
                ]
                if show_science_col:
                    row.append(science_str_from_row(r))
                row += [
                    fmt(inst.get("sog_i")), fmt(inst.get("tws_i")),
                    fmt(inst.get("twd_i"), 0), fmt(inst.get("hdg_i"), 0),
                    et, detail_para,
                ]
                ev_data.append(row)

            TYPE_COL   = len(hdr) - 2
            DETAIL_COL = len(hdr) - 1

            ev_style = base_table_style(len(ev_data))
            for i in range(1, len(ev_data)):
                ev_style.append(("BACKGROUND", (0,i), (-1,i), colors.white))
            for i, col in enumerate(row_colors):
                ri = i + 1
                ev_style.append(("BACKGROUND", (TYPE_COL,   ri), (TYPE_COL,   ri), col))
                ev_style.append(("BACKGROUND", (DETAIL_COL, ri), (DETAIL_COL, ri), col))
                ev_style.append(("TEXTCOLOR",  (TYPE_COL,   ri), (TYPE_COL,   ri), COLOR_EVENT_TEXT))
                ev_style.append(("TEXTCOLOR",  (DETAIL_COL, ri), (DETAIL_COL, ri), COLOR_EVENT_TEXT))
            ev_style.append(("VALIGN", (0,0), (-1,-1), "TOP"))

            te = Table(ev_data, colWidths=cw, repeatRows=1)
            te.setStyle(TableStyle(ev_style))
            return te

        nav_table = build_event_table(ev_nav, show_science_col=False)
        if nav_table:
            content.append(nav_table)
        else:
            content.append(Paragraph("No navigation events recorded.", styles["Normal"]))

        content.append(Spacer(1, 16))

        # -------------------------------------------------------
        # TABLE 2b : EVENT LOG — SCIENCE
        # -------------------------------------------------------
        content.append(Paragraph("EVENT LOG — SCIENCE", style_section))

        sci_table = build_event_table(ev_sci, show_science_col=True)
        if sci_table:
            content.append(sci_table)
        else:
            content.append(Paragraph("No science events recorded.", styles["Normal"]))

    else:
        content.append(Paragraph("No events recorded.", styles["Normal"]))

    # -------------------------------------------------------
    # VOYAGE STATISTICS
    # -------------------------------------------------------
    content.append(Spacer(1, 14))
    content.append(Paragraph("VOYAGE STATISTICS", style_section))
    content.append(Spacer(1, 8))

    def ts_of_max(col):
        try:
            idx = df[col].idxmax()
            dt  = pd.to_datetime(df.loc[idx,"datetime"])
            la  = df.loc[idx,"lat_raw"]; lo = df.loc[idx,"lon_raw"]
            return dt.strftime("%d/%m %H:%M"), decimal_to_deg_min(la), decimal_to_deg_min(lo,True)
        except:
            return "-","-","-"

    sog_max = df["SOG_RMC"].max() if "SOG_RMC" in df.columns else np.nan
    sog_max_ts,_,_ = ts_of_max("SOG_RMC")
    tws_max = df["TWS"].max() if "TWS" in df.columns else np.nan
    tws_max_ts,_,_ = ts_of_max("TWS")

    sog_1h_nm = np.nan; sog_1h_ts = "-"
    if not df2.empty:
        try:
            dt_s  = df2["datetime"].values.astype("int64")
            lats_v= df2["lat_raw"].values; lons_v = df2["lon_raw"].values
            one_h = int(3600e9)
            best  = 0.0; best_start = None
            j = 0
            for i in range(len(df2)):
                while j < len(df2) - 1 and dt_s[j+1] - dt_s[i] <= one_h:
                    j += 1
                if j > i:
                    seg_lats = lats_v[i:j+1]; seg_lons = lons_v[i:j+1]
                    valid_m  = ~(np.isnan(seg_lats) | np.isnan(seg_lons))
                    if valid_m.sum() >= 2:
                        d = np.sum(np.where(
                            valid_m[:-1] & valid_m[1:],
                            haversine_vec(seg_lats[:-1],seg_lons[:-1],seg_lats[1:],seg_lons[1:]),
                            0.0
                        ))
                        if d > best:
                            best = d
                            best_start = pd.Timestamp(dt_s[i])
            if best > 0:
                sog_1h_nm = best
                sog_1h_ts = best_start.strftime("%d/%m %H:%M") if best_start else "-"
        except: pass

    tws_1h = np.nan; tws_1h_ts = "-"
    if "TWS" in df.columns and not df.empty:
        try:
            s=df["TWS"].dropna(); roll=s.rolling(10,min_periods=5).mean()
            if not roll.empty:
                idx_b=roll.idxmax(); tws_1h=roll[idx_b]
                tws_1h_ts=pd.to_datetime(df.loc[idx_b,"datetime"]).strftime("%d/%m %H:%M")
        except: pass

    total_nm = np.nan
    if not df2.empty:
        try:
            lv = df2["lat_raw"].values; lnv = df2["lon_raw"].values
            vm = ~(np.isnan(lv) | np.isnan(lnv))
            segs = np.where(vm[:-1]&vm[1:], haversine_vec(lv[:-1],lnv[:-1],lv[1:],lnv[1:]), 0.0)
            total_nm = float(np.sum(np.where(segs > 1, 0.0, segs)))
        except: pass

    elapsed_str, avg_sog_str = compute_navigation_elapsed(df2)

    avg_twd_str="-"
    if "TWD" in df.columns:
        try:
            a=circ_mean(df["TWD"])
            if not np.isnan(a): avg_twd_str=f"{a:.0f}°"
        except: pass

    avg_tws_str="-"
    if "TWS" in df.columns:
        try:
            v=df["TWS"].dropna().mean()
            if not np.isnan(v): avg_tws_str=f"{v:.1f} kn"
        except: pass

    eng_h_str="-"
    if not df_events.empty and "engine_minutes" in df_events.columns:
        try:
            em = df_events["engine_minutes"].max()
            if not np.isnan(em):
                eng_h_str = engine_hhmm(em)
        except: pass

    heel_max_str="-"
    if "heel" in df.columns:
        try:
            h=df["heel"].abs().max()
            if not np.isnan(h): heel_max_str=f"{h:.1f}°"
        except: pass

    def sv(val, unit="", decimals=2):
        try:
            v=float(val)
            if np.isnan(v): return "—"
            return f"{v:.{decimals}f} {unit}".strip()
        except: return "—"

    stats_rows = [
        ["AVG SOG (sailing)",  avg_sog_str,
         "AVG TWS",            avg_tws_str],
        ["MAX SOG / 1H",       f"{sv(sog_1h_nm,'nm',1)}  @{sog_1h_ts}",
         "MAX TWS / 1H",       f"{sv(tws_1h,'kn')}  @{tws_1h_ts}"],
        ["MAX SOG",            f"{sv(sog_max,'kn')}  @{sog_max_ts}",
         "MAX TWS",            f"{sv(tws_max,'kn')}  @{tws_max_ts}"],
        ["MAX HEEL",           heel_max_str,
         "TOTAL DIST.",        sv(total_nm,"nm",1)],
        ["ELAPSED (sailing)",  elapsed_str,
         "ENGINE HOURS",       eng_h_str],
    ]

    style_sl = ParagraphStyle("SL", parent=styles["Normal"], fontSize=7,
                               textColor=colors.white, fontName="Helvetica-Bold")
    style_sv = ParagraphStyle("SV", parent=styles["Normal"], fontSize=8)

    stat_display = [
        [Paragraph(r[0],style_sl), Paragraph(str(r[1]),style_sv),
         Paragraph(r[2],style_sl), Paragraph(str(r[3]),style_sv)]
        for r in stats_rows
    ]

    ELAPSED_ROW = len(stats_rows) - 1
    RED_VAL = colors.HexColor("#f5c6cb")
    RED_LBL = colors.HexColor("#c0392b")

    stat_style_list = [
        ("BACKGROUND",  (0,0),(-1,-1), colors.HexColor("#f4f7fb")),
        ("BACKGROUND",  (0,0),(0,-1),  HEADER_COLOR),
        ("BACKGROUND",  (2,0),(2,-1),  HEADER_COLOR),
        ("FONTNAME",    (0,0),(-1,-1), "Helvetica"),
        ("FONTSIZE",    (0,0),(-1,-1), 8),
        ("VALIGN",      (0,0),(-1,-1), "MIDDLE"),
        ("GRID",        (0,0),(-1,-1), 0.4, colors.HexColor("#c0cfe0")),
        ("LEFTPADDING", (0,0),(-1,-1), 6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",  (0,0),(-1,-1), 5), ("BOTTOMPADDING",(0,0),(-1,-1),5),
        *[("BACKGROUND",(1,i),(1,i),colors.HexColor("#eaf0f8"))
          for i in range(0,len(stats_rows),2) if i!=ELAPSED_ROW],
        *[("BACKGROUND",(3,i),(3,i),colors.HexColor("#eaf0f8"))
          for i in range(0,len(stats_rows),2) if i!=ELAPSED_ROW],
        ("BACKGROUND",  (0,ELAPSED_ROW),(0,ELAPSED_ROW), RED_LBL),
        ("BACKGROUND",  (1,ELAPSED_ROW),(1,ELAPSED_ROW), RED_VAL),
        ("BACKGROUND",  (2,ELAPSED_ROW),(2,ELAPSED_ROW), RED_LBL),
        ("BACKGROUND",  (3,ELAPSED_ROW),(3,ELAPSED_ROW), RED_VAL),
    ]
    ts_tbl = Table(stat_display, colWidths=[4.5*cm,9.0*cm,4.5*cm,9.0*cm])
    ts_tbl.setStyle(TableStyle(stat_style_list))
    content.append(ts_tbl)
    content.append(Spacer(1, 16))

    # --- TRACK CHART ---
    map_img = build_track_map(df, meta)
    if map_img is not None:
        content.append(Paragraph("TRACK CHART", style_section))
        content.append(Spacer(1, 4))
        content.append(map_img)

    doc.build(content)
    print("PDF generated:", out_pdf)


# =========================================================
# BATCH PROCESSING
# =========================================================

for log_dir_name in os.listdir(base_folder):

    log_dir = os.path.join(base_folder, log_dir_name)
    if not os.path.isdir(log_dir): continue

    gz_files  = [f for f in os.listdir(log_dir) if f.endswith(".gz")]
    txt_files = [f for f in os.listdir(log_dir) if f.endswith(".txt")]

    if gz_files:
        target_file = gz_files[0]; in_progress = False
    elif txt_files:
        target_file = txt_files[0]; in_progress = True
    else:
        continue

    path = os.path.join(log_dir, target_file)
    base = target_file.replace(".gz","").replace(".txt","")

    out_xlsx = os.path.join(log_dir, base + ".xlsx")
    out_csv  = os.path.join(log_dir, base + ".csv")
    out_pdf  = os.path.join(log_dir, base + ".pdf")

    if not in_progress:
        if os.path.exists(out_xlsx) and os.path.exists(out_csv) and os.path.exists(out_pdf):
            print("Already processed:", target_file); continue

    status = "IN PROGRESS" if in_progress else "DONE"
    print(f"Processing [{status}] : {target_file}")

    df, df_events, meta = parse_nmea(path)
    if df.empty:
        print("  Empty file, skipped."); continue

    df = df.sort_values("datetime").reset_index(drop=True)

    # XLSX — 1 line per minute
    df_xlsx = df.copy()
    if "datetime" in df_xlsx.columns and not df_xlsx.empty:
        try:
            df_xlsx["datetime"] = pd.to_datetime(df_xlsx["datetime"])
            df_xlsx = df_xlsx.dropna(subset=["datetime"])
            df_xlsx = df_xlsx.set_index("datetime")
            df_xlsx = df_xlsx.resample("1min").first().dropna(how="all")
            df_xlsx = df_xlsx.reset_index()
        except Exception as e:
            print(f"  [WARN] resample XLSX: {e}")
            df_xlsx = df
    df_xlsx.to_excel(out_xlsx, index=False)
    print(f"  XLSX : {out_xlsx}  ({len(df_xlsx)} rows)")

    df_01nm = resample_every_0_1nm(df, df_events=df_events)
    df_01nm.to_csv(out_csv, index=False)
    print("  CSV  :", out_csv)

    generate_pdf(df, df_events, meta, out_pdf, in_progress=in_progress)
