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
from datetime import datetime

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

distance_traveled_nm = 0
initial_distance_nm  = None
last_position        = None

motor_state     = "OFF"
dessal_state    = "OFF"
spinnaker_state = "OFF"

main_state     = "OFF"
main_reef      = 0
jib_state      = "OFF"
staysail_state = "OFF"
stormjib_state = "OFF"

sea_state = "0"

hypernet_state      = "OFF"
net_state           = "OFF"
inline_state        = "OFF"
ctd_keel_state      = "OFF"
ctd_profile_state   = "OFF"
ctd_intercomp_state = "OFF"

# Flag for clean shutdown — set by SIGINT handler
_shutdown_requested = False

# =========================================================
# DISPLAY BUFFER — évite que les logs 30s interrompent la saisie
# =========================================================

_display_lock    = threading.Lock()
_pending_display = []   # lignes en attente
_user_typing     = False

def _flush_pending():
    """Affiche les lignes mises en buffer (appelé après un Enter)."""
    with _display_lock:
        if _pending_display:
            print()
            for line in _pending_display:
                print(line)
            _pending_display.clear()

def _buffered_print(line):
    """Appelé depuis le thread NMEA pour afficher sans interrompre la saisie."""
    with _display_lock:
        _pending_display.append(line)

# =========================================================
# SIGNAL HANDLER — clean Ctrl+C at any phase
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

    if event_log_file and os.path.exists(event_log_file):
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
# EVENT
# =========================================================

def write_event(event, timestamp=None):
    """Écrit un event dans le log. Si timestamp fourni, il est utilisé à la place de 'now'."""
    global event_log_file

    if timestamp:
        ts_str = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"# EVENT [{ts_str}] : {event}"
    else:
        line = f"# EVENT : {event}"

    print(f"\n{line}\n")

    try:
        if event_log_file:
            with open(event_log_file, "a") as f:
                f.write(line + "\n")
    except Exception as e:
        print(f"Event write error: {e}")

# =========================================================
# RETRODATE EVENT
# =========================================================

# Pattern : EVENT_NAME YYYYMMDD HH:MM:SS
# ex: ENGINE ON 20240615 14:32:00
_RETRODATE_RE = re.compile(
    r"^(.+?)\s+(\d{8})\s+(\d{2}:\d{2}:\d{2})$"
)

def try_parse_retrodate(cmd):
    """
    Essaie de parser une commande rétrodatée.
    Retourne (event_name, datetime) ou None si pas de match.
    """
    m = _RETRODATE_RE.match(cmd.strip())
    if not m:
        return None
    event_name = m.group(1).upper().strip()
    date_str   = m.group(2)   # YYYYMMDD
    time_str   = m.group(3)   # HH:MM:SS
    try:
        ts = datetime.strptime(date_str + " " + time_str, "%Y%m%d %H:%M:%S")
        return (event_name, ts)
    except ValueError:
        return None


# =========================================================
# SETUP
# =========================================================

