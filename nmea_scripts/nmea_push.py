#!/usr/bin/env python3
"""
nmea_push.py — Sync logs/scripts vers le repo Git et push sur GitHub.

Conçu pour un Raspberry Pi sur 4G lente avec un gros dépôt :
  - répare automatiquement un dépôt corrompu (objets vides, HEAD cassé)
    en récupérant d'abord seulement le dernier commit (rapide),
  - puis restaure TOUT l'historique petit à petit, par tranches,
    sur plusieurs lancements si besoin (rien n'est jamais supprimé),
  - timeouts longs + détection de connexion morte au lieu de couper à 30 s,
  - commit local même hors ligne, push dès que possible,
  - fsync pour protéger la carte SD des coupures de courant.
"""

import os
import shutil
import subprocess
import sys
import time

# =========================================================
# CONFIG
# =========================================================
REPO_PATH     = "/home/zopa/science/zopa_repo"
LOG_FOLDER    = "/home/zopa/science/nmea_logs"
SCRIPT_FOLDER = "/home/zopa/science/nmea_scripts"
BRANCH        = "main"
REMOTE        = "origin"
COMMIT_MSG    = "Ajout logs NMEA + scripts"

TIMEOUT_LOCAL   = 300    # add / commit / status (gros logs sur SD lente)
TIMEOUT_NETWORK = 1800   # fetch / push : 30 min max par opération
NET_ATTEMPTS    = 3      # tentatives réseau (la 4G coupe parfois : "early EOF")
MIN_FREE_MB     = 200    # espace disque minimum pour continuer
MAX_FILE_MB     = 95     # fichiers plus gros : jamais commités (GitHub refuse > 100 Mo)

DEEPEN_STEP     = 20     # commits d'historique récupérés par tranche
HISTORY_BUDGET  = 600    # secondes max par lancement pour restaurer l'historique

LOST_LOG = os.path.join(REPO_PATH, ".git", "nmea_lost_commits.txt")


# =========================================================
# UTILS
# =========================================================
def run(cmd, check=True, timeout=TIMEOUT_LOCAL, capture=False):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        timeout=timeout,
        capture_output=capture,
        text=True,
    )


def quiet(cmd):
    """Commande git silencieuse, ne lève jamais d'exception."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_LOCAL)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", "timeout")


def section(title):
    print(f"\n{'─'*50}\n  {title}\n{'─'*50}")


def flush_disk():
    """Force l'écriture des données en attente sur la carte SD."""
    try:
        os.sync()
    except Exception:
        pass


# =========================================================
# ESPACE DISQUE
# =========================================================
def check_disk_space():
    free_mb = shutil.disk_usage(REPO_PATH).free // (1024 * 1024)
    if free_mb < MIN_FREE_MB:
        print(f"  ✗ Espace disque insuffisant : {free_mb} Mo libres (minimum {MIN_FREE_MB} Mo)")
        print("    Libère de la place sur la carte SD avant de relancer.")
        sys.exit(1)
    print(f"  ✓ Espace disque : {free_mb} Mo libres")


