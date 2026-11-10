# CALLFLOW

Sistema interno del centro de llamadas, según la *Propuesta técnica, planificación y presupuesto del sistema CALLFLOW*.
Stack (sección 5): **Python + Django + PostgreSQL**.

## Módulos y tablas

| Sección | App | Tablas principales |
|---|---|---|
| Núcleo / seguridad (6) | `core` | `usuario` (con rol), `registrocambio` (auditoría), `notificacion` |
| 4.1 Gestión del personal | `personal` | `cargo`, `empleado`, `historialsalario`, `esquemacomision`, `tramocomision`, `asignacioncomision` |
| 4.1 Gestión documental | `documentos` | `tipodocumento`, `documento`, `plantillacontrato`, `contratogenerado` |
| 4.1 Asistencia | `asistencia` | `horario`, `asignacionhorario`, `registroasistencia`, `novedadasistencia` |
| 4.1 Inventario | `inventario` | `categoriamaterial`, `material`, `movimientoinventario` |
| 4.1 Agenda | `agenda` | `evento`, `participanteevento` |
| 4.2 Nómina | `nomina` | `reglacalculo`, `periodonomina`, `tiponovedadnomina`, `novedadnomina`, `liquidacionnomina`, `detalleliquidacion`, `solicitudpago` |
| 4.3 Ventas | `ventas` | `basecomercial`, `asignacionbase`, `solicitudbase`, `cliente`, `formulariodami`, `historialestadoformulario`, `hojaproduccion`, `registroproduccion` |
| 4.4 Cierre de ventas | `cierre` | `metamensual`, `gestioncierre`, `preferenciapanel`, vista `v_resumen_vendedor_mes` |
| 4.5 Reportes | `reportes` | `tiporeporte`, `reportegenerado` |

## Reglas que garantiza la propia base de datos

No dependen de que la aplicación "se acuerde": aplican aunque alguien escriba SQL directo.

- **Vigencias sin solapamiento** (restricciones de exclusión con `btree_gist`): un empleado no puede tener dos salarios, horarios ni esquemas de comisión vigentes a la vez; los periodos de nómina y las versiones de reglas de cálculo no se solapan.
- **Inventario**: el stock solo cambia mediante movimientos; no se permiten salidas mayores al disponible; los movimientos son inmutables (los errores se corrigen con ajustes); al llegar al mínimo se notifica a los administradores.
- **DAMI**: transiciones de estado válidas; con tarjeta activa → prioridad 1 (columna calculada); al enviar se crea sola la fila de la hoja de producción; historial de estados con el usuario; **bloqueo optimista** con `version` (actualizar con `WHERE id = ? AND version = ?`) para que los dos encargados no se sobrescriban. No se guardan números de tarjeta.
- **Cierre**: solo el encargado asignado (y nunca el propio vendedor) registra el resultado; la gestión actualiza el DAMI y la hoja de producción; venta cerrada exige valor; las gestiones no se borran.
  Reparto entre los dos encargados (propuesta a confirmar con la empresa, SCRUM-50): **cola compartida**. Cada encargado *toma*
  el formulario que va a gestionar (primero los de tarjeta activa), puede devolverlo a la cola, y el administrador puede reasignarlo;
  cada movimiento queda en `cierre_movimientoasignacion`. Todas las acciones envían la `version` que el usuario tenía en pantalla,
  así que si el otro encargado guardó antes se avisa en lugar de sobrescribir.
- **Nómina**: el neto siempre cuadra (`ingresos − deducciones`); un periodo cerrado queda congelado; tras aprobar un periodo no se cambian sus novedades; la solicitud de pago exige factura (PDF/imagen) del mismo empleado y no puede superar el neto.
- **Documentos**: solo PDF, JPG y PNG de hasta 10 MB; sin duplicados por huella SHA-256; borrado lógico.
- **Auditoría** (sección 6): los cambios en empleados, salarios, comisiones, nómina, producción, cierres, metas y usuarios quedan en `core_registrocambio` (sin contraseñas). La bitácora no se puede modificar ni borrar.

## Notificaciones

