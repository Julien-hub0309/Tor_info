#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
installateur.py - Installateur de FULL INFO (Windows, macOS, Linux)

Ce que fait ce script (aucune dépendance externe, Python 3.8+ suffit) :
  1. vérifie Python et la présence des fichiers du projet
  2. vérifie / installe tkinter (interface graphique)
  3. crée un environnement virtuel  .venv  et y installe les bibliothèques
     (requests, PySocks, beautifulsoup4 : voir requirements.txt)
  4. vérifie Tor (et propose de l'installer)
  5. génère l'icône de l'application (PNG, ICO, ICNS)
  6. crée le raccourci sur le Bureau (+ menu d'applications)
  7. lance un test de fonctionnement

Utilisation :
  python installateur.py                 installation complète
  python installateur.py --verifier      diagnostic seul, ne modifie rien
  python installateur.py --desinstaller  retire raccourcis et environnement
  python installateur.py --desinstaller --purge   idem + supprime aussi les archives (~/.full_info)

Options : -y/--oui (répond oui aux questions)  --sans-tor  --sans-venv  --sans-menu
"""

import argparse
import math
import os
import platform
import plistlib
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

APP_NAME = "Full Info"
APP_SLUG = "full-info"
APP_COMMENT = "Veille d'information anonyme via Tor"
ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
ASSETS_DIR = ROOT / "assets"
ICON_PNG = ASSETS_DIR / "icon.png"
ICON_ICO = ASSETS_DIR / "icon.ico"
ICON_ICNS = ASSETS_DIR / "icon.icns"
REQUIREMENTS = ROOT / "requirements.txt"
DEFAULT_REQS = ["requests>=2.31", "PySocks>=1.7.1", "beautifulsoup4>=4.12"]
PROJECT_FILES = ("main.py", "full_info.py", "display.py")
TOTAL_STEPS = 7

SYSTEM = platform.system()
OS = "windows" if SYSTEM == "Windows" else "macos" if SYSTEM == "Darwin" else "linux"

# --------------------------------------------------------------------------- #
# Affichage
# --------------------------------------------------------------------------- #

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _enable_ansi():
    if OS != "windows":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        k.GetConsoleMode(h, ctypes.byref(mode))
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:
        return False


COLOR = sys.stdout.isatty() and _enable_ansi() and not os.environ.get("NO_COLOR")


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def title(text):
    print("\n" + _c("1", text))


def step(n, text):
    print("\n" + _c("1;36", f"[{n}/{TOTAL_STEPS}] {text}"))


def ok(text):
    print("  " + _c("32", "✔ ") + text)


def warn(text):
    print("  " + _c("33", "⚠ ") + text)


def err(text):
    print("  " + _c("31", "✘ ") + text)


def info(text):
    print("    " + text)


ARGS = None


def ask(question, default=True):
    """Pose une question oui/non. --oui répond oui ; hors terminal, la réponse est non."""
    if ARGS is not None and ARGS.oui:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        return False  # hors terminal : jamais d'action système sans --oui
    hint = "[O/n]" if default else "[o/N]"
    try:
        answer = input(f"  ? {question} {hint} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("o", "oui", "y", "yes")


# --------------------------------------------------------------------------- #
# Outils système
# --------------------------------------------------------------------------- #


def run(cmd, **kw):
    """Exécute une commande en capturant la sortie. Ne lève jamais d'exception."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def tail(text, n=12):
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return "\n".join("      " + ln for ln in lines[-n:])


def sudo_cmd(cmd):
    if OS == "windows":
        return cmd
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return cmd
    return ["sudo"] + cmd if shutil.which("sudo") else None


LINUX_PM = {
    "apt": {"bin": "apt-get", "install": ["apt-get", "install", "-y"], "tk": "python3-tk",
            "venv": "python3-venv", "tor": "tor"},
    "dnf": {"bin": "dnf", "install": ["dnf", "install", "-y"], "tk": "python3-tkinter",
            "venv": None, "tor": "tor"},
    "pacman": {"bin": "pacman", "install": ["pacman", "-S", "--noconfirm", "--needed"], "tk": "tk",
               "venv": None, "tor": "tor"},
    "zypper": {"bin": "zypper", "install": ["zypper", "--non-interactive", "install"], "tk": "python3-tk",
               "venv": None, "tor": "tor"},
}


def linux_pm():
    for name, pm in LINUX_PM.items():
        if shutil.which(pm["bin"]):
            return name, pm
    return None, None


def linux_install(packages, reason):
    """Installe des paquets système (demande confirmation, utilise sudo). Retourne True si OK."""
    name, pm = linux_pm()
    packages = [p for p in packages if p]
    if not pm or not packages:
        return False
    cmd = pm["install"] + packages
    full = sudo_cmd(cmd)
    shown = " ".join(cmd if full is None else full)
    if full is None:
        warn(f"Pas de sudo disponible. Lance toi-même : {' '.join(cmd)}")
        return False
    if not ask(f"Installer {', '.join(packages)} ({reason}) avec « {shown} » ?"):
        info(f"Commande à lancer plus tard : {shown}")
        return False
    if name == "apt":
        subprocess.run(sudo_cmd(["apt-get", "update"]))
    return subprocess.run(full).returncode == 0


def tor_port():
    for port in (9050, 9150):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.5):
                return port
        except OSError:
            continue
    return None


def tor_installed():
    """Retourne une description de l'installation Tor trouvée, ou ''. """
    if shutil.which("tor"):
        return "service Tor"
    home = Path.home()
    if OS == "windows":
        bases = [os.environ.get(k) for k in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)")]
        cands = [home / "Desktop" / "Tor Browser", home / "OneDrive" / "Desktop" / "Tor Browser"]
        cands += [Path(b) / sub for b in bases if b for sub in ("Tor Browser", "Programs/Tor Browser")]
    elif OS == "macos":
        cands = [Path("/Applications/Tor Browser.app"), home / "Applications" / "Tor Browser.app"]
    else:
        cands = [home / ".local/share/torbrowser", home / "tor-browser", Path("/opt/tor-browser")]
        if shutil.which("torbrowser-launcher"):
            return "Tor Browser (torbrowser-launcher)"
    for c in cands:
        if c.exists():
            return "Tor Browser"
    return ""


def powershell(command, extra_env=None):
    full = "try{[Console]::OutputEncoding=[Text.Encoding]::UTF8}catch{};" + command
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", full], env={**os.environ, **(extra_env or {})}, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Environnement Python
# --------------------------------------------------------------------------- #


def venv_python(venv=VENV_DIR):
    return venv / ("Scripts/python.exe" if OS == "windows" else "bin/python")


def gui_python(py):
    """Sous Windows, pythonw.exe lance l'application sans fenêtre de console."""
    if OS == "windows":
        pyw = Path(py).with_name("pythonw.exe")
        if pyw.exists():
            return pyw
    return Path(py)


def requirements_list():
    if REQUIREMENTS.exists():
        lines = [ln.strip() for ln in REQUIREMENTS.read_text(encoding="utf-8").splitlines()]
        return [ln for ln in lines if ln and not ln.startswith("#")]
    return DEFAULT_REQS


def imports_ok(py):
    r = run([str(py), "-c", "import requests, bs4, socks, tkinter; print('ok')"])
    return r.returncode == 0 and "ok" in r.stdout, r


# --------------------------------------------------------------------------- #
# Icône (générée sans dépendance : PNG, ICO et ICNS écrits à la main)
# --------------------------------------------------------------------------- #

_PNG_CACHE = {}


def make_png(size):
    if size in _PNG_CACHE:
        return _PNG_CACHE[size]
    s = size / 256.0
    bg, cream, red = (20, 18, 14, 255), (236, 230, 214, 255), (224, 85, 90, 255)
    px = bytearray(bytes(bg) * (size * size))

    def rect(x0, y0, x1, y1, col):
        X0, X1 = max(0, round(x0 * s)), min(size, round(x1 * s))
        Y0, Y1 = max(0, round(y0 * s)), min(size, round(y1 * s))
        X1, Y1 = max(X1, X0 + 1), max(Y1, Y0 + 1)
        row = bytes(col) * (min(X1, size) - X0)
        for y in range(Y0, min(Y1, size)):
            off = (y * size + X0) * 4
            px[off:off + len(row)] = row

    if size >= 48:  # double filet de journal
        for x0, y0, x1, y1 in ((14, 14, 242, 20), (14, 236, 242, 242), (14, 14, 20, 242), (236, 14, 242, 242)):
            rect(x0, y0, x1, y1, cream)
    if size >= 96:
        for x0, y0, x1, y1 in ((28, 28, 228, 30), (28, 226, 228, 228), (28, 28, 30, 228), (226, 28, 228, 228)):
            rect(x0, y0, x1, y1, cream)
    # lettres « F » et « I »
    rect(60, 70, 88, 186, cream)
    rect(60, 70, 132, 96, cream)
    rect(60, 116, 116, 138, cream)
    rect(150, 70, 208, 92, cream)
    rect(150, 164, 208, 186, cream)
    rect(166, 92, 192, 164, cream)
    rect(60, 204, 208, 210, red)

    # coins arrondis (canal alpha)
    r = size * 0.2
    ri = int(r) + 1
    for dy in range(ri):
        for dx in range(ri):
            if dx + 0.5 >= r or dy + 0.5 >= r:
                continue
            d = math.hypot(r - dx - 0.5, r - dy - 0.5)
            a = int(255 * max(0.0, min(1.0, r - d + 0.5)))
            for x, y in ((dx, dy), (size - 1 - dx, dy), (dx, size - 1 - dy), (size - 1 - dx, size - 1 - dy)):
                px[(y * size + x) * 4 + 3] = a

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(px[y * size * 4:(y + 1) * size * 4]) for y in range(size))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    _PNG_CACHE[size] = png
    return png