def navigation_setup():
    global sea_state, ctd_keel_state

    # ---------------------------------------------------
    # PART 1 — NAVIGATION
    # ---------------------------------------------------
    print("\n===================================")
    print("       PART 1 — NAVIGATION")
    print("===================================\n")

    skipper     = input("Skipper : ").strip()
    crew        = input("Crew : ").strip()
    departure   = input("Departure port : ").strip()
    destination = input("Destination port : ").strip()

    print("\nAccepted lat/lon formats:")
    print("  43°21.456'N   |   43 21.456 N   |   4321.456N   |   43.3576")

    while True:
        try:
            raw = input("Destination latitude  (e.g. 43 21.456 N) : ").strip()
            destination_lat = parse_deg_min(raw)
            print(f"  -> {decimal_to_deg_min(destination_lat, is_lon=False)}")
            break
        except ValueError as e:
            print(f"  Unrecognised format ({e}), try again.")

    while True:
        try:
            raw = input("Destination longitude (e.g. 005 22.123 E) : ").strip()
            destination_lon = parse_deg_min(raw)
            print(f"  -> {decimal_to_deg_min(destination_lon, is_lon=True)}")
            break
        except ValueError as e:
            print(f"  Unrecognised format ({e}), try again.")

    # Direct leg or waypoints?
    direct_str = input("\nDirect leg to destination? (Y/N) : ").strip().upper()
    direct_leg = (direct_str == "Y")

    manual_distance_nm = None
    if not direct_leg:
        while True:
            try:
                manual_distance_nm = float(input("Total distance to sail (nm) : ").strip())
                if manual_distance_nm <= 0: raise ValueError("must be > 0")
                break
            except ValueError as e:
                print(f"  Invalid value ({e}), try again.")

    sea_val = input("\nSea state (0-9) : ").strip()

    # ---------------------------------------------------
    # PART 2 — ENGINE CONTROL CHECK
    # ---------------------------------------------------
    print("\n===================================")
    print("    PART 2 — ENGINE CONTROL CHECK")
    print("===================================\n")

    def ask_yn(question):
        while True:
            ans = input(f"{question} (Y/N) : ").strip().upper()
            if ans in ("Y", "N"):
                return ans == "Y"
            print("  Please answer Y or N.")

    while True:
        try:
            fuel_pct = float(input("Fuel level (0-100 %) : ").strip())
            if not (0 <= fuel_pct <= 100): raise ValueError("out of [0-100]")
            break
        except ValueError as e:
            print(f"  Invalid value ({e}), try again.")

    prefil_ok   = ask_yn("Fuel pre-filter clean")
    seawater_ok = ask_yn("Sea water filter clean")
    bilge_ok    = ask_yn("Engine bilge dry and clean")

    while True:
        try:
            oil_pct = float(input("Oil level (0-100 %) : ").strip())
            if not (0 <= oil_pct <= 100): raise ValueError("out of [0-100]")
            break
        except ValueError as e:
            print(f"  Invalid value ({e}), try again.")

    coolant_ok  = ask_yn("Coolant level OK")
    belt_ok     = ask_yn("Belt tension OK (~1 cm deflection under thumb pressure)")
    seacock_ok  = ask_yn("Sea cock open and ignition on")

    # ---------------------------------------------------
    # PART 3 — BOAT CONTROL CHECK
    # ---------------------------------------------------
    print("\n===================================")
    print("    PART 3 — BOAT CONTROL CHECK")
    print("===================================\n")

    bilges_dry    = ask_yn("Bilges dry")
    portholes_ok  = ask_yn("Portholes closed")          # hublots fermés
    seacocks_ok   = ask_yn("Sea cocks closed")           # passes-coques fermés
    boat_stowed   = ask_yn("Boat stowed and secured")

    # ---------------------------------------------------
    # PART 4 — SCIENCE
    # ---------------------------------------------------
    print("\n===================================")
    print("         PART 4 — SCIENCE")
    print("===================================\n")

    ctd_keel = "ON" if input("CTD keel ON? (Y/N) : ").strip().upper() == "Y" else "OFF"

    print("\n===================================\n")

    return {
        "skipper"           : skipper,
        "crew"              : crew,
        "departure"         : departure,
        "destination"       : destination,
        "destination_lat"   : destination_lat,
        "destination_lon"   : destination_lon,
        "direct_leg"        : direct_leg,
        "manual_distance_nm": manual_distance_nm,
        "sea"               : sea_val,
        # Engine check
        "fuel_pct"          : fuel_pct,
        "prefil_ok"         : prefil_ok,
        "seawater_ok"       : seawater_ok,
        "bilge_ok"          : bilge_ok,
        "oil_pct"           : oil_pct,
        "coolant_ok"        : coolant_ok,
        "belt_ok"           : belt_ok,
        "seacock_ok"        : seacock_ok,
        # Boat check
        "bilges_dry"        : bilges_dry,
        "portholes_ok"      : portholes_ok,
        "seacocks_ok"       : seacocks_ok,
        "boat_stowed"       : boat_stowed,
        # Science
        "ctd_keel"          : ctd_keel,
    }

# =========================================================
# TERMINAL EVENTS
# =========================================================