- **En el sistema:** la campana abre un panel con las últimas; «Ver todas» lleva a `/notificaciones/`. Cada 20 s se consultan las nuevas y aparecen como avisos flotantes abajo a la derecha.
- **Push del navegador (Web Push):** cada persona las activa en su navegador desde el panel de la campana; llegan como avisos del sistema operativo aunque CALLFLOW esté cerrado (con el navegador abierto). Requieren HTTPS o `localhost` y claves VAPID en el `.env`:
  `python manage.py generar_claves_push --guardar` (una vez por instalación; si se cambian, hay que reactivarlas).
- Toda notificación nace con `push_enviada = false`, también las que crean los triggers (stock bajo). Se envían solas al terminar cualquier petición; para los ratos sin uso, programar cada minuto `python manage.py enviar_push` (el comando `enviar_avisos_agenda` también las envía). Las de más de 15 minutos ya no se envían como push.
- Al cerrar sesión, el navegador deja de recibir los avisos de esa persona.

## Reportes y estadísticas (4.5, épica SCRUM-61)

- Catálogo en `apps/reportes/catalogo.py`: cada reporte arma una tabla que se ve en pantalla y se exporta a PDF con el mismo contenido. Quién genera cada uno lo dicen los roles de `reportes_tiporeporte`, que carga `inicializar_datos` (administrador: todos; encargado de cierre: los comerciales; el vendedor no entra al módulo).
- Cada descarga queda en `reportes_reportegenerado` (tipo, filtros, usuario, tamaño); el archivo no se guarda.
- Estadísticas (`apps/reportes/estadisticas.py`): las ventas cuentan en el mes en que se envió el DAMI. Proyección del mes en curso = vendido ÷ días laborables transcurridos × días laborables del mes; próximos 3 meses = tendencia lineal sobre los meses completos con actividad, junto al promedio de los últimos 3.
- Las gráficas son SVG generados en el servidor (sin librerías ni internet); en el PDF se dibujan con fpdf2.

## Puesta en marcha

1. Crear la base y los usuarios (pide la contraseña del superusuario `postgres` actual; no la guarda):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\crear_bd.ps1
   ```
   Crea `callflow_app` (usuario de Django, sin privilegios de superusuario) y la base `callflow`. El superusuario de la empresa es el
   indicado en `DB_ADMIN_USER` del `.env` (en esta instalación: `CALLFLOW`); si no está definido, el script crea `callflow_admin`.
   Si la base no aparece en pgAdmin: clic derecho en *Databases* → *Refresh*.
2. Crear las tablas, triggers y vistas: `python manage.py migrate`
3. Cargar catálogos y grupos por rol: `python manage.py inicializar_datos`
4. Verificar todas las reglas (no deja datos): `python manage.py verificar_bd`
5. Crear el usuario administrador del sistema: `python manage.py createsuperuser`

Después de traer cambios con migraciones nuevas, repetir `python manage.py migrate` y `python manage.py inicializar_datos`
(este último asigna a cada rol los permisos de las tablas nuevas).

Pruebas: `python manage.py test`. El usuario `callflow_app` no puede crear la base de pruebas, así que se corren con el
superusuario de la empresa (`DB_USER` y `DB_PASSWORD` de `DB_ADMIN_USER` como variables de entorno solo para ese comando).

## Respaldos (sección 6)

- Respaldo: `powershell -ExecutionPolicy Bypass -File scripts\respaldar_bd.ps1` (guarda en `respaldos\`, conserva los últimos 30 y verifica que cada archivo se pueda leer). Conviene programarlo a diario en el Programador de tareas de Windows.
- Restauración de prueba en una base aparte: `scripts\restaurar_bd.ps1 -Archivo respaldos\<archivo>.dump`
  Crea `callflow_restaurada` sin tocar la base en uso. Para comprobarla: `$env:DB_NAME='callflow_restaurada'; python manage.py verificar_bd`.
  Probado el 2026-11-05: la base restaurada quedó idéntica a la original (52 tablas, 253 restricciones, 29 triggers, mismas filas) y pasó las 31 verificaciones.

## Pendientes de la propuesta (sección 10)

Los campos definitivos del DAMI van por ahora en `formulariodami.datos_adicionales` (JSONB); las fórmulas de nómina se registran en `nomina_reglacalculo.parametros`. Cuando la empresa los defina, se agregan como columnas con una migración.
