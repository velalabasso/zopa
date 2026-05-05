import pandas as pd
import numpy as np
import os
import gzip

# =========================================================
# CONFIG
# =========================================================

base_folder = "/home/zopa/science/nmea_logs"

# =========================================================
# UTILS
# =========================================================

def safe_float(x):
    try:
        return float(x)
    except:
        return np.nan


def safe_int(x):
    try:
        return int(x)
    except:
        return np.nan


def nmea_to_decimal(coord, direction):
    try:
        if coord == "" or coord is None:
            return np.nan

        val = float(coord)
        deg = int(val // 100)
        minutes = val - deg * 100
        dec = deg + minutes / 60.0

        if direction in ["S", "W"]:
            dec *= -1

        return dec
    except:
        return np.nan


# =========================================================
# TWA
# =========================================================

def compute_twa(awa, aws, sog, hdg):
    try:
        if np.isnan(awa) or np.isnan(aws) or np.isnan(sog) or np.isnan(hdg):
            return np.nan

        awa = np.radians(awa)
        hdg = np.radians(hdg)

        awx = aws * np.cos(awa)
        awy = aws * np.sin(awa)

        bx = sog * np.cos(hdg)
        by = sog * np.sin(hdg)

        twx = awx + bx
        twy = awy + by

        return np.degrees(np.arctan2(twy, twx)) % 360
    except:
        return np.nan


# =========================================================
# DISTANCE
# =========================================================

def haversine_nm(lat1, lon1, lat2, lon2):
    R = 6371000

    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)

    a = (
        np.sin(dphi / 2) ** 2
        + np.cos(phi1) * np.cos(phi2) * np.sin(dlon / 2) ** 2
    )

    return (2 * R * np.arctan2(np.sqrt(a), np.sqrt(1 - a))) / 1852.0


# =========================================================
# PARSER NMEA
# =========================================================

def parse_nmea(file_path):

    records = []
    current = {}

    open_func = gzip.open if file_path.endswith(".gz") else open

    with open_func(file_path, "rt") as f:
        for line in f:

            if not line.startswith("$"):
                continue

            parts = line.strip().split(",")
            msg = parts[0]

            # ---------------- RMC ----------------
            if msg == "$GPRMC":

                if current:
                    records.append(current.copy())
                    current = {}

                current["datetime"] = pd.to_datetime(
                    parts[9] + parts[1],
                    format="%d%m%y%H%M%S",
                    errors="coerce"
                )

                current["lat_raw"] = nmea_to_decimal(parts[3], parts[4])
                current["lon_raw"] = nmea_to_decimal(parts[5], parts[6])

                current["SOG_RMC"] = safe_float(parts[7])
                current["COG_RMC"] = safe_float(parts[8])

            # ---------------- GGA ----------------
            elif msg == "$GPGGA":
                current["gps_fix_quality"] = safe_int(parts[6])
                current["satellites_used"] = safe_int(parts[7])
                current["hdop"] = safe_float(parts[8])
                current["altitude"] = safe_float(parts[9])

            # ---------------- VTG ----------------
            elif msg == "$GPVTG":
                current["COG_true"] = safe_float(parts[1])
                current["SOG_VTG"] = safe_float(parts[5])

            # ---------------- HDG ----------------
            elif msg == "$IIHDG":
                current["HDG"] = safe_float(parts[1])

            # ---------------- WIND ----------------
            elif msg == "$WIMWV":
                if parts[2] == "R":
                    current["AWA"] = safe_float(parts[1])
                    current["AWS"] = safe_float(parts[3])

            elif msg == "$WIMWD":
                current["TWD"] = safe_float(parts[1])
                current["TWS"] = safe_float(parts[5])

            # ---------------- DEPTH ----------------
            elif msg == "$SDDPT":
                current["depth_m"] = safe_float(parts[1])

            elif msg == "$SDDBT":
                current["depth_ft"] = safe_float(parts[1])
                current["depth_m_dbt"] = safe_float(parts[3])

            # ---------------- ENV ----------------
            elif msg == "$IIXDR":
                for i in range(1, len(parts) - 1, 4):
                    try:
                        val = safe_float(parts[i + 1])
                        name = parts[i + 3]

                        if name == "AIRTEMP":
                            current["air_temp"] = val
                        elif name == "HEEL":
                            current["heel"] = val
                        elif name == "TRIM":
                            current["trim"] = val
                        elif name == "BARO":
                            current["pressure"] = val
                        elif name == "RUDDER":
                            current["rudder_angle"] = val
                    except:
                        pass

            # ---------------- LOG ----------------
            elif msg == "$SDVLW":
                current["log_total_nm"] = safe_float(parts[2])
                current["log_trip_nm"] = safe_float(parts[4])

    if current:
        records.append(current)

    df = pd.DataFrame(records)

    df["TWA"] = df.apply(
        lambda r: compute_twa(
            r.get("AWA"),
            r.get("AWS"),
            r.get("SOG_RMC"),
            r.get("HDG")
        ),
        axis=1
    )

    return df


