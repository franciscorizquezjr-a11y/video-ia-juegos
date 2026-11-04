"""Start/reuse this workspace's local app and open Chrome after readiness."""
import argparse
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
PORTS = (8004, *[p for p in range(8000, 8010) if p != 8004])
APP_ID = "ia-video-studio"
HTTP = build_opener(ProxyHandler({}))


def workspace_id(root=ROOT):
    return hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:24]


def probe_server(port, root=ROOT):
    try:
        with HTTP.open(f"http://127.0.0.1:{int(port)}/api/health", timeout=.5) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read(4096))
        if data.get("app") == APP_ID and data.get("workspace_id") == workspace_id(root):
            return {"port": int(port), "url": f"http://127.0.0.1:{int(port)}/", "pid": data.get("pid")}
    except (OSError, ValueError, URLError):
        pass
    return None


def find_existing(root=ROOT, ports=PORTS):
    for port in ports:
        server = probe_server(port, root)
        if server:
            return server
    return None


def find_available_port(ports=PORTS):
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Los puertos 8000–8009 están ocupados. Cierra una instancia y vuelve a abrir la app.")


def chrome_path():
    for env, tail in [("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
                      ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
                      ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe")]:
        base = os.environ.get(env)
        if base and (Path(base) / tail).is_file():
            return Path(base) / tail
    candidate = shutil.which("chrome")
    return Path(candidate) if candidate else None


def open_browser(url):
    chrome = chrome_path()
    if chrome:
        try:
            subprocess.Popen([str(chrome), "--new-tab", url], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
        except OSError:
            logging.exception("Chrome no pudo iniciarse; se usará el navegador predeterminado.")
    if not webbrowser.open(url, new=2):
        raise RuntimeError("La app está lista, pero no se pudo abrir el navegador. Abre " + url)


def wait_until_ready(port, process=None, root=ROOT, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        server = probe_server(port, root)
        if server:
            return server
        if process is not None and process.poll() is not None:
            raise RuntimeError("El servidor no pudo arrancar. Revisa logs/app_server.log dentro del proyecto.")
        time.sleep(.3)
    raise RuntimeError("La app tarda demasiado en iniciar. Revisa logs/app_server.log y vuelve a abrirla.")


class StartupLock:
    """OS lock is released even if a launcher exits unexpectedly."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.file.seek(0, 2) == 0:
            self.file.write(b"0"); self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.acquired = False
        return self

    def __exit__(self, *args):
        if self.acquired:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


def start_or_reuse(root=ROOT):
    root = Path(root).resolve()
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with StartupLock(logs / "app_start.lock") as lock:
        if not lock.acquired:
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                server = find_existing(root)
                if server:
                    return server
                time.sleep(.5)
            raise RuntimeError("Hay otro arranque en curso. Revisa logs/launcher.log.")
        existing = find_existing(root)
        if existing:
            return existing
        python = root / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            raise RuntimeError("No se encuentra .venv/Scripts/python.exe. Revisa la instalación del entorno del proyecto.")
        port = find_available_port()
        with (logs / "app_server.log").open("a", encoding="utf-8") as logfile:
            logfile.write("\n--- Arranque local del servidor ---\n"); logfile.flush()
            process = subprocess.Popen([str(python), "-m", "uvicorn", "web_server:app", "--host", "127.0.0.1",
                                        "--port", str(port), "--http", "h11", "--no-access-log"],
                                       cwd=root, stdin=subprocess.DEVNULL, stdout=logfile, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        server = wait_until_ready(port, process, root)
        temporary = logs / "launcher_state.tmp"
        temporary.write_text(json.dumps(server, indent=2), encoding="utf-8")
        temporary.replace(logs / "launcher_state.json")
        return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true", help="Preparar servidor sin abrir una pestaña")
    parser.add_argument("--status", action="store_true", help="Consultar estado sin arrancar servidor")
    args = parser.parse_args(argv)
    LOGS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOGS / "launcher.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.status:
            server = find_existing()
            if sys.stdout:
                print(json.dumps(server or {"running": False}))
            return 0 if server else 1
        server = start_or_reuse()
        logging.info("App lista: %s (PID %s)", server["url"], server["pid"])
        if not args.no_browser:
            open_browser(server["url"])
        if sys.stdout:
            print(json.dumps(server))
        return 0
    except Exception as exc:
        logging.exception("No se pudo abrir la aplicación")
        message = str(exc)
        if sys.stderr:
            print(message, file=sys.stderr)
        if os.name == "nt" and not args.no_browser and not args.status:
            ctypes.windll.user32.MessageBoxW(0, message, "IA Video Studio", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
