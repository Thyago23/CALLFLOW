# CALLFLOW: paso a servidor Linux

Documento de trabajo para llevar CALLFLOW desde la PC de desarrollo (Windows) a un servidor Linux propio en la oficina de Multivisa. Sirve como punto de partida para seguir preguntando y avanzando: cada sección indica qué está **decidido**, qué está **pendiente** y qué hay que **construir** en el proyecto.

Última actualización: 12/11/2026

---

## 1. Decisiones tomadas

| Tema | Decisión |
|---|---|
| Ubicación del servidor | **En la oficina**, conectado a la misma red local que las PCs. |
| Hardware | i7 de última generación, 16 GB de RAM, 2 TB de almacenamiento. |
| Sistema operativo | **Ubuntu Server 24.04 LTS**, sin entorno gráfico. Soporte hasta 2029 (ampliable a 2034 con Ubuntu Pro gratuito). |
| Usuarios | Unas 100 personas conectadas a la vez. |
| Respaldos | Un **segundo SSD** dedicado solo a respaldos. |
| Datos | Toda la base de datos y todos los archivos: CV, cédulas, contratos, roles de pago y actas de TI. RR. HH. los sube directamente. |

### ¿Por qué Ubuntu Server 24.04 LTS?

- Es la distribución con más documentación y guías en español. Cualquier problema ya está resuelto en internet.
- Trae Python 3.12, y PostgreSQL 17 se instala desde el repositorio oficial de PostgreSQL (`apt.postgresql.org`), la misma versión que usan hoy.
- Es LTS: recibe actualizaciones de seguridad durante 5 años sin cambiar de versión.
- Alternativa válida: **Debian 12**. Es más conservadora y casi idéntica en el uso, pero tiene menos guías. Para un equipo que empieza con Linux, Ubuntu es más fácil.

---

## 2. Cómo queda armado

```
 PCs de la oficina (≈100, la mayoría por Wi-Fi)
        │  https://callflow.<dominio>  (red local)
        ▼
 ┌──────────────── Servidor Ubuntu ────────────────┐
 │  Nginx  (puerto 443, HTTPS)                     │
 │    ├─ /static/  → archivos de diseño (directo)  │
 │    └─ resto     → Gunicorn (127.0.0.1:8000)     │
 │                    └─ Django / CALLFLOW         │
 │                         ├─ PostgreSQL 18 (local)│
 │                         └─ /datos/media (CV…)   │
 │  cron: push, agenda, salidas, repetidos, respaldo│
 └─────────────────────────────────────────────────┘
        │ respaldo diario
        ▼
   SSD 2 (/respaldos)  ──► copia semanal a disco externo fuera de la oficina
```

- **Nginx** recibe a los usuarios, maneja HTTPS y entrega CSS/JS/imágenes.
- **Gunicorn** ejecuta CALLFLOW con varios procesos en paralelo. Reemplaza al `runserver`, que no sirve para producción.
- **systemd** arranca todo solo al encender el servidor y lo reinicia si se cae.
- Los **CV, roles de pago y demás documentos NO se sirven directo por Nginx**. Siguen pasando por CALLFLOW, que verifica permisos antes de entregarlos, como ahora.

### Distribución de discos

| Disco | Punto de montaje | Contenido |
|---|---|---|
| SSD principal (2 TB) | `/` | Sistema, `/srv/callflow` (código), PostgreSQL (`/var/lib/postgresql`) |
| SSD principal | `/datos/media` | Archivos subidos (`MEDIA_ROOT`) |
| SSD principal | `/datos/logs` | Registro de errores (`LOGS_DIR`) |
| SSD 2 (respaldos) | `/respaldos` | Respaldos de base + archivos (`CARPETA_RESPALDOS`) |

La página **TI → Servidor** ya detecta los puntos de montaje en Linux. Mostrará cada disco por separado con lo que CALLFLOW guarda en él.

---

## 3. Respaldos (lo más importante)

En ese servidor van a estar los datos personales de todo el personal. Si se pierden, no hay forma de recuperarlos.

### Qué se respalda

