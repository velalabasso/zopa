#!/usr/bin/env python3

import socket
import time
import subprocess
import gzip
import shutil
import os
import threading
import math
import signal
import sys
import re
from datetime import datetime, timezone, timedelta

# =========================================================
# CONFIG
# =========================================================

WIFI_SSID     = "Vulcan 9R e6e9"
WIFI_PASSWORD = "OVyWzmlU"

HOST = "192.168.76.1"
PORT = 10110

base_folder = "/home/zopa/science/nmea_logs"
os.makedirs(base_folder, exist_ok=True)

# =========================================================
# GLOBALS
# =========================================================

event_log_file = None
latest_fix     = None
latest_fix_mono = None   # time.monotonic() au moment où latest_fix a été reçu

distance_traveled_nm = 0
initial_distance_nm  = None
last_position        = None

motor_state     = "OFF"
dessal_state    = "OFF"

main_state     = "OFF"
main_reef      = 0
jib_state      = "OFF"
staysail_state = "OFF"
stormjib_state = "OFF"
spi_state      = "OFF"

sea_state = "0"

hypernet_state      = "OFF"
net_state           = "OFF"
inline_state        = "OFF"
filtration_state    = "OFF"
ctd_keel_state      = "OFF"
ctd_profile_state   = "OFF"
ctd_intercomp_state = "OFF"

bio_station_id = None      # e.g. "01", "02"
hyp_station_id = None      # e.g. "001", "002"

_shutdown_requested = False

# =========================================================
# DISPLAY BUFFER
# =========================================================

_display_lock    = threading.Lock()
_pending_display = []
_user_typing     = False

def _flush_pending():
    with _display_lock:
        if _pending_display:
            print()
            for line in _pending_display:
                print(line)
            _pending_display.clear()

def _buffered_print(line):
    with _display_lock:
        _pending_display.append(line)

# =========================================================
# SIGNAL HANDLER
# =========================================================

def _sigint_handler(signum, frame):
    global _shutdown_requested
    if _shutdown_requested:
        print("\n(shutdown already in progress…)")
        return
    _shutdown_requested = True
    print("\n\n⚓  SHUTDOWN REQUESTED")
    _ask_arrival_and_close()


def _ask_arrival_and_close(default_port=""):
    global event_log_file, distance_traveled_nm

    try:
        prompt = f"Arrival port [{default_port}] : " if default_port else "Arrival port : "
        arrival = input(prompt).strip()
        if not arrival:
            arrival = default_port
    except (EOFError, KeyboardInterrupt):
        arrival = default_port

    if arrival:
        write_event(f"ARRIVAL : {arrival}")
    write_event(f"TOTAL TRAVELED : {distance_traveled_nm:.1f} nm")

    print("\n===================================")
    print("       END OF TRIP CONTROL")
    print("===================================\n")

    def ask_yn_fin(question):
        while True:
            ans = input(f"  {question} (Y/N) : ").strip().upper()
            if ans in ("Y", "N"):
                return ans == "Y"
            print("  Please answer Y or N.")

    hull_clean      = ask_yn_fin("Hull clean")
    engine_bilge_ok = ask_yn_fin("Engine bilge dry and clean")
    fore_bilge_ok   = ask_yn_fin("Fore bilge dry and clean")

    def yn(b): return "OK" if b else "NOK"

    write_event(
        f"END OF TRIP CONTROL | HULL={yn(hull_clean)} "
        f"| ENGINE BILGE={yn(engine_bilge_ok)} "
        f"| FORE BILGE={yn(fore_bilge_ok)}"
    )

    if event_log_file and os.path.exists(event_log_file):
        with open(event_log_file, "a") as f:
            f.write("\n====================================\n")
            f.write("       END OF TRIP CONTROL\n")
            f.write("====================================\n")
            f.write(f"ARRIVAL PORT    : {arrival}\n")
            f.write(f"TOTAL DISTANCE  : {distance_traveled_nm:.1f} nm\n")
            f.write(f"HULL CLEAN      : {yn(hull_clean)}\n")
            f.write(f"ENGINE BILGE    : {yn(engine_bilge_ok)}\n")
            f.write(f"FORE BILGE      : {yn(fore_bilge_ok)}\n")
            f.write("====================================\n")

        print("Compressing log…")
        compress_file(event_log_file)
        print("File compressed.")
    else:
        print("(no log file to compress)")

    print("Goodbye.")
    os._exit(0)


signal.signal(signal.SIGINT, _sigint_handler)

# =========================================================
# WIFI
# =========================================================