# =========================================================
# BATCH PROCESSING (IMPORTANT FIX)
# =========================================================

for log_dir_name in os.listdir(base_folder):

    log_dir = os.path.join(base_folder, log_dir_name)

    if not os.path.isdir(log_dir):
        continue

    gz_files = [f for f in os.listdir(log_dir) if f.endswith(".gz")]

    if len(gz_files) == 0:
        continue

    gz_file = gz_files[0]
    path = os.path.join(log_dir, gz_file)

    print("Processing:", path)

    df = parse_nmea(path)

    if df.empty:
        continue

    df = df.sort_values("datetime").reset_index(drop=True)

    base = os.path.splitext(os.path.splitext(gz_file)[0])[0]

    # =====================================================
    # XLSX
    # =====================================================
    out_xlsx = os.path.join(log_dir, base + ".xlsx")
    df.to_excel(out_xlsx, index=False)
    print("saved:", out_xlsx)

    # =====================================================
    # DISTANCE NM
    # =====================================================
    lat = df["lat_raw"].values
    lon = df["lon_raw"].values

    dist_nm = [0]

    for i in range(1, len(df)):
        if np.isnan(lat[i]) or np.isnan(lon[i]) or np.isnan(lat[i-1]) or np.isnan(lon[i-1]):
            dist_nm.append(dist_nm[-1])
        else:
            d = haversine_nm(lat[i-1], lon[i-1], lat[i], lon[i])
            dist_nm.append(dist_nm[-1] + d)

    df["dist_nm"] = dist_nm

    # =====================================================
    # BINNING 1 NM
    # =====================================================
    df["mile"] = df["dist_nm"].astype(int)

    grouped = df.groupby("mile").agg({
        "lat_raw": "mean",
        "lon_raw": "mean",
        "datetime": "first",
        "SOG_RMC": "mean",
        "TWS": "mean"
    }).reset_index()

    grouped["datetime"] = grouped["datetime"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    grouped.rename(columns={
        "lat_raw": "latitude",
        "lon_raw": "longitude",
        "datetime": "timestamp",
        "SOG_RMC": "sog",
        "TWS": "tws"
    }, inplace=True)

    # =====================================================
    # CSV (FORMAT STRICT)
    # =====================================================

    out_csv = os.path.join(log_dir, base + ".csv")

    # force copie propre + ordre EXACT
    csv_df = grouped.copy()

    # format timestamp ISO
    csv_df["timestamp"] = pd.to_datetime(csv_df["timestamp"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # sélection + ordre EXACT demandé
    csv_df = csv_df[[
        "longitude",
        "latitude",
        "timestamp",
        "sog",
        "tws"
    ]]

    # export
    csv_df.to_csv(
        out_csv,
        index=False,
        sep=";",
        float_format="%.5f"   # optionnel mais propre pour nav/science
    )

    print("CSV saved:", out_csv)
