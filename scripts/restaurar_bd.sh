#!/usr/bin/env bash
# Restaura un respaldo de CALLFLOW en una base NUEVA (por defecto callflow_restaurada), sin tocar la base en uso.
# Versión Linux de restaurar_bd.ps1. Sirve para la prueba mensual de restauración y para la mudanza.
#
#   scripts/restaurar_bd.sh /respaldos/callflow/callflow_20261105_233000.dump
#   scripts/restaurar_bd.sh archivo.dump --base callflow_prueba
#   scripts/restaurar_bd.sh archivo.dump --base callflow --verificar     (mudanza: base vacía recién creada)
#   scripts/restaurar_bd.sh --borrar callflow_prueba                     (borra una base de prueba al terminar)
#   scripts/restaurar_bd.sh --media callflow_media_<fecha>.zip           (archivos subidos → MEDIA_ROOT del .env)
#
# Con --verificar corre manage.py verificar_bd contra la base restaurada.
# No usa --disable-triggers: los triggers de inmutabilidad y auditoría se restauran tal cual.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="$RAIZ/.env"
leer() { grep -E "^$1=" "$ENV" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true; }

ARCHIVO=""; BASE="callflow_restaurada"; VERIFICAR=0; BORRAR=""; MEDIA_ZIP=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --base) BASE="$2"; shift 2 ;;
        --verificar) VERIFICAR=1; shift ;;
        --borrar) BORRAR="$2"; shift 2 ;;
        --media) MEDIA_ZIP="$2"; shift 2 ;;
        -*) echo "Opción desconocida: $1" >&2; exit 2 ;;
        *) ARCHIVO="$1"; shift ;;
    esac
done

if [[ -n "$MEDIA_ZIP" ]]; then
    # Los .zip de Windows (Compress-Archive) guardan las rutas con «\»: se convierten a «/» para recrear las subcarpetas.
    MEDIA="$(leer MEDIA_ROOT)"; MEDIA="${MEDIA:-$RAIZ/media}"
    python3 - "$MEDIA_ZIP" "$MEDIA" <<'PY'
import os, sys, zipfile
origen, destino = sys.argv[1], os.path.realpath(sys.argv[2])
with zipfile.ZipFile(origen) as z:
    n = 0
    for info in z.infolist():
        nombre = info.filename.replace("\\", "/").lstrip("/")
        ruta = os.path.realpath(os.path.join(destino, nombre))
        if not ruta.startswith(destino + os.sep):
            sys.exit(f"Ruta fuera de la carpeta: {info.filename}")
        if nombre.endswith("/"):
            os.makedirs(ruta, exist_ok=True); continue
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with z.open(info) as f, open(ruta, "wb") as g:
            g.write(f.read())
        n += 1
print(f"{n} archivos extraídos en {destino}")
PY
    exit 0
fi

ADMIN="$(leer DB_ADMIN_USER)"; [[ -n "$ADMIN" ]] || { echo "Se necesita DB_ADMIN_USER en el .env (lo crea scripts/crear_bd.sh)" >&2; exit 1; }
export PGPASSWORD="$(leer DB_ADMIN_PASSWORD)"
HOST="$(leer DB_HOST)"; HOST="${HOST:-localhost}"
PUERTO="$(leer DB_PORT)"; PUERTO="${PUERTO:-5432}"
APP="$(leer DB_USER)"; APP="${APP:-callflow_app}"
EN_USO="$(leer DB_NAME)"; EN_USO="${EN_USO:-callflow}"
COMUN=(-h "$HOST" -p "$PUERTO" -U "$ADMIN")

if [[ -n "$BORRAR" ]]; then
    [[ "$BORRAR" != "$EN_USO" ]] || { echo "No se borra la base en uso ($EN_USO)." >&2; exit 1; }
    dropdb "${COMUN[@]}" --if-exists "$BORRAR"; echo "Base $BORRAR borrada."; exit 0
fi

[[ -n "$ARCHIVO" && -f "$ARCHIVO" ]] || { echo "Indique un archivo .dump que exista." >&2; exit 1; }

if [[ "$BASE" == "$EN_USO" ]]; then
    # Solo se permite si la base está vacía (recién creada por crear_bd.sh, el día de la mudanza).
    TABLAS="$(psql "${COMUN[@]}" -d "$BASE" -XAtc "SELECT count(*) FROM pg_tables WHERE schemaname='public'")"
    [[ "$TABLAS" == "0" ]] || { echo "La base $BASE ya tiene datos. Restaure en otra base y revise primero." >&2; exit 1; }
else
    createdb "${COMUN[@]}" -T template0 -E UTF8 "$BASE" || { echo "No se pudo crear la base $BASE (¿ya existe?)" >&2; exit 1; }
fi

# Preparar la base igual que crear_bd.sql: extensión y esquema del usuario de la aplicación.
psql "${COMUN[@]}" -d "$BASE" -X -q -v ON_ERROR_STOP=1 <<SQL
CREATE EXTENSION IF NOT EXISTS btree_gist;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO "$APP";
GRANT USAGE, CREATE ON SCHEMA public TO "$APP";
REVOKE ALL ON DATABASE "$BASE" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE "$BASE" TO "$APP";
ALTER DATABASE "$BASE" SET timezone TO 'America/Guayaquil';
SQL

pg_restore "${COMUN[@]}" -d "$BASE" --no-owner --no-comments --role "$APP" --exit-on-error "$ARCHIVO"
echo "Respaldo restaurado en la base '$BASE'."

if [[ $VERIFICAR -eq 1 ]]; then
    unset PGPASSWORD
    (cd "$RAIZ" && DB_NAME="$BASE" .venv/bin/python manage.py verificar_bd)
fi
[[ "$BASE" == "$EN_USO" ]] || echo "Al terminar de revisarla:  scripts/restaurar_bd.sh --borrar $BASE"