1. **Base de datos**: `pg_dump -Fc` → `callflow_AAAAMMDD_HHMMSS.dump`. Es el mismo formato que hoy, así que la página TI → Servidor lo sigue reconociendo.
2. **Archivos subidos** (`/datos/media`): copia incremental con `rsync`, para que no se dupliquen GB cada día.
3. **El archivo `.env`**, guardado aparte y en lugar seguro. Contiene `CLAVE_TARJETAS`, y **sin esa clave los números de tarjeta cifrados quedan ilegibles para siempre**, aunque se tenga la base.

### Regla 3-2-1

| Copia | Dónde | Protege contra |
|---|---|---|
| 1 | SSD principal (datos en uso) | — |
| 2 | **SSD 2** de respaldos, diario y automático | Falla del disco principal o borrado accidental |
| 3 | **Disco externo USB** que se lleva fuera de la oficina cada semana, o nube cifrada | Robo, incendio, inundación, ransomware, falla de toda la máquina |

> **Ojo:** el segundo SSD queda **dentro de la misma máquina**. Protege si falla el disco principal, pero no si roban el equipo, si hay un incendio o si un virus cifra todo. Por eso hace falta la copia 3.

### Retención propuesta

- Diarios: últimos 30 días (igual que hoy en `respaldar_bd.ps1`).
- Mensuales: el del día 1 de cada mes, durante 12 meses.

### Probar la restauración

Un respaldo que nunca se probó no es un respaldo. Una vez al mes, TI restaura el último respaldo en una base de prueba (`callflow_prueba`) y verifica que abra. El script `restaurar_bd.sh` debe permitirlo sin tocar la base real.

---

## 4. Seguridad

Son datos sensibles (cédulas, salarios, CV), así que aplica la **Ley Orgánica de Protección de Datos Personales (LOPDP)** de Ecuador. Mínimos:

- [ ] **HTTPS obligatorio**, también en la red local. Ver sección 5.
- [ ] **Firewall (`ufw`)**: abrir solo 443 (y 80 para redirigir), además de 22 (SSH) **solo desde las IP de TI**. PostgreSQL (5432) **no** se expone a la red.
- [ ] **SSH solo con llave**: sin contraseña y sin login de `root`.
- [ ] **Cifrado del disco (LUKS)** al instalar Ubuntu. Si roban el servidor, no pueden leer los datos. Contra: al reiniciar hay que escribir la clave en el servidor (o configurar desbloqueo por TPM).
- [ ] **Cifrar los respaldos** del disco externo (por ejemplo con `gpg` o un disco con LUKS).
- [ ] **Actualizaciones automáticas de seguridad** con `unattended-upgrades`.
- [ ] `DJANGO_DEBUG=False`, y una `DJANGO_SECRET_KEY` nueva y distinta de la de desarrollo.
- [ ] **Usuario de sistema propio** (`callflow`), sin permisos de administrador, que es el que ejecuta Gunicorn.
- [ ] El `.env` con permisos `600` (solo lo lee `callflow`).
- [ ] **UPS** con apagado ordenado si se corta la luz mucho tiempo.

---

## 5. HTTPS en la red de la oficina

Hace falta HTTPS de verdad, no solo por seguridad: **las notificaciones push del navegador solo funcionan con HTTPS** y con un certificado en el que el navegador confíe. Hay dos opciones:

| Opción | Cómo | Ventaja | Desventaja |
|---|---|---|---|
| **A. Dominio real + Let's Encrypt (recomendada)** | Usar un subdominio de Multivisa (por ejemplo `callflow.multivisa.com.ec`). El certificado se obtiene con validación DNS-01, sin abrir nada a internet. En la red local, ese nombre apunta a la IP interna del servidor (DNS del router o del servidor DNS de la oficina). | Ningún navegador muestra advertencias. No hay que instalar nada en las 100 PCs. Se renueva solo. | Necesita acceso al DNS del dominio de la empresa. |
| B. Autoridad certificadora propia | Crear una CA interna (por ejemplo con `mkcert` o `step-ca`) e instalar su certificado raíz en cada PC. | No depende de un dominio. | Hay que instalar el certificado en las ~100 PCs y en cada PC nueva. Si falta en alguna, ahí ve advertencias y no recibe push. |

**Pendiente:** confirmar si Multivisa tiene dominio propio y quién administra su DNS.

