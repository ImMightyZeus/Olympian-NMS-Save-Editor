#!/bin/sh
# Olympian NMS Save Editor - lanzador para Linux (Steam / Proton, Steam Deck) y macOS.
# Launcher for Linux (Steam / Proton, Steam Deck) and macOS.
# Uso / usage:  sh run_olympian_nmsse.sh   (en macOS tambien con doble clic en run_olympian_nmsse.command)
cd "$(dirname "$0")" || exit 1
LOG="$(pwd)/olympian_log.txt"
echo "==== $(date) ====" > "$LOG"

ok_python() { "$1" -c "import sys, encodings, venv; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1; }

if [ -x .venv/bin/python ] && .venv/bin/python -c "import encodings, olympian" >>"$LOG" 2>&1; then
    :
else
    rm -rf .venv
    PY=""
    for c in python3.13 python3.12 python3.11 python3; do
        if command -v "$c" >/dev/null 2>&1 && ok_python "$c"; then PY="$c"; break; fi
    done
    if [ -z "$PY" ]; then
        echo "Olympian necesita Python 3.11 o mas nuevo (python.org, o el gestor de paquetes de tu sistema)."
        echo "Olympian needs Python 3.11 or newer (python.org, or your system's package manager)."
        exit 1
    fi
    echo "Python: $PY" >>"$LOG"
    echo "Preparando Olympian por primera vez... / Setting up Olympian for the first time..."
    if ! "$PY" -m venv .venv >>"$LOG" 2>&1; then
        echo "No pude crear el entorno (en Debian/Ubuntu: sudo apt install python3-venv). Detalles en olympian_log.txt"
        echo "Couldn't create the environment (on Debian/Ubuntu: sudo apt install python3-venv). Details in olympian_log.txt"
        exit 1
    fi
    # lz4 acelera la lectura; sin el, Olympian usa su lector propio / lz4 is optional (faster reading)
    .venv/bin/python -m pip install -q lz4 >>"$LOG" 2>&1 || true
fi

if ! .venv/bin/python -m olympian --check >>"$LOG" 2>&1; then
    echo "Olympian se cerro con un error. Pasame el contenido de olympian_log.txt"
    echo "Olympian stopped with an error. Please send the contents of olympian_log.txt"
    exit 1
fi
exec .venv/bin/python -m olympian --log "$LOG"