def make_ico(sizes=(16, 32, 48, 128, 256)):
    images = [make_png(sz) for sz in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for sz, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", 0 if sz >= 256 else sz, 0 if sz >= 256 else sz, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
    return header + entries + b"".join(images)


def make_icns():
    body = b""
    for tag, sz in ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512)):
        data = make_png(sz)
        body += tag + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def write_icons():
    ASSETS_DIR.mkdir(exist_ok=True)
    ICON_PNG.write_bytes(make_png(256))
    ICON_ICO.write_bytes(make_ico())
    ICON_ICNS.write_bytes(make_icns())


# --------------------------------------------------------------------------- #
# Dossiers et raccourcis
# --------------------------------------------------------------------------- #


def desktop_dir():
    home = Path.home()
    if OS == "windows":
        r = powershell("[Environment]::GetFolderPath('Desktop')")
        path = r.stdout.strip()
        if r.returncode == 0 and path:
            return Path(path)
        return home / "Desktop"
    if OS == "macos":
        return home / "Desktop"
    r = run(["xdg-user-dir", "DESKTOP"])
    out = r.stdout.strip()
    if r.returncode == 0 and out and Path(out) != home:
        return Path(out)
    cfg = home / ".config" / "user-dirs.dirs"
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("XDG_DESKTOP_DIR="):
                value = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(home))
                if value and Path(value) != home:
                    return Path(value)
    for name in ("Desktop", "Bureau", "Escritorio", "Schreibtisch", "Scrivania"):
        if (home / name).is_dir():
            return home / name
    return home / "Desktop"