Cómo averiguarlo:
- Si los correos de la empresa son `@multivisa...` o tienen página web, **ya tienen dominio**. Lo maneja quien lo contrató: el proveedor de la página web o del correo, NIC.ec si termina en `.ec`, GoDaddy, Hostinger, Cloudflare, etc. Pregúntenle a quien administra el correo o la web, o busquen el dominio en `whois` (por ejemplo `https://www.nic.ec`).
- Usar un dominio **no expone el CRM a internet**. Solo se usa para obtener el certificado. El nombre apunta a la IP interna (`192.168.x.x`), que desde fuera no lleva a ningún lado.
- Si no hay dominio o no consiguen el acceso, se usa la opción B (CA propia). Funciona igual, solo cuesta más instalarla en cada PC.

---

## 5a. Red de la oficina: ~93 PCs por Wi-Fi

Las PCs de los usuarios **no instalan nada**: solo usan el navegador y entran a la dirección del servidor. Todo lo que hacen (datos, archivos, quién está conectado) queda en el servidor.

- **El servidor va siempre por cable** al router o switch, con **IP fija**. Hoy la red es `192.168.100.0/24` y el router es `192.168.100.1`. Lo más ordenado es reservar la IP del servidor por MAC, o ponerla fija **fuera del rango que reparte el DHCP**.
- **El Wi-Fi del router actual (Huawei EG8145 del proveedor) no da para 93 equipos.** Estos equipos rinden bien con unos 20–30 clientes a la vez. Con 93 PCs más los celulares va a haber cortes y lentitud. Hace falta:
  - **3 o 4 puntos de acceso (AP) empresariales** (por ejemplo TP-Link Omada, Ubiquiti UniFi o Aruba Instant On), cada uno para 25–30 PCs, conectados **por cable** a un **switch** y en modo puente (*AP mode*). Así siguen en la misma red `192.168.100.x`, con un solo DHCP en el router.
  - El mismo nombre de red (SSID) en todos los AP, para que las PCs pasen de uno a otro solas. Usar **5 GHz** para las PCs.
  - Una red Wi-Fi **aparte para invitados y celulares personales**, aislada, que no llegue al servidor.
- **Direcciones:** una red `/24` tiene 253 direcciones. Alcanzan para 93 PCs más los celulares, pero conviene revisar el rango del DHCP del router para que no se acabe. Si se acaba, ampliarlo o pasar a `/23`.
- **TI → Conectados** identifica cada PC por su IP. Si se quiere saber siempre qué PC es cuál, reservar también la IP de cada PC en el DHCP (por MAC).
- **Capacidad:** ver la sección 8. Con 100 personas el servidor sobra. El punto débil es la red Wi-Fi, no el servidor.
- **Seguridad:** cambiar la contraseña de fábrica del router (hoy es la de la etiqueta) y la clave del Wi-Fi. El firewall del servidor solo acepta la red de la oficina (sección 5b).

---

## 5b. Acceso solo por red y sucursales

### Solo desde la red de la oficina

- El servidor **no se publica a internet**: no se abre ningún puerto en el router hacia él.
- El firewall del servidor (`ufw`) solo acepta conexiones desde la red de la oficina (hoy `192.168.100.0/24`) y desde las redes de las sucursales que se conecten por VPN.
- Quien no esté en esas redes no puede ni ver la página de inicio de sesión.

### Sucursales: opción recomendada, un servidor central y VPN entre sedes

```
 Sucursal A (PCs) ─┐
                   ├─ túnel VPN cifrado (WireGuard) por internet ─► Servidor central (oficina matriz)
 Sucursal B (PCs) ─┘                                              ├─ PostgreSQL (TODAS las sucursales)
                                                                  └─ /datos/media (todos los archivos)
```

- Cada sucursal tiene un router o mini-PC con **WireGuard** que crea un túnel cifrado **solo hacia el servidor central**. Para las PCs de la sucursal, el servidor está "en su red".
- **Una sola base y un solo lugar de archivos**: toda la información de todas las sucursales queda junta, sin sincronizar nada y sin duplicados ni conflictos.
- Nadie fuera de las sedes puede entrar. El túnel solo acepta los equipos con su llave.
- Contra: si se cae el internet de una sucursal, esa sucursal no puede usar el CRM hasta que vuelva. Se mitiga con un segundo proveedor de internet de respaldo (por ejemplo, un plan móvil 4G/5G en el router).

