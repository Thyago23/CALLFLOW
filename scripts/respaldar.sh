#!/usr/bin/env bash
# Copia de seguridad de CALLFLOW en el servidor Linux. Versión Linux de respaldar_bd.ps1 (mismos nombres de archivo y
# mismo historial respaldos.jsonl, así TI › Respaldos y TI › Servidor lo ven igual). Lo corre el cron de callflow
# (despliegue/crontab).
#
#   scripts/respaldar.sh --tipo diario   base (callflow_<fecha>.dump) + archivos (callflow_media_<fecha>.zip). Conserva 30.
#                                        El del día 1 de cada mes se guarda además en mensual/ durante 12 meses.
#   scripts/respaldar.sh --tipo hora     solo la base (callflow_hora_<fecha>.dump). Conserva 50.
#   --copiar-a /mnt/usb/callflow         además copia lo generado a un segundo lugar (disco USB, NAS montado).
#   --conservar N                        cambia cuántos se conservan.
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV="$RAIZ/.env"
TIPO="diario"; COPIAR_A=""; CONSERVAR=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tipo) TIPO="$2"; shift 2 ;;
        --copiar-a) COPIAR_A="$2"; shift 2 ;;
        --conservar) CONSERVAR="$2"; shift 2 ;;
        *) echo "Opción desconocida: $1" >&2; exit 2 ;;
    esac
done
[[ "$TIPO" == "diario" || "$TIPO" == "hora" ]] || { echo "--tipo debe ser diario u hora" >&2; exit 2; }
[[ "$CONSERVAR" -gt 0 ]] || CONSERVAR=$([[ "$TIPO" == "hora" ]] && echo 50 || echo 30)

leer() { grep -E "^$1=" "$ENV" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true; }
LOGS="$(leer LOGS_DIR)"; LOGS="${LOGS:-$RAIZ/logs}"
DESTINO="$(leer CARPETA_RESPALDOS)"; DESTINO="${DESTINO:-$RAIZ/respaldos}"
MEDIA="$(leer MEDIA_ROOT)"; MEDIA="${MEDIA:-$RAIZ/media}"
INICIO_S=$(date +%s); INICIO_ISO="$(date +%Y-%m-%dT%H:%M:%S%:z)"
SELLO="$(date +%Y%m%d_%H%M%S)"
GENERADOS=(); COPIA=""

json() {  # json clave valor clave valor ... → una línea JSON (con python para escapar bien los textos)
    python3 -c 'import json,sys; a=sys.argv[1:]; print(json.dumps({a[i]: (int(a[i+1]) if a[i] in ("tamano","duracion_s") else a[i+1]) for i in range(0,len(a),2)}, ensure_ascii=False))' "$@"
}
anotar_respaldo() {  # resultado mensaje → historial de TI › Respaldos
    mkdir -p "$LOGS"
    local tam=0 nombres=() g
    for g in "${GENERADOS[@]}"; do [[ -f "$g" ]] && { tam=$((tam + $(stat -c %s "$g"))); nombres+=("$(basename "$g")"); }; done
    local lista; lista="$(IFS=,; echo "${nombres[*]:-}")"; lista="${lista//,/, }"
    json fecha "$INICIO_ISO" tipo "$TIPO" resultado "$1" archivo "$lista" tamano "$tam" \
         duracion_s "$(( $(date +%s) - INICIO_S ))" copia "$COPIA" destino_copia "$COPIAR_A" \
         equipo "$(hostname)" mensaje "$2" >> "$LOGS/respaldos.jsonl"
}
anotar_error() {  # mismo formato que apps/ti/registro_errores.py → TI › Servidor
    mkdir -p "$LOGS"
    json fecha "$(date +%Y-%m-%dT%H:%M:%S%:z)" nivel ERROR origen "respaldo.$TIPO" \
         mensaje "Falló el respaldo automático: $1" >> "$LOGS/errores.jsonl"
}
fallar() { echo "ERROR: $1" >&2; anotar_error "$1"; anotar_respaldo "fallo" "$1"; exit 1; }
podar() {  # podar carpeta patrón: deja los $CONSERVAR más nuevos
    ls -1t "$1"/$2 2>/dev/null | tail -n +$((CONSERVAR + 1)) | xargs -r rm -f --
}