# =========================================================
# INTERNET
# =========================================================
def has_internet():
    try:
        subprocess.run(
            ["curl", "-s", "--max-time", "5", "-I", "https://github.com"],
            check=True, capture_output=True,
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
# GIT : réglages (idempotents, sans risque)
# =========================================================
def git_configure():
    settings = {
        "core.fsync": "all",              # écrit vraiment sur la SD (Git >= 2.36)
        "http.postBuffer": "524288000",   # gros push en HTTPS
        "http.lowSpeedLimit": "1000",     # connexion considérée morte si < 1 Ko/s...
        "http.lowSpeedTime": "120",       # ...pendant 2 min (au lieu de bloquer)
    }
    for key, val in settings.items():
        quiet(["git", "config", key, val])
    print("  ✓ Réglages Git appliqués")


# =========================================================
# GIT : fetch avec retry
# =========================================================
def git_fetch(extra=(), timeout=TIMEOUT_NETWORK, attempts=NET_ATTEMPTS):
    for attempt in range(1, attempts + 1):
        label = " ".join(extra) or "normal"
        print(f"  → Fetch {label} (tentative {attempt}/{attempts})...")
        try:
            run(["git", "fetch", *extra, REMOTE, BRANCH], timeout=timeout)
            print("  ✓ Fetch OK")
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ⚠ Fetch échoué (code {e.returncode})")
        except subprocess.TimeoutExpired:
            print(f"  ⚠ Fetch timeout ({timeout} s)")
    return False


def is_shallow():
    return quiet(["git", "rev-parse", "--is-shallow-repository"]).stdout.strip() == "true"


def remote_commit_count():
    r = quiet(["git", "rev-list", "--count", f"{REMOTE}/{BRANCH}"])
    try:
        return int(r.stdout.strip())
    except ValueError:
        return -1


# =========================================================
# GIT : nettoyer tout état sale (lock, rebase, merge en cours)
# =========================================================
def git_cleanup_state():
    git_dir = os.path.join(REPO_PATH, ".git")
    cleaned = False

    index_lock = os.path.join(git_dir, "index.lock")
    if os.path.exists(index_lock):
        print("  ⚠ index.lock orphelin (process git interrompu) — suppression...")
        os.remove(index_lock)
        cleaned = True

    rebase_dirs = [os.path.join(git_dir, d) for d in ("rebase-merge", "rebase-apply")]
    if any(os.path.exists(d) for d in rebase_dirs):
        print("  ⚠ Rebase en cours détecté — abandon...")
        quiet(["git", "rebase", "--abort"])
        for d in rebase_dirs:
            if os.path.exists(d):
                shutil.rmtree(d)
        cleaned = True

    if os.path.exists(os.path.join(git_dir, "MERGE_HEAD")):
        print("  ⚠ Merge en cours détecté — abandon...")
        quiet(["git", "merge", "--abort"])
        cleaned = True

    if os.path.exists(os.path.join(git_dir, "CHERRY_PICK_HEAD")):
        print("  ⚠ Cherry-pick en cours détecté — abandon...")
        quiet(["git", "cherry-pick", "--abort"])
        cleaned = True

    if cleaned:
        print("  ✓ État git nettoyé")


# =========================================================
# GIT : santé et réparation
# =========================================================
def git_remove_empty_objects():
    objdir = os.path.join(REPO_PATH, ".git", "objects")
    empties = []
    for root, _dirs, files in os.walk(objdir):
        for f in files:
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) == 0:
                    empties.append(p)
            except OSError:
                pass

    if not empties:
        print("  ✓ Aucun objet Git vide")
        return False

    print(f"  ⚠ {len(empties)} objet(s) Git vide(s) détecté(s) — suppression...")
    for p in empties:
        os.remove(p)
        print(f"    supprimé : {os.path.relpath(p, REPO_PATH)}")
    flush_disk()
    return True


def git_is_healthy(verbose=True):
    for cmd in (["git", "rev-parse", "--verify", "HEAD^{commit}"],
                ["git", "status", "--porcelain"]):
        r = quiet(cmd)
        if r.returncode != 0:
            if verbose:
                print(f"  ✗ {' '.join(cmd[1:3])} a échoué : {r.stderr.strip()}")
            return False
    return True


