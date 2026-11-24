"""
Gunicorn para CALLFLOW en el servidor Linux (lo arranca despliegue/callflow.service).

Cuentas para ~100 personas: 4 procesos × 4 hilos = 16 peticiones a la vez. Con DB_CONN_MAX_AGE=60 cada hilo
mantiene su conexión: unas 16 conexiones a PostgreSQL más las del cron, muy por debajo del límite de 50 de
callflow_app. Ajustar mirando TI › Servidor (CPU y memoria) antes de subir los números.
"""
import multiprocessing

bind = "127.0.0.1:8000"          # solo Nginx entra; el puerto no se abre a la red
workers = min(4, multiprocessing.cpu_count())
worker_class = "gthread"
threads = 4
timeout = 120                     # los PDF y Excel grandes (nómina, reportes) pueden tardar
graceful_timeout = 30
keepalive = 5
# Reinicia cada proceso después de N peticiones: corta cualquier fuga de memoria sin que nadie lo note.
max_requests = 2000
max_requests_jitter = 200
# Los errores de la app ya van a LOGS_DIR/errores.jsonl (TI › Servidor). Esto es lo del propio Gunicorn.
accesslog = None
errorlog = "-"                    # al journal de systemd: journalctl -u callflow
loglevel = "warning"
forwarded_allow_ips = "127.0.0.1"