### Alternativa: un servidor en cada sucursal que se sincroniza con el central (no recomendada por ahora)

- Cada sucursal trabaja con su propio servidor aunque se caiga internet, y envía sus datos al central (replicación lógica de PostgreSQL).
- Es **mucho más compleja**. CALLFLOW no está hecho para que varias bases escriban a la vez: habría que resolver choques de números (IDs), triggers de inmutabilidad, cuentas duplicadas y archivos. Además, el central solo serviría para consultar, no para editar.
- Solo tiene sentido si alguna sucursal tiene internet muy malo.

### Lo que hay que agregar a CALLFLOW para tener sucursales (en cualquier opción)

Hoy el sistema **no conoce sucursales**: todo es de una sola sede. Habría que:
- [ ] Crear el catálogo **Sucursal** y asignar cada empleado, usuario, base comercial y venta a una sucursal.
- [ ] Definir quién ve qué: el administrador de cada sucursal solo ve la suya, y gerencia o TI ven todas.
- [ ] Filtros y reportes por sucursal (nómina, ventas, asistencia, cierres).
- [ ] TI → Conectados mostrando la sucursal según la red de la IP.

Conviene hacerlo **antes** de abrir la primera sucursal, para que los datos nazcan marcados con su sede.

---

## 6. Cambios a preparar en el proyecto

Nada de esto está hecho todavía. Es lo que hay que construir antes de instalar.

### 6.1 Ajustes de `callflow/settings.py`

- [ ] `CONN_MAX_AGE` (por ejemplo 60 s) y `CONN_HEALTH_CHECKS = True`, para no abrir una conexión nueva a PostgreSQL en cada petición.
- [ ] `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, porque Nginx entrega las peticiones a Gunicorn por HTTP interno.
- [ ] `SECURE_SSL_REDIRECT` y `SECURE_HSTS_SECONDS` (desde `.env`).
- [x] `CSRF_TRUSTED_ORIGINS` desde `DJANGO_CSRF_TRUSTED_ORIGINS` y `connect_timeout` desde `DB_CONNECT_TIMEOUT` (commit `5b754b8`).
- [ ] Revisar la cantidad de conexiones: con `max_connections = 100` en PostgreSQL y conexiones persistentes, el total de procesos de Gunicorn por hilos debe quedar muy por debajo de 100.
- [ ] **Registro de errores con varios procesos**: `RotatingFileHandler` no es seguro cuando varios procesos de Gunicorn escriben en el mismo archivo, porque al rotar pueden perderse o mezclarse líneas. Opciones: `WatchedFileHandler` con rotación por `logrotate` (recomendada en Linux), o un archivo por proceso. `registro_errores.py` ya lee `errores.jsonl*`, así que sigue funcionando.

### 6.2 Carga con 100 personas

- [ ] La campana de notificaciones consulta al servidor **cada 5 segundos** (`templates/base.html`, `every 5s`). Con 100 personas son unas 20 peticiones por segundo solo por eso. Propuesta: subirlo a 20–30 segundos, porque las push ya avisan al instante.
- [ ] Gunicorn: 4–5 procesos (`workers`) con 2–4 hilos cada uno (`gthread`), y luego ajustar mirando TI → Servidor.

### 6.3 Archivos nuevos (carpeta `despliegue/`)

- [ ] `requirements-servidor.txt`: agregar `gunicorn`.
- [ ] `despliegue/gunicorn.conf.py`
- [ ] `despliegue/callflow.service` (systemd)
- [ ] `despliegue/nginx-callflow.conf`: límite de subida de 10 MB (`client_max_body_size`), `/static/` directo y cabeceras de seguridad.
- [ ] `despliegue/logrotate-callflow`
- [ ] `despliegue/crontab`: tareas programadas (sección 6.4).
- [ ] `despliegue/.env.produccion.ejemplo`
- [ ] Versiones Bash de los scripts de Windows:
  - `scripts/crear_bd.sh` ← `crear_bd.ps1` + `crear_bd.sql` (el SQL sirve tal cual)
  - `scripts/respaldar.sh` ← `respaldar_bd.ps1`, más el `rsync` de media y la retención mensual
  - `scripts/restaurar_bd.sh` ← `restaurar_bd.ps1`, con opción de restaurar en base de prueba
- [ ] `despliegue/INSTALACION.md`: guía paso a paso para instalar desde cero.

### 6.4 Tareas programadas (cron)

Hoy están documentadas para el Programador de tareas de Windows. En Linux:

| Comando | Frecuencia | Qué hace |
|---|---|---|
| `enviar_push` | cada minuto | Envía las notificaciones pendientes como push del navegador |
| `enviar_avisos_agenda` | cada minuto | Crea los avisos de eventos de agenda |
| `revisar_salidas` | cada 15 minutos | Cierra jornadas sin hora de salida y avisa a los administradores |
| `buscar_repetidos` | cada noche | Pasa contactos repetidos a la base de repetidos |
| `scripts/respaldar.sh` | cada noche (por ejemplo 23:30) | Respaldo de la base y de los archivos al SSD 2 |

### 6.5 Variables del `.env` de producción

| Variable | Valor en el servidor |
|---|---|
| `DJANGO_SECRET_KEY` | Nueva y generada para el servidor |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_ALLOWED_HOSTS` | `callflow.<dominio>,<IP del servidor>` |
| `DB_*` | Usuario `callflow_app` y contraseña nueva |
| `CLAVE_TARJETAS` | **La MISMA de la PC actual.** Si cambia, no se pueden leer las tarjetas ya guardadas. |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | Las mismas (así no hay que volver a suscribir los navegadores) o nuevas |
| `VAPID_CONTACTO` | Correo de TI |
| `MEDIA_ROOT` | `/datos/media` |
| `LOGS_DIR` | `/datos/logs` |
| `CARPETA_RESPALDOS` | `/respaldos/callflow` |
| `TI_PROXY_CONFIABLE` | `True`, porque está detrás de Nginx. Así TI → Conectados ve la IP real de cada PC. |