[[ -f "$ENV" ]] || fallar "No existe $ENV"
mkdir -p "$DESTINO" || fallar "No se pudo crear la carpeta $DESTINO"

BD="$(leer DB_NAME)"; BD="${BD:-callflow}"
HOST="$(leer DB_HOST)"; HOST="${HOST:-localhost}"
PUERTO="$(leer DB_PORT)"; PUERTO="${PUERTO:-5432}"
USUARIO="$(leer DB_ADMIN_USER)"; CLAVE="$(leer DB_ADMIN_PASSWORD)"
[[ -n "$USUARIO" ]] || { USUARIO="$(leer DB_USER)"; CLAVE="$(leer DB_PASSWORD)"; }

PREFIJO=$([[ "$TIPO" == "hora" ]] && echo "callflow_hora_" || echo "callflow_")
ARCHIVO="$DESTINO/$PREFIJO$SELLO.dump"
GENERADOS=("$ARCHIVO")
if ! PGPASSWORD="$CLAVE" pg_dump -h "$HOST" -p "$PUERTO" -U "$USUARIO" -d "$BD" -Fc -Z 6 -f "$ARCHIVO"; then
    rm -f "$ARCHIVO"
    fallar "pg_dump terminó con error. Si dice 'server version mismatch', instale el cliente de PostgreSQL de la misma versión que el servidor."
fi
pg_restore --list "$ARCHIVO" > /dev/null || fallar "El respaldo $ARCHIVO no es legible"
chmod 600 "$ARCHIVO"
echo "Respaldo de la base: $ARCHIVO"

if [[ "$TIPO" == "diario" ]]; then
    if [[ -d "$MEDIA" ]]; then
        ZIP="$DESTINO/callflow_media_$SELLO.zip"
        # Mismo formato que en Windows: un .zip con las subcarpetas de MEDIA_ROOT (la base guarda rutas relativas).
        python3 -c 'import shutil,sys; shutil.make_archive(sys.argv[1][:-4], "zip", sys.argv[2])' "$ZIP" "$MEDIA" \
            || fallar "No se pudo comprimir la carpeta de archivos $MEDIA"
        chmod 600 "$ZIP"; GENERADOS+=("$ZIP")
        echo "Respaldo de los archivos: $ZIP"
    else
        echo "AVISO: no existe la carpeta de archivos $MEDIA; solo se respaldó la base." >&2
    fi
    # Mensuales: el diario del día 1 se guarda aparte durante 12 meses.
    if [[ "$(date +%d)" == "01" ]]; then
        mkdir -p "$DESTINO/mensual" && cp -p "${GENERADOS[@]}" "$DESTINO/mensual/"
        ls -1t "$DESTINO"/mensual/callflow_2*.dump 2>/dev/null | tail -n +13 | xargs -r rm -f --
        ls -1t "$DESTINO"/mensual/callflow_media_*.zip 2>/dev/null | tail -n +13 | xargs -r rm -f --
    fi
    PATRONES=("callflow_2*.dump" "callflow_media_*.zip")
else
    PATRONES=("callflow_hora_*.dump")
fi

if [[ -n "$COPIAR_A" ]]; then
    # Si el segundo destino no está (USB desconectado), el respaldo local igual queda; solo se anota el aviso.
    if mkdir -p "$COPIAR_A" 2>/dev/null && cp -p "${GENERADOS[@]}" "$COPIAR_A/"; then
        for p in "${PATRONES[@]}"; do podar "$COPIAR_A" "$p"; done
        COPIA="ok"; echo "Copia en el segundo destino: $COPIAR_A"
    else
        COPIA="fallo"
        anotar_error "el respaldo local se hizo, pero no se pudo copiar a '$COPIAR_A'"
        echo "AVISO: no se pudo copiar a $COPIAR_A" >&2
    fi
fi

for p in "${PATRONES[@]}"; do podar "$DESTINO" "$p"; done
anotar_respaldo "ok" "$([[ "$COPIA" == "fallo" ]] && echo "El respaldo local se hizo, pero no se pudo copiar a '$COPIAR_A'." || true)"