def windows_programs_dir():
    r = powershell("[Environment]::GetFolderPath('Programs')")
    path = r.stdout.strip()
    return Path(path) if r.returncode == 0 and path else None


def create_windows_shortcut(lnk, target, arguments, workdir, icon):
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = ("$ws=New-Object -ComObject WScript.Shell;$s=$ws.CreateShortcut($env:FI_LNK);"
              "$s.TargetPath=$env:FI_TARGET;$s.Arguments=$env:FI_ARGS;$s.WorkingDirectory=$env:FI_WD;"
              "$s.IconLocation=$env:FI_ICON;$s.Description=$env:FI_DESC;$s.Save()")
    env = {"FI_LNK": str(lnk), "FI_TARGET": str(target), "FI_ARGS": arguments, "FI_WD": str(workdir),
           "FI_ICON": f"{icon},0", "FI_DESC": APP_COMMENT}
    r = powershell(script, env)
    if r.returncode != 0 or not lnk.exists():
        raise RuntimeError((r.stderr or r.stdout or "PowerShell indisponible").strip())


def desktop_quote(value):
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def linux_desktop_entry(py, icon):
    return "\n".join([
        "[Desktop Entry]", "Version=1.0", "Type=Application", f"Name={APP_NAME}",
        f"GenericName={APP_COMMENT}", f"Comment={APP_COMMENT}",
        f"Exec={desktop_quote(py)} {desktop_quote(ROOT / 'main.py')}",
        f"Path={ROOT}", f"Icon={icon}", "Terminal=false", "Categories=Network;News;",
        "Keywords=tor;actualités;veille;information;", "StartupNotify=true", ""])


