#!/usr/bin/env python3
"""
nmea_push.py — Sync logs/scripts vers le repo Git et push sur GitHub.
"""

import os
import shutil
import subprocess
import sys

# =========================================================
# CONFIG
# =========================================================
REPO_PATH     = "/home/zopa/science/zopa_repo"
LOG_FOLDER    = "/home/zopa/science/nmea_logs"
SCRIPT_FOLDER = "/home/zopa/science/nmea_scripts"
BRANCH        = "main"
REMOTE        = "origin"
COMMIT_MSG    = "Ajout logs NMEA + scripts"

# =========================================================
# UTILS
# =========================================================
def run(cmd, check=True, timeout=30, capture=False):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        timeout=timeout,
        capture_output=capture,
        text=True
    )

def section(title):
    print(f"\n{'─'*50}\n  {title}\n{'─'*50}")

# =========================================================
# INTERNET
# =========================================================
def has_internet():
    try:
        subprocess.run(
            ["curl", "-s", "--max-time", "5", "-I", "https://github.com"],
            check=True, capture_output=True
        )
        return True
    except Exception:
        return False

# =========================================================
# SYNC DOSSIER
# =========================================================
def sync_folder(src, dst):
    os.makedirs(dst, exist_ok=True)
    run(["rsync", "-av", "--delete", src + "/", dst + "/"])
    print(f"  ✓ Sync : {src} → {dst}")

# =========================================================
# GIT : nettoyer tout état sale (rebase, merge en cours)
# =========================================================
def git_cleanup_state():
    git_dir = os.path.join(REPO_PATH, ".git")
    cleaned = False

    rebase_merge = os.path.join(git_dir, "rebase-merge")
    rebase_apply = os.path.join(git_dir, "rebase-apply")
    merge_head   = os.path.join(git_dir, "MERGE_HEAD")
    cherry_head  = os.path.join(git_dir, "CHERRY_PICK_HEAD")

    if os.path.exists(rebase_merge) or os.path.exists(rebase_apply):
        print("  ⚠ Rebase en cours détecté — abandon...")
        run(["git", "rebase", "--abort"], check=False)
        for d in [rebase_merge, rebase_apply]:
            if os.path.exists(d):
                shutil.rmtree(d)
                print(f"  ✓ Supprimé : {d}")
        cleaned = True

    if os.path.exists(merge_head):
        print("  ⚠ Merge en cours détecté — abandon...")
        run(["git", "merge", "--abort"], check=False)
        cleaned = True

    if os.path.exists(cherry_head):
        print("  ⚠ Cherry-pick en cours détecté — abandon...")
        run(["git", "cherry-pick", "--abort"], check=False)
        cleaned = True

    if cleaned:
        print("  ✓ État git nettoyé")

# =========================================================
# GIT : s'assurer qu'on est sur la bonne branche
# =========================================================
def ensure_branch():
    result = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True)
    current = result.stdout.strip()
    if current in ("HEAD", ""):
        print(f"  ⚠ HEAD détaché — rattachement à '{BRANCH}'...")
        run(["git", "checkout", BRANCH])
    elif current != BRANCH:
        print(f"  ⚠ Branche '{current}' — passage sur '{BRANCH}'...")
        run(["git", "checkout", BRANCH])
    else:
        print(f"  ✓ Branche : {current}")

# =========================================================
# GIT : pull en gardant notre version locale si conflit
# =========================================================
def git_pull():
    print("  → Pull (stratégie : garder local si conflit)...")
    try:
        run(["git", "pull", "--no-rebase", "-X", "ours", REMOTE, BRANCH], timeout=30)
        print("  ✓ Pull OK")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ Pull échoué : {e}")
        git_dir = os.path.join(REPO_PATH, ".git")
        if os.path.exists(os.path.join(git_dir, "MERGE_HEAD")):
            print("  ⚠ Merge bloqué — abandon...")
            run(["git", "merge", "--abort"], check=False)
    except subprocess.TimeoutExpired:
        print("  ⚠ Pull timeout — on continue")

# =========================================================
# GIT : commit si changements locaux
# =========================================================
def git_commit():
    result = run(["git", "status", "--porcelain"], capture=True)
    if result.stdout.strip():
        print("  → Changements détectés, commit en cours...")
        run(["git", "add", "."])
        run(["git", "commit", "-m", COMMIT_MSG])
        print("  ✓ Commit OK")
        return True
    else:
        print("  ✓ Rien à committer (repo à jour)")
        return False

# =========================================================
# GIT : push avec retry
# =========================================================
def git_push_with_retry(max_attempts=3):
    for attempt in range(1, max_attempts + 1):
        print(f"  → Push (tentative {attempt}/{max_attempts})...")
        try:
            run(["git", "push", REMOTE, BRANCH], timeout=30)
            print("  ✓ Push GitHub OK")
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ✗ Push échoué : {e}")
        except subprocess.TimeoutExpired:
            print(f"  ✗ Push timeout")
    print("  ✗ Push en attente — sera retenté au prochain nmea_push")
    return False

# =========================================================
# MAIN
# =========================================================
def main():
    section("Sync fichiers → repo")
    sync_folder(LOG_FOLDER,    os.path.join(REPO_PATH, "nmea_logs"))
    sync_folder(SCRIPT_FOLDER, os.path.join(REPO_PATH, "nmea_scripts"))

    section("Vérification internet")
    if not has_internet():
        print("  ✗ Pas d'accès internet — push annulé")
        print("    (Vulcan doit être connecté via 4G)")
        sys.exit(0)
    print("  ✓ Internet OK")

    section("Git push")
    os.chdir(REPO_PATH)

    git_cleanup_state()   # 1. nettoie rebase/merge orphelins
    ensure_branch()       # 2. bonne branche
    git_pull()            # 3. pull AVANT commit, local gagne si conflit
    git_commit()          # 4. commit les nouveaux fichiers
    git_push_with_retry() # 5. push

    section("Terminé")

if __name__ == "__main__":
    main()
