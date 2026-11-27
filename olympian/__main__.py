"""Start Olympian NMS Save Editor: local server + an app-style window (Microsoft Edge --app, included in Windows 11)."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .server import app_data_dir, serve

IDLE_SHUTDOWN = 180  # safety net: seconds without any ping (minimized windows ping less often)
BYE_GRACE = 6        # window closed (or reloaded): exit if no new ping arrives within this time


def find_edge() -> str | None:
    """Edge, or on macOS / Linux a Chromium browser that takes --app (a window without tabs); else None."""
    cands = [shutil.which('msedge')]
    if sys.platform == 'darwin':
        cands += [f'/Applications/{n}.app/Contents/MacOS/{n}' for n in ('Microsoft Edge', 'Google Chrome', 'Chromium', 'Brave Browser')]
    elif sys.platform != 'win32':
        cands += [shutil.which(n) for n in ('microsoft-edge', 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'brave-browser')]
    for env in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA'):
        base = os.environ.get(env)
        if base:
            cands.append(str(Path(base) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'))
    return next((c for c in cands if c and Path(c).is_file()), None)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='olympian')
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('--no-window', action='store_true', help='solo servidor (pruebas)')
    ap.add_argument('--folder', help='save folder (optional)')
    ap.add_argument('--log', help='escribir mensajes y errores en este archivo')
    ap.add_argument('--check', action='store_true', help='solo comprobar que todo carga y salir')
    args = ap.parse_args(argv)

    if args.log or sys.stdout is None:  # pythonw has no console: keep a log file
        log_path = args.log or str(Path(__file__).resolve().parent.parent / 'olympian_log.txt')
        fh = open(log_path, 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = fh
    if args.check:
        from .core import hgfile
        from .core.keys import mapping
        mapping()
        print(f'check ok: python {sys.version.split()[0]}, lz4={hgfile.HAVE_LZ4}', flush=True)
        return 0

    srv, app = serve(args.port)
    if args.folder:
        app.settings['folder'] = args.folder
    url = f'http://127.0.0.1:{srv.server_address[1]}/#t={app.token}'
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f'Olympian NMS Save Editor {url}', flush=True)

    if not args.no_window:
        edge = find_edge()
        if edge:
            profile = Path(os.environ.get('LOCALAPPDATA') or app_data_dir()) / 'OlympianNMS' / 'edge-profile'
            subprocess.Popen([edge, f'--app={url}', f'--user-data-dir={profile}', '--window-size=1280,860',
                              '--no-first-run', '--disable-features=Translate,msTranslate',
                              '--disable-background-timer-throttling', '--disable-renderer-backgrounding'])
        else:
            webbrowser.open(url)

    try:
        while True:
            time.sleep(1)
            now = time.time()
            if app.bye_at and app.live <= 0 and now - app.bye_at > BYE_GRACE:
                break
            if not args.no_window and now - app.last_ping > IDLE_SHUTDOWN:
                break
    except KeyboardInterrupt:
        pass
    app.stopping = True
    srv.shutdown()


if __name__ == '__main__':
    sys.exit(main())