Como el servidor está **en la red local**, TI → Conectados va a distinguir cada PC por su IP interna, siempre que Nginx envíe `X-Forwarded-For` y `TI_PROXY_CONFIABLE=True`.

---

## 6.6 Etapa actual (antes del servidor): la PC de Nicolás hace de servidor

Desde el 13/11/2026, mientras no hay servidor, la **PC de Nicolás** cumple el mismo papel que va a cumplir el servidor. Así la mudanza es solo copiar dos cosas (la base y los archivos) y cambiar una dirección.

```
 Usuarios (cualquier rol)              Desarrolladores
   navegador ─┐                          runserver local ─┐  (solo para programar y probar)
              ▼                                           ▼
 ┌──────── PC de Nicolás  192.168.100.83 (cable) ─────────────────┐
 │  CALLFLOW  runserver 0.0.0.0:8000 → http://192.168.100.83:8000 │
 │  PostgreSQL 18 · base callflow            ← la ÚNICA base      │
 │  carpeta media\ (CV, contratos, fotos, adjuntos, actas)        │
 │                                           ← la ÚNICA carpeta   │
 │  Tareas programadas (push, agenda, salidas, repetidos, respaldo)│
 └─────────────────────────────────────────────────────────────────┘
```

**Regla de oro: los datos y los archivos viven en un solo lugar.**
- La base guarda solo la **ruta relativa** de cada archivo (por ejemplo `documentos/…/cv.pdf`). El archivo en sí queda en la carpeta `MEDIA_ROOT` **de la PC donde corre la app que lo recibió**.
- Los archivos siempre se entregan por vistas de CALLFLOW que revisan el permiso del rol (CV, contratos, adjuntos de mensajes, actas, fotos). Por eso, para que un rol vea lo que subió otro, los dos tienen que estar usando la **misma carpeta**.
- **Los usuarios reales usan siempre `http://192.168.100.83:8000`**, nunca la app de otra PC. Así todo lo que suben queda en la PC de Nicolás, igual que va a quedar en el servidor.
- Si un desarrollador corre CALLFLOW en su propia PC contra la base compartida, su `MEDIA_ROOT` tiene que apuntar a la carpeta compartida de la PC de Nicolás (ver más abajo). Si no, lo que suba queda solo en su PC y los demás ven "archivo no encontrado".

