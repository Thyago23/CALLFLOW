#!/usr/bin/env bash
# Carga una copia de la base de CALLFLOW en la base LOCAL de desarrollo (contenedor Docker callflow-db, 127.0.0.1:5436).
# BORRA la base local y la reemplaza por la copia. Nunca toca la base de producción.
#
# Uso:
#   bash scripts/cargar_copia_local.sh ruta/al/callflow_AAAAMMDD_HHMMSS.dump   (respaldo del servidor, generado con pg_dump -Fc)
#   bash scripts/cargar_copia_local.sh --desde 192.168.100.83                  (saca la copia por red con callflow_app)
#
# Si el contenedor no existe, lo crea. Las credenciales se toman del .env (DB_PASSWORD, DB_ADMIN_USER, DB_ADMIN_PASSWORD).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${DB_PASSWORD:?falta DB_PASSWORD en .env}" "${DB_ADMIN_USER:?falta DB_ADMIN_USER en .env}" "${DB_ADMIN_PASSWORD:?falta DB_ADMIN_PASSWORD en .env}"
contenedor=callflow-db
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

if ! docker inspect "$contenedor" >/dev/null 2>&1; then
    echo "Creando el contenedor $contenedor (PostgreSQL 18)..."
    docker run -d --name "$contenedor" --restart unless-stopped -p 127.0.0.1:5436:5432 \
        -v callflow-db-datos:/var/lib/postgresql \
        -e POSTGRES_USER="$DB_ADMIN_USER" -e POSTGRES_PASSWORD="$DB_ADMIN_PASSWORD" -e POSTGRES_DB=postgres postgres:18 >/dev/null
fi
docker start "$contenedor" >/dev/null
for _ in $(seq 30); do docker exec "$contenedor" pg_isready -q -U "$DB_ADMIN_USER" && break; sleep 1; done

case "${1:-}" in
    --desde)
        host="${2:?indique la IP, por ejemplo --desde 192.168.100.83}"
        echo "Sacando la copia desde $host..."
        docker run --rm --network host -u "$(id -u):$(id -g)" -v "$tmp":/d -e PGPASSWORD="$DB_PASSWORD" postgres:18 \
            pg_dump -h "$host" -U callflow_app -d callflow -Fc -f /d/copia.dump
        archivo="$tmp/copia.dump" ;;
    "") echo "Falta el archivo .dump o --desde <IP>. Vea el encabezado del script." >&2; exit 1 ;;
    *)  archivo="$1"; [ -s "$archivo" ] || { echo "No existe o está vacío: $archivo" >&2; exit 1; } ;;
esac

echo "Reemplazando la base local..."
docker exec -i -e PGPASSWORD="$DB_ADMIN_PASSWORD" "$contenedor" psql -q -v ON_ERROR_STOP=1 -U "$DB_ADMIN_USER" -d postgres -v pw="$DB_PASSWORD" <<'SQL'
\o /dev/null
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'callflow' AND pid <> pg_backend_pid();
\o
DROP DATABASE IF EXISTS callflow;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'callflow_app') THEN CREATE ROLE callflow_app LOGIN; END IF; END $$;
ALTER ROLE callflow_app PASSWORD :'pw';
CREATE DATABASE callflow OWNER callflow_app;
SQL
docker cp "$archivo" "$contenedor":/tmp/copia.dump
# Se restaura como callflow_app para que sea dueño de todas las tablas, igual que en producción.
docker exec -e PGPASSWORD="$DB_PASSWORD" "$contenedor" \
    pg_restore -h localhost -U callflow_app -d callflow --no-owner --no-acl --exit-on-error /tmp/copia.dump
docker exec "$contenedor" rm -f /tmp/copia.dump
echo "Listo. Base local cargada desde: ${2:-$archivo}"