def terminal_event_listener():

    global motor_state, dessal_state, spinnaker_state
    global main_state, main_reef
    global jib_state, staysail_state, stormjib_state
    global sea_state
    global hypernet_state, net_state, inline_state
    global ctd_keel_state, ctd_profile_state, ctd_intercomp_state

    def ask_yn_inline(question):
        while True:
            ans = input(f"  {question} (Y/N) : ").strip().upper()
            if ans in ("Y", "N"):
                return ans == "Y"
            print("  Please answer Y or N.")

    def print_help():
        print("\n===================================")
        print("            COMMANDS")
        print("===================================")
        print("\n--- Navigation ---")
        print(" engine on/off")
        print(" engine check    (event moteur inspecté)")
        print(" dessal on/off")
        print(" spinnaker on/off")
        print(" main on         (asks for reef)")
        print(" main off")
        print(" jib on/off")
        print(" staysail on/off")
        print(" stormjib on/off")
        print(" sea X           (0-9)")
        print(" n: texte        (commentaire navigation)")
        print(" s: texte        (commentaire science)")
        print(" state           (show current state)")
        print("\n--- Science ---")
        print(" hypernet on/off")
        print(" net on/off")
        print(" inline on/off")
        print(" ctd keel on/off")
        print(" ctd profile on/off")
        print(" ctd intercomp on/off")
        print("\n--- Event rétrodaté ---")
        print(" NOM_EVENT YYYYMMDD HH:MM:SS")
        print("   ex: ENGINE ON 20240615 14:32:00")
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
        print(f" Spinnaker : {spinnaker_state}")
        print(f" Sea state : {sea_state}")
        print("--- Science ---")
        print(f" Hypernet     : {hypernet_state}")
        print(f" Net          : {net_state}")
        print(f" Inline       : {inline_state}")
        print(f" CTD keel     : {ctd_keel_state}")
        print(f" CTD profile  : {ctd_profile_state}")
        print(f" CTD intercomp: {ctd_intercomp_state}")
        print("===================================\n")

    print_help()

    while True:
        try:
            # Flush les lignes de log en attente après chaque Enter
            cmd = input("").strip()
            _flush_pending()
            cmd_lower = cmd.lower()

            # ---- Navigation ----
            if   cmd_lower == "engine on":
                # Demande confirmation eau de refroidissement
                cooling_ok = ask_yn_inline("Eau de refroidissement crachée à l'extérieur")
                motor_state = "ON"
                write_event(f"ENGINE ON | COOLING WATER {'OK' if cooling_ok else 'NOK'}")

            elif cmd_lower == "engine off":
                motor_state = "OFF";     write_event("ENGINE OFF")

            elif cmd_lower == "engine check":
                write_event("ENGINE CHECK")

            elif cmd_lower == "dessal on":     dessal_state = "ON";     write_event("DESSAL ON")
            elif cmd_lower == "dessal off":    dessal_state = "OFF";    write_event("DESSAL OFF")
            elif cmd_lower == "spinnaker on":  spinnaker_state = "ON";  write_event("SPINNAKER ON")
            elif cmd_lower == "spinnaker off": spinnaker_state = "OFF"; write_event("SPINNAKER OFF")

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

            elif cmd_lower.startswith("sea "):
                parts = cmd_lower.split()
                if len(parts) == 2 and parts[1].isdigit():
                    sea_state = parts[1]; write_event(f"SEA {parts[1]}")
                else:
                    print("Unknown command.")

            # ---- Commentaires nav / science ----
            elif cmd_lower.startswith("n:"):
                text = cmd[2:].strip()
                if text:
                    write_event(f"NAV : {text}")
                else:
                    print("  Usage : n: votre commentaire")

            elif cmd_lower.startswith("s:"):
                text = cmd[2:].strip()
                if text:
                    write_event(f"SCI : {text}")
                else:
                    print("  Usage : s: votre commentaire")

            # ---- Science ----
            elif cmd_lower == "ctd keel on":        ctd_keel_state = "ON";        write_event("CTD KEEL ON")
            elif cmd_lower == "ctd keel off":       ctd_keel_state = "OFF";       write_event("CTD KEEL OFF")
            elif cmd_lower == "ctd profile on":     ctd_profile_state = "ON";     write_event("CTD PROFILE ON")
            elif cmd_lower == "ctd profile off":    ctd_profile_state = "OFF";    write_event("CTD PROFILE OFF")
            elif cmd_lower == "ctd intercomp on":   ctd_intercomp_state = "ON";   write_event("CTD INTERCOMP ON")
            elif cmd_lower == "ctd intercomp off":  ctd_intercomp_state = "OFF";  write_event("CTD INTERCOMP OFF")

            elif cmd_lower == "hypernet on":   hypernet_state = "ON";  write_event("HYPERNET ON")
            elif cmd_lower == "hypernet off":  hypernet_state = "OFF"; write_event("HYPERNET OFF")
            elif cmd_lower == "net on":        net_state = "ON";       write_event("NET ON")
            elif cmd_lower == "net off":       net_state = "OFF";      write_event("NET OFF")
            elif cmd_lower == "inline on":     inline_state = "ON";    write_event("INLINE ON")
            elif cmd_lower == "inline off":    inline_state = "OFF";   write_event("INLINE OFF")

            # ---- Etat / help ----
            elif cmd_lower == "state":
                print_state()

            elif cmd_lower == "help":
                print_help()

            elif cmd_lower == "":
                pass   # silent blank line

            else:
                # ---- Tentative event rétrodaté ----
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
                print(f"Terminal error: {e}")

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

    # ---------- wait for first GPS fix ----------
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
                            latest_fix = fix; break
        except Exception:
            if not _shutdown_requested:
                time.sleep(2)

    if _shutdown_requested:
        return

    # Distance to destination
    calculated_distance_nm = haversine_nm(
        latest_fix["lat"], latest_fix["lon"],
        setup["destination_lat"], setup["destination_lon"]
    )

    if setup["direct_leg"]:
        initial_distance_nm = calculated_distance_nm
        dist_note = "direct leg (calculated from GPS fix)"
    else:
        initial_distance_nm = setup["manual_distance_nm"]
        dist_note = f"manual entry (calculated: {calculated_distance_nm:.1f} nm)"

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

    dst_lat_str = decimal_to_deg_min(setup["destination_lat"], is_lon=False)
    dst_lon_str = decimal_to_deg_min(setup["destination_lon"], is_lon=True)

    def yn(b): return "OK" if b else "NOK"

    with open(output_file, "w") as f:
        f.write("====================================\n")
        f.write("         NAVIGATION LOG\n")
        f.write("====================================\n\n")
        f.write(f"SKIPPER : {setup['skipper']}\n")
        f.write(f"CREW : {setup['crew']}\n")
        f.write(f"DEPARTURE : {setup['departure']}\n")
        f.write(f"DESTINATION : {setup['destination']}\n")
        f.write(f"DESTINATION LAT : {dst_lat_str}\n")
        f.write(f"DESTINATION LON : {dst_lon_str}\n")
        f.write(f"INITIAL DISTANCE : {initial_distance_nm:.1f}\n")
        f.write(f"DIRECT LEG : {'YES' if setup['direct_leg'] else 'NO'} ({dist_note})\n")
        f.write(f"SEA : {setup['sea']}\n")
        f.write("\n--- ENGINE CONTROL CHECK ---\n")
        f.write(f"FUEL : {setup['fuel_pct']:.0f} %\n")
        f.write(f"FUEL PRE-FILTER : {yn(setup['prefil_ok'])}\n")
        f.write(f"SEA WATER FILTER : {yn(setup['seawater_ok'])}\n")
        f.write(f"ENGINE BILGE : {yn(setup['bilge_ok'])}\n")
        f.write(f"OIL : {setup['oil_pct']:.0f} %\n")
        f.write(f"COOLANT : {yn(setup['coolant_ok'])}\n")
        f.write(f"BELT : {yn(setup['belt_ok'])}\n")
        f.write(f"SEA COCK + IGNITION : {yn(setup['seacock_ok'])}\n")
        f.write("\n--- BOAT CONTROL CHECK ---\n")
        f.write(f"BILGES DRY : {yn(setup['bilges_dry'])}\n")
        f.write(f"PORTHOLES CLOSED : {yn(setup['portholes_ok'])}\n")
        f.write(f"SEA COCKS CLOSED : {yn(setup['seacocks_ok'])}\n")
        f.write(f"BOAT STOWED : {yn(setup['boat_stowed'])}\n")
        f.write("\n--- SCIENCE ---\n")
        f.write(f"CTD KEEL : {setup['ctd_keel']}\n")
        f.write("\n====================================\n\n")

    if setup["ctd_keel"] == "ON":
        write_event("CTD KEEL ON")

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
                            latest_fix = fix
                            lat = fix["lat"]; lon = fix["lon"]

                            if last_position:
                                d = haversine_nm(last_position[0], last_position[1], lat, lon)
                                if d < 1:
                                    distance_traveled_nm += d
                            last_position = (lat, lon)

                    # 30-second display — mis en buffer, affiché au prochain Enter
                    now = time.time()
                    if now - last_display > 30:
                        if latest_fix:
                            ts_now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                            ls  = decimal_to_deg_min(latest_fix["lat"], is_lon=False)
                            lo  = decimal_to_deg_min(latest_fix["lon"], is_lon=True)
                            rem = haversine_nm(
                                latest_fix["lat"], latest_fix["lon"],
                                setup["destination_lat"], setup["destination_lon"]
                            )
                            _buffered_print(
                                f"[{ts_now}] {ls}  {lo}  REM={rem:.1f} nm  DIST={distance_traveled_nm:.1f} nm"
                            )
                        last_display = now

        except Exception as e:
            if _shutdown_requested:
                break
            print(f"\nDisconnected: {e}")
            print("Reconnecting in 5 s…\n")
            for _ in range(50):
                if _shutdown_requested:
                    break
                time.sleep(0.1)

    if not _shutdown_requested:
        _ask_arrival_and_close()


if __name__ == "__main__":
    main()
