# CALLFLOW: instalación en el servidor Linux

Guía paso a paso para instalar CALLFLOW desde cero en **Ubuntu Server 24.04 LTS**. El porqué de cada decisión
(discos, respaldos, seguridad, HTTPS) está en `docs/DESPLIEGUE_LINUX.md`. Aquí van solo los comandos.

Cómo queda: **Nginx** (HTTPS, puerto 443) → **Gunicorn** (`127.0.0.1:8000`, servicio `callflow`) → Django →
**PostgreSQL** local. Tareas programadas con el **cron** del usuario `callflow`.

| Ruta | Qué hay |
|---|---|
| `/srv/callflow` | Código (clon del repositorio), `.venv` y `.env` |
| `/datos/media` | Archivos subidos (CV, contratos, roles, actas) |
| `/datos/logs` | Registros (`errores.jsonl`, `seguridad.jsonl`, `respaldos.jsonl`, `cron_*.log`) |
| `/respaldos/callflow` | Respaldos (SSD 2) |

> Todos los comandos se escriben como un usuario de TI con `sudo`. Donde dice **como callflow**, anteponer
> `sudo -u callflow` o entrar con `sudo -iu callflow`.

---

## 1. Sistema base

Al instalar Ubuntu: usuario de TI propio (no `root`), OpenSSH activado, y si se decidió, cifrado del disco (LUKS).

```bash
sudo apt update && sudo apt full-upgrade -y
sudo timedatectl set-timezone America/Guayaquil
sudo apt install -y git nginx python3-venv python3-dev build-essential unattended-upgrades ufw
sudo dpkg-reconfigure -plow unattended-upgrades      # actualizaciones de seguridad automáticas
```

**SSH solo con llave:** copiar la llave de cada persona de TI (`ssh-copy-id usuario@servidor` desde su PC), comprobar
que entra **sin** contraseña y recién entonces, en `/etc/ssh/sshd_config`: `PasswordAuthentication no` y
`PermitRootLogin no`; luego `sudo systemctl restart ssh`.

**Firewall** (ajustar las IP de TI):

```bash
sudo ufw default deny incoming
sudo ufw allow 80,443/tcp
sudo ufw allow from 192.168.100.82 to any port 22 proto tcp   # PC de TI
sudo ufw allow from 192.168.100.83 to any port 22 proto tcp   # PC de TI
sudo ufw enable
```

PostgreSQL (5432) **no** se abre: solo lo usa CALLFLOW dentro del servidor.

## 2. PostgreSQL 18

La base actual está en PostgreSQL 18, y un respaldo de la 18 no se restaura en una versión anterior.

```bash
sudo apt install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt install -y postgresql-18
```

Ajustes recomendados para 16 GB de RAM, en `/etc/postgresql/18/main/postgresql.conf`:

```
shared_buffers = 4GB
effective_cache_size = 10GB
work_mem = 16MB
maintenance_work_mem = 512MB
timezone = 'America/Guayaquil'
```

`sudo systemctl restart postgresql`. Se deja `listen_addresses = 'localhost'` (el valor por defecto).

## 3. Usuario, carpetas y código

```bash
sudo adduser --system --group --home /srv/callflow --shell /bin/bash callflow
sudo mkdir -p /datos/media /datos/logs /respaldos/callflow
sudo chown -R callflow:callflow /datos/media /datos/logs /respaldos/callflow /srv/callflow
sudo chmod 750 /datos/media /datos/logs /respaldos/callflow
sudo install -d -o callflow -g callflow -m 750 /var/cache/callflow   # límite de peticiones (también la crea systemd)
sudo chmod 755 /srv/callflow   # Nginx (www-data) tiene que poder entrar a staticfiles/; el .env queda en 600

# como callflow
sudo -iu callflow
git clone https://github.com/Thyago23/CALLFLOW.git /srv/callflow     # la carpeta tiene que estar vacía
cd /srv/callflow && git checkout main
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-servidor.txt
chmod +x scripts/*.sh
exit
```

> Si el repositorio es privado: crear una *deploy key* de solo lectura en GitHub para el usuario `callflow`.

## 4. `.env` del servidor

```bash
sudo -u callflow cp /srv/callflow/despliegue/.env.produccion.ejemplo /srv/callflow/.env
sudo -u callflow chmod 600 /srv/callflow/.env
sudo -u callflow nano /srv/callflow/.env
```

Completar según los comentarios del archivo. Lo **obligatorio** del día de la mudanza: `CLAVE_TARJETAS`, `VAPID_*` y
(si no quieren que todos inicien sesión de nuevo) `DJANGO_SECRET_KEY` tienen que ser **los mismos** de la PC actual.

## 5. Base de datos

```bash
sudo /srv/callflow/scripts/crear_bd.sh
```

Crea `callflow_admin` (superusuario de la empresa), `callflow_app` (el de Django) y la base `callflow`, y escribe las
contraseñas en el `.env`.

- **Instalación vacía** (pruebas): como callflow, `.venv/bin/python manage.py migrate` y `.venv/bin/python manage.py inicializar_datos`.
- **Mudanza** (datos reales): ver la sección 10.

Luego, como callflow:

```bash
cd /srv/callflow
.venv/bin/python manage.py migrate              # siempre con callflow_app (el .env), nunca con el admin
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py verificar_bd
.venv/bin/python manage.py check --deploy
```

`check --deploy` solo debería avisar de HSTS (W004) mientras `DJANGO_HSTS_SEGUNDOS=0`.

