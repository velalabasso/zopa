#!/usr/bin/env python3

import subprocess
import os

# =========================================================
# CONFIG
# =========================================================

REPO_PATH     = "/home/zopa/science/zopa_repo"
LOG_FOLDER    = "/home/zopa/science/nmea_logs"
SCRIPT_FOLDER = "/home/zopa/science/nmea_scripts"

# =========================================================
# CHECK INTERNET
# =========================================================

def has_internet():
    """
    Returns True if GitHub is reachable.
    Works whether Vulcan routes internet or not.
    """
    try:
        subprocess.run(
            ["curl", "-s", "--max-time", "5", "-I", "https://github.com"],
            check=True,
            capture_output=True
        )
        return True
    except:
        return False

# =========================================================
# SYNC FOLDER
# =========================================================

def sync_folder(src, dst):
    os.makedirs(dst, exist_ok=True)
    subprocess.run([
        "rsync",
        "-av",
        "--delete",
        src + "/",
        dst + "/"
    ], check=True)
    print(f"Sync: {src} -> {dst}")

# =========================================================
# GIT PUSH
# =========================================================

def git_push():

    print("Vérification internet...")

    if not has_internet():
        print("Pas d'accès internet — push annulé")
        print("(Vulcan doit être connecté à internet via 4G)")
        return

    print("Internet OK")

    try:

        os.chdir(REPO_PATH)

        # Vérifier changements locaux
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True
        )

        if result.stdout.strip():
            print("Changements détectés -> commit")
            subprocess.run(["git", "add", "."], check=True)
            subprocess.run(
                ["git", "commit", "-m", "Ajout logs NMEA + scripts"],
                check=True
            )
        else:
            print("Aucun nouveau changement")

        # Pull
        print("Tentative pull GitHub...")
        try:
            subprocess.run(
                ["git", "pull", "--rebase"],
                check=True,
                timeout=20
            )
        except Exception as e:
            print(f"Pull ignoré (réseau ou conflit): {e}")

        # Push avec retry
        print("Tentative push GitHub...")
        pushed = False
        for i in range(3):
            try:
                subprocess.run(
                    ["git", "push"],
                    check=True,
                    timeout=20
                )
                print("Push GitHub OK")
                pushed = True
                break
            except Exception as e:
                print(f"Push échoué (tentative {i+1}/3): {e}")

        if not pushed:
            print("Push en attente (sera retenté au prochain nmea_push)")

    except Exception as e:
        print(f"Erreur Git globale: {e}")

# =========================================================
# MAIN
# =========================================================

def main():

    print("Sync logs + scripts vers repo Git...")

    sync_folder(
        LOG_FOLDER,
        os.path.join(REPO_PATH, "nmea_logs")
    )

    sync_folder(
        SCRIPT_FOLDER,
        os.path.join(REPO_PATH, "nmea_scripts")
    )

    git_push()

if __name__ == "__main__":
    main()
