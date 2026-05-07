#!/usr/bin/env python3

import subprocess
import os
import shutil

REPO_PATH = "/home/zopa/science/zopa_repo"

LOG_FOLDER = "/home/zopa/science/nmea_logs"
SCRIPT_FOLDER = "/home/zopa/science/nmea_scripts"


# =========================================================
# COPY FOLDERS INTO REPO
# =========================================================
def sync_folder(src, dst):
    if os.path.exists(dst):
        shutil.rmtree(dst)

    shutil.copytree(src, dst)
    print(f"Copié: {src} -> {dst}")


# =========================================================
# GIT PUSH
# =========================================================
def git_push():
    try:
        os.chdir(REPO_PATH)

        # 1. voir si changements
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True
        )

        if result.stdout.strip() == "":
            print("Aucun changement à push")
            return

        # 2. stage + commit d'abord
        subprocess.run(["git", "add", "."], check=True)

        subprocess.run(
            ["git", "commit", "-m", "Ajout logs NMEA + scripts"],
            check=True
        )

        # 3. sync avec GitHub
        subprocess.run(["git", "pull", "--rebase"], check=True)

        # 4. push
        subprocess.run(["git", "push"], check=True)

        print("Push GitHub OK")

    except Exception as e:
        print(f"Erreur Git: {e}")

# =========================================================
# MAIN
# =========================================================
def main():

    print("Sync logs + scripts vers repo Git...")

    # logs
    sync_folder(
        LOG_FOLDER,
        os.path.join(REPO_PATH, "nmea_logs")
    )

    # scripts
    sync_folder(
        SCRIPT_FOLDER,
        os.path.join(REPO_PATH, "nmea_scripts")
    )

    git_push()


if __name__ == "__main__":
    main()
