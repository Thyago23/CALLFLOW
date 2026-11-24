#!/usr/bin/env bash
# Crea la base de CALLFLOW en el servidor Linux: superusuario de la empresa (callflow_admin), usuario de la aplicación
# (callflow_app, sin privilegios) y la base. Versión Linux de crear_bd.ps1; usa el mismo crear_bd.sql.
#
# Se corre como root (entra a PostgreSQL como el usuario de sistema postgres, sin contraseña):
#   sudo /srv/callflow/scripts/crear_bd.sh
# La contraseña de callflow_app se toma de DB_PASSWORD del .env; si dice «cambie-esto», se genera una y se guarda.
# La de callflow_admin se genera y se agrega al .env (DB_ADMIN_USER / DB_ADMIN_PASSWORD). Es idempotente.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="$RAIZ/.env"
[[ -f "$ENV" ]] || { echo "No existe $ENV. Copie despliegue/.env.produccion.ejemplo como .env primero." >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo "Correr con sudo." >&2; exit 1; }

leer() { grep -E "^$1=" "$ENV" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true; }
clave_nueva() { python3 -c "import secrets; print(secrets.token_urlsafe(24))"; }
poner() {  # poner CLAVE valor: reemplaza la línea o la agrega al final
    if grep -qE "^$1=" "$ENV"; then sed -i "s|^$1=.*|$1=$2|" "$ENV"; else printf '\n%s=%s\n' "$1" "$2" >> "$ENV"; fi
}

BD="$(leer DB_NAME)"; BD="${BD:-callflow}"
APP="$(leer DB_USER)"; APP="${APP:-callflow_app}"
APP_CLAVE="$(leer DB_PASSWORD)"
if [[ -z "$APP_CLAVE" || "$APP_CLAVE" == "cambie-esto" ]]; then
    APP_CLAVE="$(clave_nueva)"; poner DB_PASSWORD "$APP_CLAVE"; echo "Se generó la contraseña de $APP y se guardó en .env"
fi
ADMIN="$(leer DB_ADMIN_USER)"; ADMIN="${ADMIN:-callflow_admin}"
ADMIN_CLAVE="$(leer DB_ADMIN_PASSWORD)"
if [[ -z "$ADMIN_CLAVE" ]]; then
    ADMIN_CLAVE="$(clave_nueva)"; poner DB_ADMIN_USER "$ADMIN"; poner DB_ADMIN_PASSWORD "$ADMIN_CLAVE"
    echo "Se guardó la contraseña de $ADMIN en .env"
fi

# Se copia el SQL a /tmp porque el usuario postgres no puede leer dentro de /srv/callflow.
SQL="$(mktemp /tmp/crear_bd.XXXXXX.sql)"; trap 'rm -f "$SQL"' EXIT
sed '1s/^\xEF\xBB\xBF//' "$RAIZ/scripts/crear_bd.sql" > "$SQL"; chmod 644 "$SQL"
sudo -u postgres env PGCLIENTENCODING=UTF8 psql -d postgres -X -q \
    -v "admin_user=$ADMIN" -v "admin_password=$ADMIN_CLAVE" \
    -v "app_user=$APP" -v "app_password=$APP_CLAVE" -v "db_name=$BD" -f "$SQL"

chmod 600 "$ENV"
echo
echo "Listo. Siguiente paso (como callflow): .venv/bin/python manage.py migrate  (y en una instalación vacía: inicializar_datos)"
