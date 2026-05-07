import socket
import time
import subprocess
from datetime import datetime
import gzip
import shutil
import os

WIFI_SSID = "Vulcan 9R e6e9"
WIFI_PASSWORD = "OVyWzmlU"

base_folder = "/home/zopa/science/nmea_logs"
os.makedirs(base_folder, exist_ok=True)

HOST = "192.168.76.1"
PORT = 10110


# =========================================================
# WIFI
# =========================================================
def connect_wifi():
    print("Connexion WIFI...")

    try:
        subprocess.run(
            ["nmcli", "dev", "wifi", "connect", WIFI_SSID, "password", WIFI_PASSWORD],
            check=True
        )
        print("Wi-fi connecté")
    except Exception as e:
        print(f"Wi-fi erreur: {e}")


# =========================================================
# COMPRESSION
# =========================================================
def compress_file(file_path):
    gz_path = file_path + ".gz"

    with open(file_path, "rb") as f_in:
        with gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    os.remove(file_path)
    print(f"Compression OK: {gz_path}")


# =========================================================
# NMEA PARSER MINIMAL (RMC ONLY)
# =========================================================
def parse_rmc(line):
    try:
        parts = line.split(",")

        if parts[0] != "$GPRMC":
            return None

        lat = parts[3]
        lat_dir = parts[4]
        lon = parts[5]
        lon_dir = parts[6]

        time_utc = parts[1]
        date_utc = parts[9]

        if lat == "" or lon == "":
            return None

        return {
            "lat": lat,
            "lat_dir": lat_dir,
            "lon": lon,
            "lon_dir": lon_dir,
            "time": time_utc,
            "date": date_utc
        }

    except:
        return None


# =========================================================
# MAIN
# =========================================================
def main():

    connect_wifi()

    while True:

        print("\nConnexion Vesper...")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(10)
                s.connect((HOST, PORT))
                print("Connecté flux NMEA")

                buffer_lines = []
                first_fix = None

                # =================================================
                # CAPTURE JUSQU'À PREMIÈRE POSITION VALIDE
                # =================================================
                while first_fix is None:
                    data = s.recv(2048).decode(errors="ignore")

                    if not data:
                        raise Exception("Connexion perdue")

                    for line in data.split("\n"):
                        line = line.strip()

                        if not line.startswith("$"):
                            continue

                        buffer_lines.append(line)

                        fix = parse_rmc(line)
                        if fix:
                            first_fix = fix
                            break

                # =================================================
                # NOM DU LOG AVEC DATE GPS
                # =================================================

                gps_date = first_fix["date"]   # ex: 060526
                gps_time = first_fix["time"]   # ex: 050015

                # convertir ddmmyy -> yyyymmdd
                formatted_date = (
                    f"20{gps_date[4:6]}"
                    f"{gps_date[2:4]}"
                    f"{gps_date[0:2]}"
                )

                log_name = (
                    f"nmea_"
                    f"{formatted_date}_"
                    f"{gps_time}_"
                    f"{first_fix['lat']}{first_fix['lat_dir']}_"
                    f"{first_fix['lon']}{first_fix['lon_dir']}"
                )
                
                log_dir = os.path.join(base_folder, log_name)
                os.makedirs(log_dir, exist_ok=True)

                output_file = os.path.join(log_dir, f"{log_name}.txt")

                print(f"Dossier log : {log_dir}")

                # =================================================
                # ÉCRITURE FICHIER
                # =================================================
                with open(output_file, "w") as f:

                    f.write(f"# LOG NAME: {log_name}\n")
                    f.write(f"# FIRST FIX: {first_fix}\n\n")

                    # écrire buffer initial
                    for l in buffer_lines:
                        f.write(l + "\n")

                    # continuer flux
                    while True:
                        data = s.recv(2048).decode(errors="ignore")

                        if not data:
                            raise Exception("Connexion perdue")

                        for line in data.split("\n"):
                            line = line.strip()

                            if line.startswith("$"):
                                print(line)
                                f.write(line + "\n")

        except KeyboardInterrupt:
            print("\nArrêt manuel confirmé 👍")
            break

        except Exception as e:
            print(f"Déconnexion : {e}")

        finally:
            if 'output_file' in locals() and os.path.exists(output_file):
                print("Fermeture fichier + compression")
                compress_file(output_file)

        print("Reconnexion dans 5 secondes...\n")
        time.sleep(5)


if __name__ == "__main__":
    main()