def connect_wifi():
    print("Connecting to WiFi…")
    try:
        subprocess.run(
            ["nmcli", "dev", "wifi", "connect", WIFI_SSID, "password", WIFI_PASSWORD],
            check=True
        )
        print("WiFi connected.")
    except Exception as e:
        print(f"WiFi error: {e}")

# =========================================================
# COMPRESSION
# =========================================================

def compress_file(file_path):
    gz_path = file_path + ".gz"
    with open(file_path, "rb") as f_in:
        with gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(file_path)
    print(f"Compressed: {gz_path}")

# =========================================================
# FORMAT DEG-MIN
# =========================================================

def parse_deg_min(text):
    text = text.strip().upper()
    direction = None
    for d in ["N", "S", "E", "W"]:
        if text.endswith(d):
            direction = d; text = text[:-1].strip(); break
        if text.startswith(d):
            direction = d; text = text[1:].strip(); break

    text  = text.replace("deg", " ").replace("°", " ").replace("'", " ").strip()
    parts = text.split()

    if len(parts) == 1:
        val = float(parts[0])
        if val > 360:
            deg = int(val // 100)
            dec = deg + (val - deg * 100) / 60.0
        else:
            dec = val
    elif len(parts) == 2:
        dec = int(float(parts[0])) + float(parts[1]) / 60.0
    else:
        raise ValueError(f"Unrecognised format: {text}")

    if direction in ["S", "W"]:   dec = -abs(dec)
    elif direction in ["N", "E"]: dec =  abs(dec)
    return dec


def decimal_to_deg_min(decimal, is_lon=False):
    if decimal is None or (isinstance(decimal, float) and math.isnan(decimal)):
        return "---"
    direction = ("E" if decimal >= 0 else "W") if is_lon else ("N" if decimal >= 0 else "S")
    dw  = 3 if is_lon else 2
    a   = abs(decimal)
    deg = int(a)
    mn  = (a - deg) * 60.0
    return f"{deg:0{dw}d}deg{mn:07.4f}'{direction}"

# =========================================================
# NMEA → DECIMAL
# =========================================================

def nmea_to_decimal(coord, direction):
    try:
        if coord == "": return None
        if len(coord.split(".")[0]) > 4:
            degrees = float(coord[:3]); minutes = float(coord[3:])
        else:
            degrees = float(coord[:2]); minutes = float(coord[2:])
        decimal = degrees + minutes / 60
        if direction in ["S", "W"]: decimal *= -1
        return decimal
    except:
        return None

# =========================================================
# HAVERSINE
# =========================================================

def haversine_nm(lat1, lon1, lat2, lon2):
    R    = 6371000
    phi1 = math.radians(lat1); phi2 = math.radians(lat2)
    a    = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
            + math.cos(phi1) * math.cos(phi2)
            * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)) / 1852

# =========================================================
# PARSE RMC
# =========================================================

def parse_rmc(line):
    try:
        parts = line.split(",")
        if parts[0] != "$GPRMC": return None
        lat = nmea_to_decimal(parts[3], parts[4])
        lon = nmea_to_decimal(parts[5], parts[6])
        if lat is None or lon is None: return None
        return {
            "datetime": datetime.strptime(parts[9] + parts[1][:6], "%d%m%y%H%M%S"),
            "lat": lat, "lon": lon,
            "speed": parts[7], "course": parts[8],
        }
    except:
        return None

# =========================================================
# HEURE GPS
# =========================================================

def set_fix(fix):
    """Enregistre un nouveau fix GPS + l'instant (horloge monotone) de réception."""
    global latest_fix, latest_fix_mono
    latest_fix      = fix
    latest_fix_mono = time.monotonic()


def gps_now():
    """
    Heure UTC courante d'après le GPS : heure du dernier $GPRMC + temps écoulé
    depuis sa réception (horloge monotone, indépendante de l'horloge du Pi qui
    peut être fausse en mer : pas de RTC, pas de NTP).
    Si la liaison NMEA est coupée, l'heure continue d'avancer correctement.
    Retourne None tant qu'aucun fix GPS n'a été reçu.
    """
    if latest_fix is None or latest_fix_mono is None:
        return None
    return latest_fix["datetime"] + timedelta(seconds=time.monotonic() - latest_fix_mono)


