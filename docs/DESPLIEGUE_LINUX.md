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
 PCs de la oficina (≈100)
        │  https://callflow.<dominio>  (red local)
        ▼
 ┌──────────────── Servidor Ubuntu ────────────────┐
 │  Nginx  (puerto 443, HTTPS)                     │
 │    ├─ /static/  → archivos de diseño (directo)  │
 │    └─ resto     → Gunicorn (127.0.0.1:8000)     │
 │                    └─ Django / CALLFLOW         │
 │                         ├─ PostgreSQL 17 (local)│
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

## 5b. Acceso solo por red y sucursales

### Solo desde la red de la oficina

- El servidor **no se publica a internet**: no se abre ningún puerto en el router hacia él.
- El firewall del servidor (`ufw`) solo acepta conexiones desde la red de la oficina (por ejemplo `192.168.1.0/24`) y desde las redes de las sucursales que se conecten por VPN.
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
- [ ] `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS` y `CSRF_TRUSTED_ORIGINS` (desde `.env`).
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

## 7. Mudanza de los datos actuales

Orden propuesto para el día del cambio:

1. Avisar a todos y **detener el uso** de CALLFLOW en la PC actual.
2. En la PC actual: `scripts\respaldar_bd.ps1` para obtener el `.dump` final.
3. Copiar al servidor (USB o red): el `.dump`, la carpeta `media/` completa y los valores de `CLAVE_TARJETAS` y `VAPID_*` del `.env`.
4. En el servidor: `crear_bd.sh` (roles y base), luego `pg_restore` del dump.
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
- [ ] ¿Qué rango de red usa la oficina (por ejemplo `192.168.1.x`)? Cada sucursal debe usar uno distinto.
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

- Django 5.2, PostgreSQL 17.4, Python 3.12+, HTMX, Bootstrap, fpdf2.
- Las migraciones se corren con el usuario de la aplicación (`callflow_app`): `python manage.py migrate`.
- Las pruebas se corren con el usuario administrador de la base:
  `DB_USER=$DB_ADMIN_USER DB_PASSWORD=$DB_ADMIN_PASSWORD python manage.py test apps --noinput`
- La base tiene triggers de inmutabilidad (actas, hoja de vida, cuentas TI) y de auditoría. `pg_restore` debe restaurarlos tal cual: no usar `--disable-triggers` en producción.
- Repositorio: GitHub `Thyago23/CALLFLOW`, rama de trabajo `feature/ventas`.