## 6. Gunicorn (servicio `callflow`)

```bash
sudo cp /srv/callflow/despliegue/callflow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now callflow
systemctl status callflow
curl -sI http://127.0.0.1:8000/cuentas/ingresar/ -H 'X-Forwarded-Proto: https' | head -1   # 200 o 302
```

Si no arranca: `journalctl -u callflow -n 50`.

## 7. HTTPS y Nginx

**Opción A, dominio propio (recomendada):** certificado de Let's Encrypt con validación DNS, sin abrir nada a
internet. Con el DNS en Cloudflare, por ejemplo:

```bash
sudo apt install -y certbot python3-certbot-dns-cloudflare
# token de la API de Cloudflare con permiso "Zone.DNS: Edit" en /root/.secrets/cloudflare.ini (chmod 600)
sudo certbot certonly --dns-cloudflare --dns-cloudflare-credentials /root/.secrets/cloudflare.ini -d callflow.EJEMPLO.com
```

Con otro proveedor de DNS hay plugins parecidos, o `--manual --preferred-challenges dns` (renovación manual cada
90 días: no recomendado). En el router o DNS de la oficina, `callflow.EJEMPLO.com` apunta a la IP interna del servidor.

**Opción B, CA propia:** con `mkcert` o `step-ca`, instalando el certificado raíz en todas las PCs. Cambiar las
rutas `ssl_certificate` del archivo de Nginx.

Luego:

```bash
sudo cp /srv/callflow/despliegue/nginx-callflow.conf /etc/nginx/sites-available/callflow
sudo sed -i 's/callflow.EJEMPLO.com/callflow.multivisa.com.ec/g' /etc/nginx/sites-available/callflow   # el nombre real
sudo ln -s /etc/nginx/sites-available/callflow /etc/nginx/sites-enabled/callflow
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

## 8. Tareas programadas, registros y respaldos

```bash
sudo crontab -u callflow /srv/callflow/despliegue/crontab
sudo cp /srv/callflow/despliegue/logrotate-callflow /etc/logrotate.d/callflow
sudo -u callflow /srv/callflow/scripts/respaldar.sh --tipo diario    # primer respaldo, a mano
ls -lh /respaldos/callflow
```

Si el SSD de respaldos no está montado en `/respaldos`, agregarlo a `/etc/fstab` antes (por UUID: `sudo blkid`).
Para la copia fuera de la oficina, agregar `--copiar-a /mnt/<disco externo>/callflow` a la línea del respaldo diario
del crontab, o copiar a mano cada semana con `rsync -a /respaldos/callflow/ /mnt/<disco externo>/callflow/`.
**Guardar una copia del `.env` fuera del servidor** (sin `CLAVE_TARJETAS` las tarjetas guardadas no se pueden leer).

## 9. Comprobar

1. Desde una PC de la oficina: `https://callflow.<dominio>` abre sin advertencias y se puede iniciar sesión.
2. Abrir un CV, descargar un rol de pago y generar un acta PDF.
3. **TI › Servidor**: discos, último respaldo y sin errores nuevos. **TI › Conectados**: cada PC con su IP real
   (si todas salen como `127.0.0.1`, falta `TI_PROXY_CONFIABLE=True`).
4. Activar las push desde la campana y mandarse un mensaje de prueba.
5. Prueba de restauración (repetirla una vez al mes):
   ```bash
   sudo -u callflow /srv/callflow/scripts/restaurar_bd.sh "$(ls -t /respaldos/callflow/callflow_2*.dump | head -1)" --base callflow_prueba --verificar
   sudo -u callflow /srv/callflow/scripts/restaurar_bd.sh --borrar callflow_prueba
   ```

## 10. Mudanza desde la PC actual

1. Avisar a todos y **detener** CALLFLOW en la PC actual (detener la tarea «CALLFLOW servidor» y las demás de `\CALLFLOW\`).
2. En la PC actual: `powershell -ExecutionPolicy Bypass -File scripts\respaldar_bd.ps1` → deja `callflow_<fecha>.dump` y
   `callflow_media_<fecha>.zip`.
3. Copiar los dos archivos al servidor (USB o `scp`) en `/respaldos/callflow/`, y los valores de `CLAVE_TARJETAS`,
   `VAPID_*` y `DJANGO_SECRET_KEY` al `.env` del servidor.
4. En el servidor, con la base vacía recién creada por `crear_bd.sh` (paso 5, **sin** `migrate`):
   ```bash
   sudo -u callflow /srv/callflow/scripts/restaurar_bd.sh /respaldos/callflow/callflow_<fecha>.dump --base callflow
   sudo -u callflow /srv/callflow/scripts/restaurar_bd.sh --media /respaldos/callflow/callflow_media_<fecha>.zip
   ```
5. Como callflow: `migrate`, `collectstatic`, `verificar_bd` (paso 5) y `sudo systemctl restart callflow`.
6. Comprobar todo lo del paso 9.
7. Recién entonces dar la dirección nueva a todos. Dejar la PC vieja **intacta al menos un mes**.

## 11. Actualizar el código

```bash
sudo -u callflow /srv/callflow/scripts/respaldar.sh --tipo hora    # respaldo antes de migrar
sudo -iu callflow
cd /srv/callflow && git pull
.venv/bin/pip install -r requirements-servidor.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
exit
sudo systemctl reload callflow     # reinicia los procesos de Gunicorn sin cortar a nadie
```

Las plantillas y el código Python solo se actualizan con el `reload`. CSS y JS, con `collectstatic`.