def git_restore_head_from_remote():
    """
    HEAD est cassé : on récupère seulement le dernier commit de GitHub
    (rapide), on y rattache la branche et on reconstruit l'index.
    Les fichiers du dossier ne sont PAS touchés : les nouveaux logs
    seront simplement recommités. L'historique complet reste sur GitHub
    et sera re-téléchargé ensuite par git_restore_history().
    """
    print("  → Restauration du dernier commit depuis GitHub (rapide)...")

    old = quiet(["git", "rev-parse", f"refs/heads/{BRANCH}"]).stdout.strip()
    if old:
        with open(LOST_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ancien {BRANCH} = {old}\n")
        print(f"    (ancien commit local noté dans {os.path.relpath(LOST_LOG, REPO_PATH)})")

    if not git_fetch(["--depth", "1"]):
        return False

    run(["git", "update-ref", f"refs/heads/{BRANCH}", f"refs/remotes/{REMOTE}/{BRANCH}"])
    run(["git", "symbolic-ref", "HEAD", f"refs/heads/{BRANCH}"])

    index = os.path.join(REPO_PATH, ".git", "index")
    if os.path.exists(index):
        os.remove(index)                  # l'index peut lui aussi être abîmé
    run(["git", "reset", "-q"])           # reconstruit l'index, garde les fichiers
    flush_disk()
    return git_is_healthy()


def git_ensure_healthy(online):
    git_remove_empty_objects()

    if git_is_healthy():
        print("  ✓ Dépôt Git sain")
        return

    if not online:
        print("  ✗ Dépôt corrompu et pas d'internet : réparation au prochain lancement.")
        sys.exit(1)

    if git_restore_head_from_remote():
        print("  ✓ Dépôt réparé (l'historique sera restauré progressivement)")
        return

    print("\n  ✗ Réparation impossible pour l'instant (connexion trop instable ?).")
    print("    Relance nmea_push plus tard : la réparation reprendra automatiquement.")
    sys.exit(1)


# =========================================================
# GIT : restaurer tout l'historique, par tranches
# =========================================================
def git_restore_history(budget=HISTORY_BUDGET):
    if not is_shallow():
        print("  ✓ Historique complet présent")
        return

    start = time.time()
    while True:
        remaining = budget - (time.time() - start)
        if remaining < 30:
            break

        before = remote_commit_count()
        ok = git_fetch(["--deepen", str(DEEPEN_STEP)],
                       timeout=max(60, int(remaining)), attempts=1)
        if not ok:
            break

        if not is_shallow():
            print("  ✓ Historique complet restauré")
            return

        after = remote_commit_count()
        if after == before:
            # plus rien à approfondir : on termine proprement
            if git_fetch(["--unshallow"], timeout=max(60, int(remaining)), attempts=1):
                print("  ✓ Historique complet restauré")
                return
            break
        print(f"  ✓ Historique : {after} commits présents localement")

    print("  … Historique encore incomplet : la suite au prochain lancement")
    print("    (rien n'est perdu, tout l'historique est sur GitHub)")


# =========================================================
# GIT : branche, merge, commit, push
# =========================================================
def ensure_branch():
    current = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True).stdout.strip()
    if current in ("HEAD", ""):
        print(f"  ⚠ HEAD détaché — rattachement à '{BRANCH}'...")
        run(["git", "checkout", BRANCH])
    elif current != BRANCH:
        print(f"  ⚠ Branche '{current}' — passage sur '{BRANCH}'...")
        run(["git", "checkout", BRANCH])
    else:
        print(f"  ✓ Branche : {current}")


def git_merge():
    print("  → Merge (stratégie : garder local si conflit)...")
    try:
        run(["git", "merge", "--no-edit", "-X", "ours", f"{REMOTE}/{BRANCH}"])
        print("  ✓ Merge OK")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  ⚠ Merge échoué : {e}")
        if os.path.exists(os.path.join(REPO_PATH, ".git", "MERGE_HEAD")):
            print("  ⚠ Merge bloqué — abandon...")
            quiet(["git", "merge", "--abort"])
        return False


EXCLUDE_BEGIN = "# >>> nmea_push : gros fichiers (géré automatiquement)"
EXCLUDE_END   = "# <<< nmea_push"


def _escape_glob(path):
    for ch in "\\[]*?!#":
        path = path.replace(ch, "\\" + ch)
    return path


