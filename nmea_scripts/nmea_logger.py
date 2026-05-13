#!/usr/bin/env python3

import socket
import time
import subprocess
import gzip
import shutil
import os
import threading
import math
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
next_milestone       = 5
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

# =========================================================
# WIFI
# =========================================================

def connect_wifi():
    print("Connecting WiFi...")
    try:
        subprocess.run(
            ["nmcli", "dev", "wifi", "connect", WIFI_SSID, "password", WIFI_PASSWORD],
            check=True
        )
        print("WiFi connected")
    except Exception as e:
        print(f"WiFi error : {e}")

# =========================================================
# COMPRESSION
# =========================================================

def compress_file(file_path):
    gz_path = file_path + ".gz"
    with open(file_path, "rb") as f_in:
        with gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.remove(file_path)
    print(f"Compressed : {gz_path}")

# =========================================================
# FORMAT deg-min.decimal
# =========================================================

def parse_deg_min(text):
    """
    Accepte :
      43 21.456 N  |  4321.456N  |  43.3576  |  -43.3576
    Retourne un float decimal.
    """
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
        if val > 360:                   # format DDDMM.mmm
            deg = int(val // 100)
            dec = deg + (val - deg * 100) / 60.0
        else:
            dec = val
    elif len(parts) == 2:
        dec = int(float(parts[0])) + float(parts[1]) / 60.0
    else:
        raise ValueError(f"Format non reconnu : {text}")

    if direction in ["S", "W"]: dec = -abs(dec)
    elif direction in ["N", "E"]: dec = abs(dec)
    return dec


def decimal_to_deg_min(decimal, is_lon=False):
    """
    40.9986  ->  40deg59.916'N
    9.6213   ->  009deg37.278'E
    """
    if decimal is None or (isinstance(decimal, float) and math.isnan(decimal)):
        return "---"
    direction = ("E" if decimal >= 0 else "W") if is_lon else ("N" if decimal >= 0 else "S")
    dw        = 3 if is_lon else 2
    a         = abs(decimal)
    deg       = int(a)
    mn        = (a - deg) * 60.0
    return f"{deg:0{dw}d}deg{mn:07.4f}'{direction}"

# =========================================================
# NMEA -> DECIMAL
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

def write_event(event):
    global event_log_file
    line = f"# EVENT : {event}"
    print(f"\n{line}\n")
    try:
        if event_log_file:
            with open(event_log_file, "a") as f:
                f.write(line + "\n")
    except Exception as e:
        print(f"Event error : {e}")

# =========================================================
# NAVIGATION SETUP
# =========================================================

def navigation_setup():
    global motor_state, dessal_state, sea_state, ctd_keel_state

    print("\n===================================")
    print("        START NAVIGATION")
    print("===================================\n")

    skipper     = input("Skipper : ").strip()
    crew        = input("Crew : ").strip()
    departure   = input("Departure : ").strip()
    destination = input("Destination : ").strip()

    # Lat/lon destination en deg-min.decimal
    print("\nFormats acceptes pour lat/lon :")
    print("  43°21.456'N        (degres-minutes avec symboles)")
    print("  43 21.456 N        (degres minutes direction separes)")
    print("  4321.456 N         (format NMEA brut)")
    print("  43.3576            (degres decimaux, positif=N/E)")
    while True:
        try:
            raw = input("Destination latitude  (ex: 43 21.456 N) : ").strip()
            destination_lat = parse_deg_min(raw)
            print(f"  -> {decimal_to_deg_min(destination_lat, is_lon=False)}")
            break
        except ValueError as e:
            print(f"  Format non reconnu ({e}), reessayez.")

    while True:
        try:
            raw = input("Destination longitude (ex: 005 22.123 E) : ").strip()
            destination_lon = parse_deg_min(raw)
            print(f"  -> {decimal_to_deg_min(destination_lon, is_lon=True)}")
            break
        except ValueError as e:
            print(f"  Format non reconnu ({e}), reessayez.")

    # Gasoil
    print()
    while True:
        try:
            fuel_pct = float(input("Gasoil (%) : ").strip())
            if not (0 <= fuel_pct <= 100): raise ValueError("hors [0-100]")
            break
        except ValueError as e:
            print(f"  Valeur invalide ({e}), reessayez.")

    # Navigation
    print()
    motor_state  = "ON" if input("Engine ON ? (Y/N) : ").strip().upper() == "Y" else "OFF"
    dessal_state = "ON" if input("Dessal ON ? (Y/N) : ").strip().upper() == "Y" else "OFF"
    sea_state    = input("Sea state (0-9) : ").strip()

    # Science
    print("\n-----------------------------------")
    print("         SCIENCE SETUP")
    print("-----------------------------------\n")
    ctd_keel_state = "ON" if input("CTD keel ON ? (Y/N) : ").strip().upper() == "Y" else "OFF"

    print("\n===================================\n")

    return {
        "skipper"         : skipper,
        "crew"            : crew,
        "fuel_pct"        : fuel_pct,
        "departure"       : departure,
        "destination"     : destination,
        "destination_lat" : destination_lat,
        "destination_lon" : destination_lon,
        "engine"          : motor_state,
        "dessal"          : dessal_state,
        "sea"             : sea_state,
        "ctd_keel"        : ctd_keel_state,
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

    def print_help():
        print("\n===================================")
        print("           COMMANDS")
        print("===================================")
        print("\n--- Navigation ---")
        print(" engine on/off")
        print(" dessal on/off")
        print(" spinnaker on/off")
        print(" main on         (demande le reef)")
        print(" main off")
        print(" jib on/off")
        print(" staysail on/off")
        print(" stormjib on/off")
        print(" sea X           (0-9)")
        print(" comment: texte libre")
        print("\n--- Science ---")
        print(" hypernet on/off")
        print(" net on/off")
        print(" inline on/off")
        print(" ctd keel on/off")
        print(" ctd profile on/off")
        print(" ctd intercomp on/off")
        print("\n--- Aide ---")
        print(" help")
        print("===================================\n")

    print_help()

    while True:
        try:
            cmd = input("> ").strip().lower()

            if   cmd == "engine on":     motor_state = "ON";      write_event("ENGINE ON")
            elif cmd == "engine off":    motor_state = "OFF";     write_event("ENGINE OFF")
            elif cmd == "dessal on":     dessal_state = "ON";     write_event("DESSAL ON")
            elif cmd == "dessal off":    dessal_state = "OFF";    write_event("DESSAL OFF")
            elif cmd == "spinnaker on":  spinnaker_state = "ON";  write_event("SPINNAKER ON")
            elif cmd == "spinnaker off": spinnaker_state = "OFF"; write_event("SPINNAKER OFF")

            elif cmd == "main on":
                main_state = "ON"
                reef = input("Reef number (0-3) : ").strip()
                main_reef = reef
                write_event(f"MAIN ON | REEF {reef}")
            elif cmd == "main off":
                main_state = "OFF"; write_event("MAIN OFF")

            elif cmd == "jib on":       jib_state = "ON";       write_event("JIB ON")
            elif cmd == "jib off":      jib_state = "OFF";      write_event("JIB OFF")
            elif cmd == "staysail on":  staysail_state = "ON";  write_event("STAYSAIL ON")
            elif cmd == "staysail off": staysail_state = "OFF"; write_event("STAYSAIL OFF")
            elif cmd == "stormjib on":  stormjib_state = "ON";  write_event("STORMJIB ON")
            elif cmd == "stormjib off": stormjib_state = "OFF"; write_event("STORMJIB OFF")

            elif cmd.startswith("sea "):
                parts = cmd.split()
                if len(parts) == 2 and parts[1].isdigit():
                    sea_state = parts[1]; write_event(f"SEA {parts[1]}")
                else:
                    print("Unknown command")

            elif cmd == "ctd keel on":       ctd_keel_state = "ON";       write_event("CTD KEEL ON")
            elif cmd == "ctd keel off":      ctd_keel_state = "OFF";      write_event("CTD KEEL OFF")
            elif cmd == "ctd profile on":    ctd_profile_state = "ON";    write_event("CTD PROFILE ON")
            elif cmd == "ctd profile off":   ctd_profile_state = "OFF";   write_event("CTD PROFILE OFF")
            elif cmd == "ctd intercomp on":  ctd_intercomp_state = "ON";  write_event("CTD INTERCOMP ON")
            elif cmd == "ctd intercomp off": ctd_intercomp_state = "OFF"; write_event("CTD INTERCOMP OFF")

            elif cmd == "hypernet on":  hypernet_state = "ON";  write_event("HYPERNET ON")
            elif cmd == "hypernet off": hypernet_state = "OFF"; write_event("HYPERNET OFF")
            elif cmd == "net on":       net_state = "ON";       write_event("NET ON")
            elif cmd == "net off":      net_state = "OFF";      write_event("NET OFF")
            elif cmd == "inline on":    inline_state = "ON";    write_event("INLINE ON")
            elif cmd == "inline off":   inline_state = "OFF";   write_event("INLINE OFF")

            elif cmd.startswith("comment:"):
                write_event("COMMENT : " + cmd.replace("comment:", "").strip())

            elif cmd == "help":
                print_help()
            else:
                print("Unknown command")

        except Exception as e:
            print(f"Terminal error : {e}")

# =========================================================
# MAIN
# =========================================================

def main():
    global latest_fix, event_log_file
    global last_position
    global distance_traveled_nm, next_milestone, initial_distance_nm

    connect_wifi()
    setup = navigation_setup()

    threading.Thread(target=terminal_event_listener, daemon=True).start()

    print("Waiting GPS fix...")
    while latest_fix is None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(10)
                s.connect((HOST, PORT))
                while latest_fix is None:
                    data = s.recv(2048).decode(errors="ignore")
                    for line in data.split("\n"):
                        fix = parse_rmc(line.strip())
                        if fix:
                            latest_fix = fix; break
        except:
            time.sleep(2)

    initial_distance_nm = haversine_nm(
        latest_fix["lat"], latest_fix["lon"],
        setup["destination_lat"], setup["destination_lon"]
    )

    # Nom fichier log
    ts      = latest_fix["datetime"].strftime("%Y%m%d_%H%M%S")
    rlat    = latest_fix["lat"];  rlon = latest_fix["lon"]
    ldir    = "N" if rlat >= 0 else "S"; alat = abs(rlat); dlt = int(alat); mlt = (alat - dlt) * 60
    londir  = "E" if rlon >= 0 else "W"; alon = abs(rlon); dln = int(alon); mln = (alon - dln) * 60
    ln      = f"nmea_{ts}_{dlt:02d}{mlt:07.4f}{ldir}_{dln:03d}{mln:07.4f}{londir}"
    ld      = os.path.join(base_folder, ln)
    os.makedirs(ld, exist_ok=True)
    output_file    = os.path.join(ld, f"{ln}.txt")
    event_log_file = output_file
    print(f"Log : {output_file}")

    dst_lat_str = decimal_to_deg_min(setup["destination_lat"], is_lon=False)
    dst_lon_str = decimal_to_deg_min(setup["destination_lon"], is_lon=True)

    with open(output_file, "w") as f:
        f.write("====================================\n")
        f.write("         NAVIGATION LOG\n")
        f.write("====================================\n\n")
        f.write(f"SKIPPER : {setup['skipper']}\n")
        f.write(f"CREW : {setup['crew']}\n")
        f.write(f"FUEL : {setup['fuel_pct']:.0f}%\n")
        f.write(f"DEPARTURE : {setup['departure']}\n")
        f.write(f"DESTINATION : {setup['destination']}\n")
        f.write(f"DESTINATION LAT : {dst_lat_str}\n")
        f.write(f"DESTINATION LON : {dst_lon_str}\n")
        f.write(f"INITIAL DISTANCE : {initial_distance_nm:.1f} nm\n")
        f.write(f"ENGINE : {setup['engine']}\n")
        f.write(f"DESSAL : {setup['dessal']}\n")
        f.write(f"SEA : {setup['sea']}\n")
        f.write(f"CTD KEEL : {setup['ctd_keel']}\n")
        f.write("\n====================================\n\n")

    if setup["ctd_keel"] == "ON":
        write_event("CTD KEEL ON")

    while True:
        try:
            print("Connecting NMEA...")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(10)
                s.connect((HOST, PORT))
                print("NMEA connected")
                last_display = time.time()

                while True:
                    data = s.recv(2048).decode(errors="ignore")
                    if not data: raise Exception("Connection lost")

                    for line in data.split("\n"):
                        line = line.strip()
                        if not line.startswith("$"): continue

                        with open(output_file, "a") as f:
                            f.write(line + "\n")

                        fix = parse_rmc(line)
                        if fix:
                            latest_fix = fix
                            lat = fix["lat"]; lon = fix["lon"]

                            if last_position:
                                d = haversine_nm(last_position[0], last_position[1], lat, lon)
                                if d < 1: distance_traveled_nm += d
                            last_position = (lat, lon)

                            remaining_nm = haversine_nm(lat, lon,
                                setup["destination_lat"], setup["destination_lon"])
                            completed_nm = initial_distance_nm - remaining_nm
                            pct          = (completed_nm / initial_distance_nm) * 100

                            if completed_nm >= next_milestone:
                                write_event(f"{completed_nm:.1f} NM COMPLETED | "
                                            f"{pct:.1f}% DONE | {remaining_nm:.1f} NM REMAINING")
                                next_milestone += 5

                            if remaining_nm < 0.2:
                                write_event("DESTINATION REACHED")

                        if time.time() - last_display > 30:
                            if latest_fix:
                                ls = decimal_to_deg_min(latest_fix["lat"], is_lon=False)
                                lo = decimal_to_deg_min(latest_fix["lon"], is_lon=True)
                                rem = haversine_nm(latest_fix["lat"], latest_fix["lon"],
                                    setup["destination_lat"], setup["destination_lon"])
                                print(f"[NMEA] {ls}  {lo}  REM={rem:.1f} nm")
                            last_display = time.time()

        except KeyboardInterrupt:
            print("\nStopping logger")
            arrival = input("Arrival : ").strip()
            write_event(f"ARRIVAL : {arrival}")
            write_event(f"TOTAL TRAVELED : {distance_traveled_nm:.1f} nm")
            print("Compressing log...")
            compress_file(output_file)
            print("Bye")
            break

        except Exception as e:
            print(f"\nDisconnected : {e}")
            print("Reconnect in 5 sec...\n")
            time.sleep(5)

if __name__ == "__main__":
    main()