def now_utc_str():
    """Heure GPS si disponible, sinon heure système du Pi (repli)."""
    t = gps_now()
    if t is None:
        t = datetime.now(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")

# =========================================================
# EVENT  — horodatage UTC GPS
# =========================================================

def write_event(event, timestamp=None):
    global event_log_file

    if timestamp:
        ts_str = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        # Heure GPS (même référence que la trace NMEA) ; heure du Pi
        # seulement si aucun fix GPS n'a encore été reçu.
        ts_str = now_utc_str()

    line = f"# EVENT [{ts_str}] : {event}"

    # Saut de ligne avant, flush immédiat pour que le log défile
    print(f"\n{line}\n", flush=True)

    try:
        if event_log_file:
            with open(event_log_file, "a") as f:
                f.write(line + "\n")
    except Exception as e:
        print(f"Event write error: {e}", flush=True)

# =========================================================
# RETRODATE EVENT
# =========================================================

_RETRODATE_RE = re.compile(
    r"^(.+?)\s+(\d{8})\s+(\d{2}:\d{2}:\d{2})$"
)

def try_parse_retrodate(cmd):
    m = _RETRODATE_RE.match(cmd.strip())
    if not m:
        return None
    event_name = m.group(1).upper().strip()
    date_str   = m.group(2)
    time_str   = m.group(3)
    try:
        ts = datetime.strptime(date_str + " " + time_str, "%Y%m%d %H:%M:%S")
        return (event_name, ts)
    except ValueError:
        return None

# =========================================================
# FUEL ESTIMATION
# =========================================================

_FUEL_CALIB = [
    (0,   0),
    (20,  40),
    (40,  50),
    (60,  62),
    (80,  88),
    (100, 100),
    (120, 100),
]

def gauge_pct_to_litres(gauge_pct):
    if gauge_pct <= _FUEL_CALIB[0][1]:
        return _FUEL_CALIB[0][0]
    if gauge_pct >= _FUEL_CALIB[-1][1]:
        return _FUEL_CALIB[-1][0]
    for i in range(len(_FUEL_CALIB) - 1):
        l0, p0 = _FUEL_CALIB[i]
        l1, p1 = _FUEL_CALIB[i + 1]
        if p0 <= gauge_pct <= p1:
            if p1 == p0:
                return (l0 + l1) / 2
            t = (gauge_pct - p0) / (p1 - p0)
            return l0 + t * (l1 - l0)
    return None


def parse_engine_hours_hhmm(prompt_text):
    """
    Demande les heures moteur au format HH:MM et retourne un float en heures.
    Accepte aussi un nombre décimal direct (ex: 1.5).
    """
    while True:
        raw = input(prompt_text).strip()
        m = re.match(r"^(\d{1,4}):(\d{2})$", raw)
        if m:
            h  = int(m.group(1))
            mn = int(m.group(2))
            if mn < 60:
                return h + mn / 60.0
            print("  Les minutes doivent être < 60. Essayez à nouveau.")
        else:
            try:
                val = float(raw.replace(",", "."))
                if val >= 0:
                    return val
                print("  La valeur doit être >= 0.")
            except ValueError:
                print("  Format invalide. Utilisez HH:MM (ex: 12:34) ou un nombre décimal.")


def fuel_check_interactive():
    TANK_CAPACITY = 120.0
    CONSUMPTION   = 1.5

    print("\n  --- Fuel check ---")

    while True:
        try:
            last_fill_l = float(input("  Last fill (L) : ").strip())
            if last_fill_l <= 0: raise ValueError("must be > 0")
            break
        except ValueError as e:
            print(f"  Invalid value ({e}), try again.")

    # --- Format HH:MM ---
    engine_hours = parse_engine_hours_hhmm(
        "  Engine hours since last fill (HH:MM) : "
    )

    consumed    = engine_hours * CONSUMPTION
    remaining_1 = max(0.0, last_fill_l - consumed)
    pct_est_1   = round(remaining_1 / TANK_CAPACITY * 100, 1)

    print(f"\n  -> Estimate 1 (engine hours) : {remaining_1:.1f} L  ({pct_est_1:.1f} %)")

    while True:
        try:
            gauge_pct = float(input("  Fuel gauge reading (%) : ").strip())
            if not (0 <= gauge_pct <= 100): raise ValueError("out of [0-100]")
            break
        except ValueError as e:
            print(f"  Invalid value ({e}), try again.")

    remaining_2 = gauge_pct_to_litres(gauge_pct)
    pct_est_2   = round(remaining_2 / TANK_CAPACITY * 100, 1)

    print(f"  -> Estimate 2 (gauge)        : {remaining_2:.1f} L  ({pct_est_2:.1f} %)\n")

    return {
        "last_fill_l"   : last_fill_l,
        "engine_hours"  : engine_hours,
        "consumed_l"    : consumed,
        "remaining_est1": remaining_1,
        "pct_est1"      : pct_est_1,
        "gauge_pct"     : gauge_pct,
        "remaining_est2": remaining_2,
        "pct_est2"      : pct_est_2,
    }

# =========================================================
# ENGINE ON — with cooling water check + EVENT log
# =========================================================

def engine_on_sequence():
    global motor_state
    while True:
        ans = input("  Cooling water flowing overboard (Y/N) : ").strip().upper()
        if ans in ("Y", "N"):
            break
        print("  Please answer Y or N.")
    cooling_ok = (ans == "Y")
    motor_state = "ON"
    # FIX : write_event enregistre ENGINE ON → démarre le compteur moteur
    write_event(f"ENGINE ON | COOLING WATER {'OK' if cooling_ok else 'NOK'}")

# =========================================================
# FILTRATION helpers
# =========================================================

FILTRATION_SIZES = ["micro", "nano", "pico"]

def ask_filtration_size(prompt="  Filter size"):
    while True:
        ans = input(f"{prompt} (micro/nano/pico) : ").strip().lower()
        if ans in FILTRATION_SIZES:
            return ans
        print(f"  Please answer one of: {', '.join(FILTRATION_SIZES)}.")


def filtration_on_sequence():
    size = ask_filtration_size("  Filter size")
    return size


def filtration_off_sequence():
    size = ask_filtration_size("  Filter size closed")

    while True:
        try:
            volume = float(input("  Volume Lamprey filtré (mL) : ").strip())
            if volume < 0: raise ValueError("must be >= 0")
            break
        except ValueError as e:
            print(f"  Invalid value ({e}), try again.")

    while True:
        ans = input("  Saturation ? (Y/N) : ").strip().upper()
        if ans in ("Y", "N"):
            saturated = (ans == "Y")
            break
        print("  Please answer Y or N.")

    return size, volume, saturated

# =========================================================
# SETUP
# =========================================================

def navigation_setup():
    global sea_state, ctd_keel_state

    def ask_yn(question):
        while True:
            ans = input(f"{question} (Y/N) : ").strip().upper()
            if ans in ("Y", "N"):
                return ans == "Y"
            print("  Please answer Y or N.")

    # ---------- Mode : NAV ou MOORING ----------
    print("\n===================================")
    print("              MODE")
    print("===================================\n")
    while True:
        ans = input("Nav or Mooring? (N/M) : ").strip().upper()
        if ans in ("N", "NAV"):
            mode = "NAV"; break
        if ans in ("M", "MOORING"):
            mode = "MOORING"; break
        print("  Please answer N (Nav) or M (Mooring).")
    is_nav = (mode == "NAV")

    print("\n===================================")
    print("       PART 1 — NAVIGATION")
    print("===================================\n")

    skipper     = input("Skipper : ").strip()
    crew        = input("Crew : ").strip()
    departure   = input("Departure port : ").strip()
    destination = input("Destination port : ").strip()
    sea_val     = input("Sea state (0-9) : ").strip()

    # ---------- Engine check : NAV uniquement ----------
    fuel_data = None
    engine = {}
    if is_nav:
        print("\n===================================")
        print("    PART 2 — ENGINE CONTROL CHECK")
        print("===================================\n")

        fuel_data = fuel_check_interactive()

        print("  [Aft engine compartment]")
        engine["prefil_ok"]     = ask_yn("Fuel pre-filter clean")
        engine["seawater_ok"]   = ask_yn("Sea water filter clean")
        engine["priming_ok"]    = ask_yn("Priming bulb firm")
        engine["bilge_ok_rear"] = ask_yn("Engine bilge dry and clean")
        engine["coolant_ok"]    = ask_yn("Coolant level OK")
        engine["seacock_ok"]    = ask_yn("Sea cock open and ignition on")

        print("\n  [Forward engine compartment]")
        engine["belt_ok"]        = ask_yn("Belt tension OK (~1 cm deflection under thumb pressure)")
        engine["bilge_ok_front"] = ask_yn("Engine bilge dry and clean (forward)")

    # ---------- Science ----------
    print("\n===================================")
    print("         PART 3 — SCIENCE")
    print("===================================\n")

    ctd_keel = "ON" if ask_yn("CTD keel ON?") else "OFF"

    # ---------- Engine ON : NAV uniquement ----------
    engine_start_now = False
    if is_nav:
        print("\n===================================\n")
        # L'event ENGINE ON est écrit après création du fichier log
        engine_start_now = ask_yn("Engine ON now?")

    return {
        "mode"            : mode,
        "skipper"         : skipper,
        "crew"            : crew,
        "departure"       : departure,
        "destination"     : destination,
        "sea"             : sea_val,
        "fuel_data"       : fuel_data,
        "engine"          : engine,
        "ctd_keel"        : ctd_keel,
        "engine_start_now": engine_start_now,
    }

# =========================================================
# TERMINAL EVENTS
# =========================================================

def ask_station_id(name, format_str="Station ID"):
    """Ask user for station ID. format_str can be like '01' or '001'."""
    while True:
        raw = input(f"  {name} ID (e.g. 01 or 001) : ").strip()
        if raw:
            return raw
        print("  Please enter an ID.")

def check_station_active(station_type):
    """Check if required station is active. station_type = 'bio' or 'hyp'."""
    global bio_station_id, hyp_station_id
    if station_type == "bio":
        if not bio_station_id:
            print("  ❌ No active BIO station. Use 'station bio on' first.")
            return False
        return True
    elif station_type == "hyp":
        if not hyp_station_id:
            print("  ❌ No active HYP station. Use 'station hyp on' first.")
            return False
        return True
    return False

def get_station_name(station_type):
    """Get the full station name with ID."""
    global bio_station_id, hyp_station_id
    if station_type == "bio":
        return f"Vela_Lab_bio_st{bio_station_id}"
    elif station_type == "hyp":
        return f"Vela_Lab_hyp_st{hyp_station_id}"
    return ""

def terminal_event_listener():

    global motor_state, dessal_state
    global main_state, main_reef
    global jib_state, staysail_state, stormjib_state, spi_state
    global sea_state
    global hypernet_state, net_state, inline_state, filtration_state
    global ctd_keel_state, ctd_profile_state, ctd_intercomp_state
    global bio_station_id, hyp_station_id  

    def print_help():
        print("\n===================================")
        print("            COMMANDS")
        print("===================================")
        print("\n--- Navigation ---")
        print(" engine on/off")
        print(" engine check    (engine inspected)")
        print(" dessal on/off")
        print(" main on         (asks for reef)")
        print(" main off")
        print(" jib on/off")
        print(" staysail on/off")
        print(" stormjib on/off")
        print(" spi on/off")
        print(" sea X           (0-9)")
        print(" n: text         (navigation comment)")
        print(" s: text         (science comment)")
        print(" state           (show current state)")
        print("\n--- Science ---")
        print(" station bio on     (asks for ID)")
        print(" station bio off")
        print(" filtration on      (with active station)")
        print(" filtration off     (asks size + volume + saturation + planctospace)")
        print(" bucket")
        print(" net on/off")
        print(" station hyp on     (asks for ID)")
        print(" station hyp off")
        print(" hypernet on/off")
        print(" turbidity")
        print(" secchi")
        print(" ctd keel on/off")
        print(" ctd profile on/off")
        print(" ctd intercomp on/off")
        print("\n--- Backdated event ---")
        print(" EVENT_NAME YYYYMMDD HH:MM:SS")
        print("   e.g.: ENGINE ON 20240615 14:32:00")
        print("\n--- Help ---")
        print(" help")
        print("===================================\n")

    def print_state():
        print("\n===================================")
        print("          CURRENT STATE")
        print("===================================")
        print("--- Navigation ---")
        print(f" Engine    : {motor_state}")
        print(f" Dessal    : {dessal_state}")
        print(f" Main      : {main_state}" + (f"  (reef {main_reef})" if main_state == "ON" else ""))
        print(f" Jib       : {jib_state}")
        print(f" Staysail  : {staysail_state}")
        print(f" Storm jib : {stormjib_state}")
        print(f" Spi       : {spi_state}")
        print(f" Sea state : {sea_state}")
        print("--- Science ---")
        print(f" Hypernet     : {hypernet_state}")
        print(f" Net          : {net_state}")
        print(f" Inline       : {inline_state}")
        print(f" Filtration   : {filtration_state}")
        print(f" CTD keel     : {ctd_keel_state}")
        print(f" CTD profile  : {ctd_profile_state}")
        print(f" CTD intercomp: {ctd_intercomp_state}")
        print("===================================\n")

    print_help()

    while True:
        try:
            cmd = input("").strip()
            _flush_pending()
            cmd_lower = cmd.lower()

            # ---- Navigation ----
            if cmd_lower == "engine on":
                engine_on_sequence()

            elif cmd_lower == "engine off":
                motor_state = "OFF"
                write_event("ENGINE OFF")

            elif cmd_lower == "engine check":
                write_event("ENGINE CHECK")

            elif cmd_lower == "dessal on":     dessal_state = "ON";     write_event("DESSAL ON")
            elif cmd_lower == "dessal off":    dessal_state = "OFF";    write_event("DESSAL OFF")

            elif cmd_lower == "main on":
                main_state = "ON"
                reef = input("  Reef number (0-3) : ").strip()
                main_reef = reef
                write_event(f"MAIN ON | REEF {reef}")
            elif cmd_lower == "main off":
                main_state = "OFF"; write_event("MAIN OFF")

            elif cmd_lower == "jib on":       jib_state = "ON";       write_event("JIB ON")
            elif cmd_lower == "jib off":      jib_state = "OFF";      write_event("JIB OFF")
            elif cmd_lower == "staysail on":  staysail_state = "ON";  write_event("STAYSAIL ON")
            elif cmd_lower == "staysail off": staysail_state = "OFF"; write_event("STAYSAIL OFF")
            elif cmd_lower == "stormjib on":  stormjib_state = "ON";  write_event("STORMJIB ON")
            elif cmd_lower == "stormjib off": stormjib_state = "OFF"; write_event("STORMJIB OFF")
            elif cmd_lower == "spi on":       spi_state = "ON";       write_event("SPI ON")
            elif cmd_lower == "spi off":      spi_state = "OFF";      write_event("SPI OFF")

            elif cmd_lower.startswith("sea "):
                parts = cmd_lower.split()
                if len(parts) == 2 and parts[1].isdigit():
                    sea_state = parts[1]; write_event(f"SEA {parts[1]}")
                else:
                    print("Unknown command.")

            # ---- Navigation / science comments ----
            elif cmd_lower.startswith("n:"):
                text = cmd[2:].strip()
                if text:
                    write_event(f"NAV : {text}")
                else:
                    print("  Usage : n: your comment")

            elif cmd_lower.startswith("s:"):
                text = cmd[2:].strip()
                if text:
                    write_event(f"SCI : {text}")
                else:
                    print("  Usage : s: your comment")

            # ---- Science ----
                
                       # ---- Stations ----
                       
            elif cmd_lower == "station bio on":
                bio_station_id = ask_station_id("BIO")
                station_name = f"Vela_Lab_bio_st{bio_station_id}"
                write_event(f"STATION BIO ON | ID {station_name}")

            elif cmd_lower == "station bio off":
                if bio_station_id:
                    station_name = f"Vela_Lab_bio_st{bio_station_id}"
                    write_event(f"STATION BIO OFF | ID {station_name}")
                    bio_station_id = None
                else:
                    print("  No active BIO station.")

            elif cmd_lower == "station hyp on":
                hyp_station_id = ask_station_id("HYP")
                station_name = f"Vela_Lab_hyp_st{hyp_station_id}"
                write_event(f"STATION HYP ON | ID {station_name}")

            elif cmd_lower == "station hyp off":
                if hyp_station_id:
                    station_name = f"Vela_Lab_hyp_st{hyp_station_id}"
                    write_event(f"STATION HYP OFF | ID {station_name}")
                    hyp_station_id = None
                else:
                    print("  No active HYP station.")

            # ---- Science (BIO station) ----
            elif cmd_lower == "filtration on":
                if check_station_active("bio"):
                    station_name = get_station_name("bio")
                    size = filtration_on_sequence()
                    write_event(f"FILTRATION ON | STATION {station_name} | SIZE {size.upper()}")

            elif cmd_lower == "filtration off":
                if check_station_active("bio"):
                    station_name = get_station_name("bio")
                    size, volume, saturated = filtration_off_sequence()
                    
                    # Ask for planctospace station
                    while True:
                        ps_id = input("  Planctospace station (#06-XX, enter XX) : ").strip()
                        if ps_id:
                            break
                        print("  Please enter a planctospace station ID.")
                    
                    sat_str = "YES" if saturated else "NO"
                    write_event(
                        f"FILTRATION OFF | STATION {station_name} | SIZE {size.upper()} "
                        f"| VOLUME {volume:.1f} mL | SATURATION {sat_str} | PLANCTOSPACE st06-{ps_id}"
                    )

            elif cmd_lower == "bucket":
                if check_station_active("bio"):
                    station_name = get_station_name("bio")
                    write_event(f"BUCKET | STATION {station_name}")

            elif cmd_lower == "net on":
                if check_station_active("bio"):
                    station_name = get_station_name("bio")
                    net_state = "ON"
                    write_event(f"NET ON | STATION {station_name}")

            elif cmd_lower == "net off":
                if check_station_active("bio"):
                    station_name = get_station_name("bio")
                    net_state = "OFF"
                    write_event(f"NET OFF | STATION {station_name}")

            # ---- Science (HYP station) ----
            elif cmd_lower == "hypernet on":
                if check_station_active("hyp"):
                    station_name = get_station_name("hyp")
                    hypernet_state = "ON"
                    write_event(f"HYPERNET ON | STATION {station_name}")

            elif cmd_lower == "hypernet off":
                if check_station_active("hyp"):
                    station_name = get_station_name("hyp")
                    hypernet_state = "OFF"
                    write_event(f"HYPERNET OFF | STATION {station_name}")

            elif cmd_lower == "turbidity":
                if check_station_active("hyp"):
                    station_name = get_station_name("hyp")
                    vals = []
                    for i in range(1, 4):
                        while True:
                            try:
                                v = float(input(f"  Turbidity {i} : ").strip())
                                vals.append(v)
                                break
                            except ValueError:
                                print("  Please enter a numeric value.")
                    write_event(
                        f"TURBIDITY | STATION {station_name} | "
                        f"T1={vals[0]:.4f} | T2={vals[1]:.4f} | T3={vals[2]:.4f}"
                    )

            elif cmd_lower == "secchi":
                if check_station_active("hyp"):
                    station_name = get_station_name("hyp")
                    while True:
                        try:
                            depth = float(input("  Profondeur du disque Secchi (m) : ").strip())
                            if depth < 0: raise ValueError("must be >= 0")
                            break
                        except ValueError as e:
                            print(f"  Invalid value ({e}), try again.")
                    write_event(f"SECCHI | STATION {station_name} | DEPTH {depth:.2f} m")

            elif cmd_lower == "ctd keel on":
                ctd_keel_state = "ON"
                write_event("CTD KEEL ON")

            elif cmd_lower == "ctd keel off":
                ctd_keel_state = "OFF"
                write_event("CTD KEEL OFF")

            elif cmd_lower == "ctd profile on":
                ctd_profile_state = "ON"
                write_event("CTD PROFILE ON")

            elif cmd_lower == "ctd profile off":
                ctd_profile_state = "OFF"
                write_event("CTD PROFILE OFF")

            elif cmd_lower == "ctd intercomp on":
                ctd_intercomp_state = "ON"
                write_event("CTD INTERCOMP ON")

            elif cmd_lower == "ctd intercomp off":
                ctd_intercomp_state = "OFF"
                write_event("CTD INTERCOMP OFF")

            elif cmd_lower == "inline on":          inline_state = "ON";      write_event("INLINE ON")
            elif cmd_lower == "inline off":         inline_state = "OFF";     write_event("INLINE OFF")

            elif cmd_lower == "state":
                print_state()

            elif cmd_lower == "help":
                print_help()

            elif cmd_lower == "":
                pass

            else:
                retro = try_parse_retrodate(cmd)
                if retro:
                    event_name, ts = retro
                    write_event(event_name, timestamp=ts)
                else:
                    print("Unknown command. Type 'help' for the list.")

        except EOFError:
            break
        except Exception as e:
            if not _shutdown_requested:
                print(f"Terminal error: {e}", flush=True)

# =========================================================
# MAIN
# =========================================================

def main():
    global latest_fix, event_log_file
    global last_position
    global distance_traveled_nm, initial_distance_nm
    global sea_state, ctd_keel_state

    connect_wifi()
    setup = navigation_setup()

    sea_state      = setup["sea"]
    ctd_keel_state = setup["ctd_keel"]

    threading.Thread(target=terminal_event_listener, daemon=True).start()

    print("Waiting for GPS fix…")
    while latest_fix is None and not _shutdown_requested:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(10)
                s.connect((HOST, PORT))
                while latest_fix is None and not _shutdown_requested:
                    data = s.recv(2048).decode(errors="ignore")
                    for line in data.split("\n"):
                        fix = parse_rmc(line.strip())
                        if fix:
                            set_fix(fix); break
        except Exception:
            if not _shutdown_requested:
                time.sleep(2)

    if _shutdown_requested:
        return

    # ---------- create log file ----------
    ts     = latest_fix["datetime"].strftime("%Y%m%d_%H%M%S")
    rlat   = latest_fix["lat"]; rlon = latest_fix["lon"]
    ldir   = "N" if rlat >= 0 else "S"; alat = abs(rlat); dlt = int(alat); mlt = (alat-dlt)*60
    londir = "E" if rlon >= 0 else "W"; alon = abs(rlon); dln = int(alon); mln = (alon-dln)*60
    ln     = f"nmea_{ts}_{dlt:02d}{mlt:07.4f}{ldir}_{dln:03d}{mln:07.4f}{londir}"
    ld     = os.path.join(base_folder, ln)
    os.makedirs(ld, exist_ok=True)
    output_file    = os.path.join(ld, f"{ln}.txt")
    event_log_file = output_file
    print(f"Log: {output_file}")

    def yn(b): return "OK" if b else "NOK"
    fd = setup["fuel_data"]

    with open(output_file, "w") as f:
        f.write("====================================\n")
        f.write("         NAVIGATION LOG\n")
        f.write("====================================\n\n")
        f.write(f"MODE : {setup['mode']}\n")
        f.write(f"SKIPPER : {setup['skipper']}\n")
        f.write(f"CREW : {setup['crew']}\n")
        f.write(f"DEPARTURE : {setup['departure']}\n")
        f.write(f"DESTINATION : {setup['destination']}\n")
        f.write(f"SEA : {setup['sea']}\n")

        f.write("\n--- ENGINE CONTROL CHECK ---\n")
        if fd is not None:
            eg = setup["engine"]
            f.write(f"LAST FILL (L)                        : {fd['last_fill_l']:.0f} L\n")
            f.write(f"ENGINE HOURS SINCE LAST FILL (h)     : {fd['engine_hours']:.4f} h\n")
            f.write(f"ESTIMATE 1 (engine hours)            : {fd['remaining_est1']:.1f} L  ({fd['pct_est1']:.1f} %)\n")
            f.write(f"GAUGE READING                        : {fd['gauge_pct']:.0f} %\n")
            f.write(f"ESTIMATE 2 (gauge)                   : {fd['remaining_est2']:.1f} L  ({fd['pct_est2']:.1f} %)\n")
            f.write(f"FUEL PRE-FILTER     : {yn(eg['prefil_ok'])}\n")
            f.write(f"SEA WATER FILTER    : {yn(eg['seawater_ok'])}\n")
            f.write(f"PRIMING BULB        : {'FIRM' if eg['priming_ok'] else 'SOFT'}\n")
            f.write(f"ENGINE BILGE (AFT)  : {yn(eg['bilge_ok_rear'])}\n")
            f.write(f"COOLANT             : {yn(eg['coolant_ok'])}\n")
            f.write(f"SEA COCK + IGNITION : {yn(eg['seacock_ok'])}\n")
            f.write(f"BELT                : {yn(eg['belt_ok'])}\n")
            f.write(f"ENGINE BILGE (FWD)  : {yn(eg['bilge_ok_front'])}\n")
        else:
            f.write("(not done - mooring)\n")

        f.write("\n--- SCIENCE ---\n")
        f.write(f"CTD KEEL : {setup['ctd_keel']}\n")

        f.write("\n--- END OF TRIP CONTROL ---\n")
        f.write("(filled at arrival)\n")

        f.write("\n====================================\n\n")

    # --- Events initiaux après création du fichier log ---
    if setup["ctd_keel"] == "ON":
        write_event("CTD KEEL ON")

    # FIX: engine on au setup → écrit l'event ENGINE ON maintenant que le fichier existe
    if setup["engine_start_now"]:
        engine_on_sequence()

    # ---------- main NMEA loop ----------
    last_display = time.time()

    while not _shutdown_requested:
        try:
            print("Connecting to NMEA…")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(10)
                s.connect((HOST, PORT))
                print("NMEA connected.")

                while not _shutdown_requested:
                    data = s.recv(2048).decode(errors="ignore")
                    if not data:
                        raise Exception("Connection lost")

                    for line in data.split("\n"):
                        line = line.strip()
                        if not line.startswith("$"):
                            continue

                        with open(output_file, "a") as f:
                            f.write(line + "\n")

                        fix = parse_rmc(line)
                        if fix:
                            set_fix(fix)
                            lat = fix["lat"]; lon = fix["lon"]

                            if last_position:
                                d = haversine_nm(last_position[0], last_position[1], lat, lon)
                                if d < 1:
                                    distance_traveled_nm += d
                            last_position = (lat, lon)

                    now = time.time()
                    if now - last_display > 30:
                        if latest_fix:
                            # Heure GPS (l'horloge du Pi peut être fausse en mer)
                            ts_now = now_utc_str()
                            ls  = decimal_to_deg_min(latest_fix["lat"], is_lon=False)
                            lo  = decimal_to_deg_min(latest_fix["lon"], is_lon=True)
                            _buffered_print(
                                f"[{ts_now}] {ls}  {lo}  DIST={distance_traveled_nm:.1f} nm"
                            )
                        last_display = now

        except Exception as e:
            if _shutdown_requested:
                break
            print(f"\nDisconnected: {e}", flush=True)
            print("Reconnecting in 5 s…\n", flush=True)
            for _ in range(50):
                if _shutdown_requested:
                    break
                time.sleep(0.1)

    if not _shutdown_requested:
        _ask_arrival_and_close()


if __name__ == "__main__":
    main()