def create_linux_entry(path, py, icon, trusted=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(linux_desktop_entry(py, icon), encoding="utf-8")
    path.chmod(0o755)
    if trusted and shutil.which("gio"):  # GNOME : autorise le lancement par double-clic
        run(["gio", "set", str(path), "metadata::trusted", "true"])


def create_macos_app(dest_dir, py):
    dest_dir.mkdir(parents=True, exist_ok=True)
    app = dest_dir / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    macos, res = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)
    launcher = macos / APP_SLUG
    launcher.write_text("#!/bin/bash\n"
                        f"cd {shlex.quote(str(ROOT))}\n"
                        f"exec {shlex.quote(str(py))} {shlex.quote(str(ROOT / 'main.py'))} \"$@\"\n",
                        encoding="utf-8")
    launcher.chmod(0o755)
    shutil.copy(ICON_ICNS, res / "icon.icns")
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump({
            "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": "org.fullinfo.app", "CFBundleVersion": "3.1",
            "CFBundleShortVersionString": "3.1", "CFBundleExecutable": APP_SLUG,
            "CFBundleIconFile": "icon", "CFBundlePackageType": "APPL",
            "LSMinimumSystemVersion": "10.13", "NSHighResolutionCapable": True,
        }, fh)
    return app


def shortcut_targets():
    """Liste (type, chemin) de tous les raccourcis gérés par l'installateur."""
    desk = desktop_dir()
    if OS == "windows":
        items = [("fichier", desk / f"{APP_NAME}.lnk")]
        prog = windows_programs_dir()
        if prog:
            items.append(("fichier", prog / f"{APP_NAME}.lnk"))
    elif OS == "macos":
        items = [("dossier", desk / f"{APP_NAME}.app"),
                 ("dossier", Path.home() / "Applications" / f"{APP_NAME}.app")]
    else:
        items = [("fichier", desk / f"{APP_SLUG}.desktop"),
                 ("fichier", Path.home() / ".local/share/applications" / f"{APP_SLUG}.desktop"),
                 ("fichier", Path.home() / ".local/share/icons/hicolor/256x256/apps" / f"{APP_SLUG}.png")]
    return items


# --------------------------------------------------------------------------- #
# Étapes d'installation
# --------------------------------------------------------------------------- #