def git_skip_big_files():
    """
    Les fichiers > MAX_FILE_MB ne sont jamais commités : ils sont ajoutés
    à .git/info/exclude (ignore local, non poussé) et retirés de l'index
    s'ils y étaient. Ils restent intacts sur le disque.
    """
    limit = MAX_FILE_MB * 1024 * 1024
    big = []
    for root, dirs, files in os.walk(REPO_PATH):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            p = os.path.join(root, f)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > limit:
                big.append((os.path.relpath(p, REPO_PATH), size))
    big.sort()

    # Réécrit le bloc géré dans .git/info/exclude (le reste n'est pas touché)
    exclude = os.path.join(REPO_PATH, ".git", "info", "exclude")
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    lines = []
    if os.path.exists(exclude):
        with open(exclude) as fh:
            lines = fh.read().splitlines()
    kept, inside = [], False
    for line in lines:
        if line == EXCLUDE_BEGIN:
            inside = True
        elif line == EXCLUDE_END:
            inside = False
        elif not inside:
            kept.append(line)
    if big:
        kept += [EXCLUDE_BEGIN] + ["/" + _escape_glob(rel) for rel, _ in big] + [EXCLUDE_END]
    with open(exclude, "w") as fh:
        fh.write("\n".join(kept) + "\n")

    if not big:
        print(f"  ✓ Aucun fichier > {MAX_FILE_MB} Mo")
        return

    print(f"  ⚠ {len(big)} fichier(s) > {MAX_FILE_MB} Mo ignoré(s) (gardés sur le disque, pas poussés) :")
    for rel, size in big:
        print(f"    - {rel} ({size / 1024 / 1024:.0f} Mo)")
        if quiet(["git", "ls-files", "--error-unmatch", "--", rel]).returncode == 0:
            quiet(["git", "rm", "--cached", "-q", "--", rel])
            print("      (retiré du suivi Git)")


def git_commit():
    result = run(["git", "status", "--porcelain"], capture=True)
    if not result.stdout.strip():
        print("  ✓ Rien à committer (repo à jour)")
        return False

    print("  → Changements détectés, commit en cours...")
    run(["git", "add", "."])
    run(["git", "commit", "-q", "-m", COMMIT_MSG])
    flush_disk()
    print("  ✓ Commit OK")
    return True


def git_push_with_retry(attempts=NET_ATTEMPTS):
    for attempt in range(1, attempts + 1):
        print(f"  → Push (tentative {attempt}/{attempts})...")
        try:
            run(["git", "push", REMOTE, BRANCH], timeout=TIMEOUT_NETWORK)
            print("  ✓ Push GitHub OK")
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ✗ Push échoué (code {e.returncode})")
        except subprocess.TimeoutExpired:
            print("  ✗ Push timeout")
    print("  ✗ Push en attente — sera retenté au prochain nmea_push")
    return False


# =========================================================
# MAIN
# =========================================================
def main():
    section("Vérifications")
    check_disk_space()

    section("Sync fichiers → repo")
    sync_folder(LOG_FOLDER,    os.path.join(REPO_PATH, "nmea_logs"))
    sync_folder(SCRIPT_FOLDER, os.path.join(REPO_PATH, "nmea_scripts"))
    flush_disk()

    section("Vérification internet")
    online = has_internet()
    if online:
        print("  ✓ Internet OK")
    else:
        print("  ✗ Pas d'accès internet — commit local uniquement")
        print("    (Vulcan doit être connecté via 4G pour le push)")

    section("Santé du dépôt Git")
    os.chdir(REPO_PATH)
    git_configure()
    git_cleanup_state()
    git_ensure_healthy(online)

    section("Git")
    ensure_branch()
    if online and git_fetch():
        git_merge()
    git_skip_big_files()
    git_commit()
    if online:
        git_push_with_retry()

    if online:
        section("Historique")
        git_restore_history()

    flush_disk()
    section("Terminé")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"\n  ✗ Commande échouée (code {e.returncode}) : {' '.join(e.cmd)}")
        if e.stderr:
            print(e.stderr.strip())
        sys.exit(1)
    except subprocess.TimeoutExpired as e:
        print(f"\n  ✗ Timeout : {' '.join(e.cmd)}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n  ✗ Interrompu")
        sys.exit(130)