| Dato | Valor |
|---|---|
| PC que hace de servidor | `192.168.100.83` (cable), PostgreSQL **18**, base `callflow`, app en el puerto 8000 |
| PC de desarrollo | `192.168.100.82` (cable) |
| Router | Huawei EG8145, `192.168.100.1`. Las IPs por cable se reservan por MAC en *LAN → DHCP Static IP*. |
| Acceso a la base por red | `pg_hba.conf`: `host callflow callflow_app 192.168.100.0/24 scram-sha-256`. El superusuario solo entra desde esa misma PC. |
| Firewall | Entrantes 5432 (PostgreSQL), 8000 (Django) y 445 (carpeta compartida), solo desde `192.168.100.0/24` |
| Carpeta de archivos compartida | `\\192.168.100.83\callflow-media` → carpeta `media\` del proyecto en la PC de Nicolás |

### Qué tiene que ser IGUAL en todas las PCs que corren CALLFLOW

| Variable | Por qué |
|---|---|
| `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Una sola base |
| `CLAVE_TARJETAS` | Si no coincide, no se pueden leer las tarjetas del DAMI guardadas desde otra PC |
| `MEDIA_ROOT` | Una sola carpeta de archivos (en la PC de Nicolás es la local; en las demás, `\\192.168.100.83\callflow-media`) |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` | Las suscripciones push se guardan en la base atadas a esta clave. Con claves distintas, los avisos al navegador fallan |
| `DJANGO_SECRET_KEY` | Firma las sesiones (guardadas en la base) y los enlaces de restablecer contraseña |

`.env` de una PC de desarrollo (las claves se entregan aparte, nunca en el repo):

```
DB_HOST=192.168.100.83
DB_USER=callflow_app
DB_PASSWORD=<la de callflow_app en la PC servidor>
DB_CONNECT_TIMEOUT=5
CLAVE_TARJETAS=<idéntica>
VAPID_PUBLIC_KEY=<idéntica>
VAPID_PRIVATE_KEY=<idéntica>
DJANGO_SECRET_KEY=<idéntica>
MEDIA_ROOT=\\192.168.100.83\callflow-media
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,<IP de esta PC>
DJANGO_CSRF_TRUSTED_ORIGINS=http://<IP de esta PC>:8000
```

Para que la PC de desarrollo pueda escribir en la carpeta compartida sin pedir usuario, se guarda una sola vez la credencial de Windows de la PC servidor:
`cmdkey /add:192.168.100.83 /user:<usuario de Windows de esa PC> /pass:<su contraseña>`

### Reglas de esta etapa
- Hay **una sola base y una sola carpeta de archivos**. Nadie corre `migrate` sin avisar, y antes se hace un respaldo.
- `migrate` siempre se corre con `callflow_app`.
- Las **tareas programadas** (sección 6.4) y los **respaldos** corren **solo en la PC servidor**. En las PCs de desarrollo no, para que no se dupliquen avisos.
- Respaldo: `scripts\respaldar_bd.ps1` en la PC servidor. Genera `callflow_<fecha>.dump` (la base) y `callflow_media_<fecha>.zip` (los archivos) con la misma fecha y hora. Elige sola la versión de PostgreSQL más nueva instalada; desde una PC con una versión más vieja que la de la base no se puede respaldar.
- Las pruebas se corren contra el PostgreSQL **local** de cada PC (ver la sección 10), porque el superusuario no entra por red.
- La PC servidor tiene que estar prendida y con cable. Si alguien no se puede conectar, primero hay que revisar que no le haya cambiado la IP.

---

## 7. Mudanza de los datos actuales

Orden propuesto para el día del cambio:

1. Avisar a todos y **detener el uso** de CALLFLOW en la PC actual (hoy, la PC de la base compartida, `192.168.100.83`; ver la sección 6.6).
2. En la PC actual: `scripts\respaldar_bd.ps1`. Deja el `.dump` final de la base y el `.zip` de los archivos (`MEDIA_ROOT`), con la misma fecha y hora.
3. Copiar al servidor (USB o red): el `.dump`, el `.zip` de archivos (se descomprime en `/datos/media`, respetando las subcarpetas, porque la base guarda las rutas relativas) y los valores de `CLAVE_TARJETAS`, `VAPID_*` y `DJANGO_SECRET_KEY` del `.env`. Con la misma `SECRET_KEY` nadie pierde la sesión; si se cambia, todos inician sesión de nuevo una vez.
4. En el servidor (con PostgreSQL **18 o superior**, porque un dump hecho en la 18 no se restaura en una versión anterior): `crear_bd.sh` (roles y base), luego `pg_restore` del dump.
5. `python manage.py migrate` (con `callflow_app`), `verificar_bd` y `collectstatic`.
6. Comprobar con TI: iniciar sesión, abrir un CV, descargar un rol de pago, generar un acta PDF y revisar TI → Servidor.
7. Recién entonces, dar la dirección nueva a todos.
8. Guardar la PC vieja intacta **al menos un mes**, como respaldo adicional.

---

## 8. Capacidad estimada

| Recurso | Uso esperado con 100 personas | Disponible |
|---|---|---|
| CPU | Gunicorn 4–5 procesos + PostgreSQL | i7 de última generación: sobra |
| RAM | 3–5 GB (Gunicorn ~1–1,5 GB, PostgreSQL 1–2 GB, sistema) | 16 GB: sobra. Configurar `shared_buffers ≈ 4 GB` en PostgreSQL. |
| Disco | Base hoy 15 MB, archivos 2,5 MB. Un CV o rol de pago pesa 0,1–1 MB, así que 100 empleados × 12 roles/año × 1 MB ≈ 1,2 GB por año. | 2 TB: años de margen |
| Conexiones a la base | ~10–20 con conexiones persistentes | `max_connections = 100` |

Recomendación: que el almacenamiento sea **SSD NVMe**, al menos donde va PostgreSQL.

---

## 9. Pendientes abiertos

### Decisiones

- [ ] ¿Multivisa tiene **dominio propio**? ¿Quién maneja su DNS? (Define la opción A o B de HTTPS.)
- [ ] ¿Cuántas sucursales se esperan y cuándo? ¿Qué internet tienen? (Define VPN central o servidor por sucursal.)
- [x] Rango de red de la oficina: `192.168.100.x` (router `192.168.100.1`). Cada sucursal debe usar uno distinto.
- [ ] ¿Quién tiene acceso físico al servidor y por SSH? (Solo TI: santiago.cedeno y nicolas.correa.)
- [ ] ¿Dónde se guarda la **copia 3** de los respaldos, fuera de la oficina?
- [ ] ¿Se cifra el disco con LUKS? (Implica escribir la clave en cada reinicio.)
- [ ] ¿Hay **UPS** para el servidor?
- [ ] Fecha tentativa de la mudanza.

### En el proyecto (independiente del servidor)

- [ ] Subir a `feature/ventas` los cambios sin confirmar: página **TI → Servidor** y arreglo del **encabezado del supervisor**.
- [ ] Registrar a **Santiago Cedeño** y **Nicolás Correa** como empleados en Personal (cargo "Tecnología (TI)", vinculando su cuenta existente) para que diego.mendoza los vea.
- [ ] Que RR. HH. o el área legal revise las cláusulas del **acta de entrega/devolución de equipos** (`apps/ti/actas_pdf.py`).
- [ ] La PC actual tiene el **disco C: al 8,6 % libre** y el último respaldo es del 05/11. Conviene respaldar ya y liberar espacio mientras se hace la mudanza.

---

## 10. Datos útiles del proyecto

- Django 5.2, PostgreSQL 18 (en la PC de la base compartida), Python 3.12+, HTMX, Bootstrap, fpdf2.
- Las migraciones se corren con el usuario de la aplicación (`callflow_app`): `python manage.py migrate`.
- Las pruebas se corren con el usuario administrador de la base:
  `DB_HOST=localhost DB_USER=$DB_ADMIN_USER DB_PASSWORD=$DB_ADMIN_PASSWORD python manage.py test apps --noinput`
  (`DB_HOST=localhost` hace falta en las PCs que apuntan a la base compartida.)
- La base tiene triggers de inmutabilidad (actas, hoja de vida, cuentas TI) y de auditoría. `pg_restore` debe restaurarlos tal cual: no usar `--disable-triggers` en producción.
- Repositorio: GitHub `Thyago23/CALLFLOW`, rama de trabajo `feature/ventas`.