def step_python():
    step(1, "Vérification de Python et du projet")
    v = sys.version_info
    if v < (3, 8):
        err(f"Python {v.major}.{v.minor} détecté : Python 3.8 ou plus récent est nécessaire.")
        return False
    ok(f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
    ok(f"Système : {platform.system()} {platform.release()}")
    missing = [f for f in PROJECT_FILES if not (ROOT / f).exists()]
    if missing:
        err("Fichiers du projet introuvables : " + ", ".join(missing))
        info(f"Place installateur.py dans le dossier de Full Info ({ROOT}).")
        return False
    ok(f"Projet trouvé : {ROOT}")
    return True


def tkinter_available():
    return run([sys.executable, "-c", "import tkinter"]).returncode == 0


def step_tkinter():
    step(2, "Interface graphique (tkinter)")
    if tkinter_available():
        ok("tkinter disponible")
        return True
    warn("tkinter est absent de cette installation de Python.")
    if OS == "linux":
        _, pm = linux_pm()
        if pm and linux_install([pm["tk"]], "interface graphique") and tkinter_available():
            ok("tkinter installé")
            return True
        info("Installe le paquet tkinter de ta distribution (ex : sudo apt install python3-tk), puis relance.")
    elif OS == "macos":
        v = sys.version_info
        if shutil.which("brew") and ask(f"Installer python-tk@{v.major}.{v.minor} avec Homebrew ?"):
            subprocess.run(["brew", "install", f"python-tk@{v.major}.{v.minor}"])
            if tkinter_available():
                ok("tkinter installé")
                return True
        info("Installe Python depuis https://www.python.org/downloads/ (tkinter inclus) "
             f"ou : brew install python-tk@{v.major}.{v.minor}")
    else:
        info("Relance l'installateur de Python → « Modify » → coche « tcl/tk and IDLE ».")
    warn("L'interface graphique ne fonctionnera pas ; la ligne de commande (python main.py -t ...) reste utilisable.")
    return False


def create_venv():
    py = venv_python()
    if py.exists() and run([str(py), "-c", "import sys"]).returncode == 0:
        ok("Environnement virtuel existant réutilisé (.venv)")
        return py
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    r = run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if r.returncode != 0 and OS == "linux":
        _, pm = linux_pm()
        if pm and pm["venv"] and linux_install([pm["venv"]], "création d'environnements virtuels"):
            shutil.rmtree(VENV_DIR, ignore_errors=True)
            r = run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if r.returncode != 0 or not py.exists():
        err("Impossible de créer l'environnement virtuel.")
        print(tail(r.stderr or r.stdout))
        return None
    ok("Environnement virtuel créé (.venv)")
    return py


def step_dependencies():
    step(3, "Bibliothèques Python")
    reqs = requirements_list()
    if ARGS.sans_venv:
        py = Path(sys.executable)
        warn("Mode sans environnement virtuel : installation dans l'interpréteur courant.")
    else:
        py = create_venv()
        if py is None:
            return None
        run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--upgrade", "pip"])
    info("Installation : " + ", ".join(reqs))
    cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--quiet"] + reqs
    r = run(cmd)
    if r.returncode != 0 and "externally-managed" in (r.stderr + r.stdout):
        r = run(cmd + ["--user", "--break-system-packages"])
    if r.returncode != 0:
        err("L'installation des bibliothèques a échoué :")
        print(tail(r.stderr or r.stdout))
        info("Vérifie ta connexion Internet (pip télécharge depuis pypi.org), puis relance.")
        return None
    good, res = imports_ok(py)
    if not good:
        # tkinter peut manquer : les bibliothèques réseau suffisent pour la ligne de commande
        net = run([str(py), "-c", "import requests, bs4, socks"])
        if net.returncode != 0:
            err("Les bibliothèques sont installées mais ne s'importent pas :")
            print(tail(res.stderr))
            return None
        warn("Bibliothèques installées (tkinter manquant : voir étape 2).")
    else:
        ok("requests, PySocks, beautifulsoup4 et tkinter fonctionnent")
    return py


def step_tor():
    step(4, "Tor (obligatoire pour les recherches)")
    if ARGS.sans_tor:
        warn("Vérification de Tor ignorée (--sans-tor).")
        return
    port = tor_port()
    if port:
        ok(f"Tor actif sur le port {port}")
        return
    found = tor_installed()
    if found:
        warn(f"{found} installé, mais pas démarré.")
        explain_start_tor()
        return

    warn("Tor n'est pas installé.")
    installed = False
    if OS == "linux":
        _, pm = linux_pm()
        if pm and linux_install([pm["tor"]], "réseau Tor"):
            installed = True
            sc = sudo_cmd(["systemctl", "enable", "--now", "tor"])
            if sc and shutil.which("systemctl"):
                subprocess.run(sc)
    elif OS == "macos":
        if shutil.which("brew") and ask("Installer Tor avec Homebrew (brew install tor) ?"):
            installed = subprocess.run(["brew", "install", "tor"]).returncode == 0
            if installed:
                subprocess.run(["brew", "services", "start", "tor"])
        else:
            info("Télécharge Tor Browser : https://www.torproject.org/download/ (puis ouvre-le).")
    else:
        if shutil.which("winget") and ask("Installer Tor Browser avec winget ?"):
            installed = subprocess.run(["winget", "install", "-e", "--id", "TorProject.TorBrowser",
                                        "--accept-package-agreements", "--accept-source-agreements"]).returncode == 0
        else:
            info("Télécharge Tor Browser : https://www.torproject.org/download/")
    if installed:
        ok("Tor installé")
        import time
        for _ in range(8):
            if tor_port():
                ok("Tor actif")
                return
            time.sleep(1)
        explain_start_tor()
    else:
        warn("Installe Tor avant de lancer des recherches : l'application refuse de fonctionner sans lui.")


def explain_start_tor():
    if OS == "windows":
        info("Ouvre Tor Browser et clique sur « Se connecter » : Full Info utilisera son port (9150).")
    elif OS == "macos":
        info("Ouvre Tor Browser (« Se connecter ») ou lance :  brew services start tor")
    else:
        info("Démarre le service :  sudo systemctl enable --now tor   (ou ouvre Tor Browser)")


def step_icons():
    step(5, "Icône de l'application")
    try:
        write_icons()
    except OSError as exc:
        warn(f"Icône non créée : {exc}")
        return False
    ok(f"Icônes générées dans {ASSETS_DIR}")
    return True


def step_shortcuts(py):
    step(6, "Raccourcis (Bureau et menu des applications)")
    gui = gui_python(py)
    main_py = ROOT / "main.py"
    created = []
    desk = desktop_dir()
    try:
        if OS == "windows":
            create_windows_shortcut(desk / f"{APP_NAME}.lnk", gui, f'"{main_py}"', ROOT, ICON_ICO)
            created.append(desk / f"{APP_NAME}.lnk")
            if not ARGS.sans_menu:
                prog = windows_programs_dir()
                if prog:
                    create_windows_shortcut(prog / f"{APP_NAME}.lnk", gui, f'"{main_py}"', ROOT, ICON_ICO)
                    created.append(prog / f"{APP_NAME}.lnk")
        elif OS == "macos":
            created.append(create_macos_app(desk, gui))
            if not ARGS.sans_menu:
                created.append(create_macos_app(Path.home() / "Applications", gui))
        else:
            icon_dir = Path.home() / ".local/share/icons/hicolor/256x256/apps"
            icon_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(ICON_PNG, icon_dir / f"{APP_SLUG}.png")
            create_linux_entry(desk / f"{APP_SLUG}.desktop", gui, ICON_PNG, trusted=True)
            created.append(desk / f"{APP_SLUG}.desktop")
            if not ARGS.sans_menu:
                menu = Path.home() / ".local/share/applications" / f"{APP_SLUG}.desktop"
                create_linux_entry(menu, gui, ICON_PNG)
                created.append(menu)
                if shutil.which("update-desktop-database"):
                    run(["update-desktop-database", str(menu.parent)])
    except Exception as exc:
        err(f"Création du raccourci impossible : {exc}")
        if OS == "macos":
            info("macOS peut demander l'autorisation d'accéder au Bureau (Réglages → Confidentialité).")
        info(f"Lancement manuel : {gui} {main_py}")
        return []
    for p in created:
        ok(f"Raccourci créé : {p}")
    if OS == "linux":
        info("Si l'icône du Bureau affiche un cadenas : clic droit → « Autoriser le lancement ».")
    return created


def step_test(py):
    step(7, "Test de fonctionnement")
    with tempfile.TemporaryDirectory() as tmp:
        r = run([str(py), str(ROOT / "main.py"), "--list-modules"], cwd=str(ROOT),
                env={**os.environ, "FULL_INFO_HOME": tmp})
    if r.returncode == 0 and "bing_news" in r.stdout:
        count = sum(1 for ln in r.stdout.splitlines() if ln.strip().startswith("["))
        ok(f"Application opérationnelle ({count} module(s) détecté(s))")
        return True
    err("Le test a échoué :")
    print(tail(r.stderr or r.stdout))
    return False


# --------------------------------------------------------------------------- #
# Diagnostic et désinstallation
# --------------------------------------------------------------------------- #


def verify():
    title(f"Diagnostic de {APP_NAME}")
    v = sys.version_info
    (ok if v >= (3, 8) else err)(f"Python {v.major}.{v.minor}.{v.micro}")
    (ok if all((ROOT / f).exists() for f in PROJECT_FILES) else err)("Fichiers du projet")
    (ok if tkinter_available() else warn)("tkinter")
    py = venv_python()
    if py.exists():
        good, _ = imports_ok(py)
        (ok if good else warn)("Environnement virtuel .venv et bibliothèques")
    else:
        warn("Environnement virtuel .venv absent (lance l'installation)")
    port = tor_port()
    if port:
        ok(f"Tor actif (port {port})")
    else:
        warn("Tor non démarré" + (f" ({tor_installed()} trouvé)" if tor_installed() else " / non installé"))
    for _, path in shortcut_targets():
        (ok if path.exists() else warn)(f"Raccourci : {path}")
    return 0


def uninstall():
    title(f"Désinstallation de {APP_NAME}")
    if not ask("Supprimer l'environnement virtuel, les icônes et les raccourcis ?"):
        info("Annulé.")
        return 0
    for kind, path in shortcut_targets():
        if path.exists() or path.is_symlink():
            try:
                shutil.rmtree(path) if kind == "dossier" else path.unlink()
                ok(f"Supprimé : {path}")
            except OSError as exc:
                warn(f"Impossible de supprimer {path} : {exc}")
    for path in (VENV_DIR, ASSETS_DIR):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            ok(f"Supprimé : {path}")
    if ARGS.purge:
        data = Path(os.environ.get("FULL_INFO_HOME") or (Path.home() / ".full_info"))
        if data.exists() and ask(f"Supprimer aussi les archives et réglages ({data}) ? Irréversible.", default=False):
            shutil.rmtree(data, ignore_errors=True)
            ok(f"Supprimé : {data}")
    else:
        info("Tes archives (~/.full_info) sont conservées. Utilise --purge pour les supprimer aussi.")
    info(f"Le dossier du programme ({ROOT}) n'est pas supprimé : efface-le toi-même si besoin.")
    return 0


def install():
    title(f"Installation de {APP_NAME}")
    if not step_python():
        return 1
    step_tkinter()
    py = step_dependencies()
    if py is None:
        return 1
    step_tor()
    step_icons()
    created = step_shortcuts(py)
    tested = step_test(py)

    title("Récapitulatif")
    ok("Bibliothèques installées")
    (ok if created else warn)("Raccourci créé" if created else "Aucun raccourci créé")
    (ok if tested else warn)("Test réussi" if tested else "Test à vérifier")
    print()
    launcher = {"windows": f"le raccourci « {APP_NAME} » du Bureau",
                "macos": f"l'application « {APP_NAME} » du Bureau",
                "linux": f"le raccourci « {APP_NAME} » du Bureau"}[OS]
    print(f"  Pour lancer : double-clique sur {launcher}")
    print(f"  Ou en terminal : {py} {ROOT / 'main.py'}")
    print("  Rappel : démarre Tor (ou Tor Browser) avant de lancer une enquête.")
    return 0 if tested else 1


def parse_args():
    p = argparse.ArgumentParser(description=f"Installateur de {APP_NAME} (Windows / macOS / Linux)")
    p.add_argument("-y", "--oui", action="store_true", help="répond oui à toutes les questions")
    p.add_argument("--verifier", action="store_true", help="diagnostic seul, ne modifie rien")
    p.add_argument("--desinstaller", action="store_true", help="retire raccourcis, icônes et environnement")
    p.add_argument("--purge", action="store_true", help="avec --desinstaller : supprime aussi ~/.full_info")
    p.add_argument("--sans-tor", action="store_true", help="ne pas vérifier / installer Tor")
    p.add_argument("--sans-venv", action="store_true", help="installer dans l'interpréteur courant")
    p.add_argument("--sans-menu", action="store_true", help="raccourci du Bureau uniquement")
    return p.parse_args()


def windows_double_click():
    """True si la console a été ouverte par un double-clic (elle disparaîtrait à la fin)."""
    if OS != "windows":
        return False
    try:
        import ctypes
        buf = (ctypes.c_uint * 4)()
        return ctypes.windll.kernel32.GetConsoleProcessList(buf, 4) <= 2
    except Exception:
        return False


def main():
    global ARGS
    ARGS = parse_args()
    try:
        if ARGS.verifier:
            code = verify()
        elif ARGS.desinstaller:
            code = uninstall()
        else:
            code = install()
    except KeyboardInterrupt:
        print("\nInterrompu.")
        code = 130
    if windows_double_click():
        input("\nAppuyez sur Entrée pour fermer cette fenêtre…")
    return code


if __name__ == "__main__":
    sys.exit(main())