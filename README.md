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

## Bases comerciales (SCRUM-93 a 97)

- Encargados de cierre y administrador cargan la base desde Excel (.xlsx, .xls) o .csv en **Ventas → Bases comerciales**. `apps/ventas/bases_excel.py` la deja en el formato de la empresa: **Ciudad · Categoría tarjeta · Dirección · Nombre y apellido · Cédula · Número**. Las bases suelen llegar sin encabezados: cada columna se reconoce por su contenido (cédulas válidas, teléfonos, tipos de tarjeta, ciudades del Ecuador, direcciones, nombres) y, si trae encabezados, por sinónimos (Teléfono, Celular, CI, Cliente…). Une Nombres + Apellidos, recupera el 0 que Excel quita a cédulas y teléfonos, convierte 593… a 0… y omite filas vacías y sin teléfono. Se puede descargar la base formateada y una plantilla.
- **Contactos repetidos** (`ventas_contactorepetido`, «base de repetidos»): un contacto es repetido si su cédula válida o alguno de sus teléfonos (también los «otros números») ya está en el mismo archivo o en otra base. No entra a la base, así que nunca se envía a un vendedor; queda en **Bases comerciales → Repetidos** con el contacto con el que coincide (base, fila, vendedor y estado). Al cargar se avisa en pantalla y con una notificación (campana y push) a administradores y encargados de cierre. Cada repetido se *descarta* o, si no es la misma persona, se *recupera* a su base como contacto sin enviar; se puede descargar en Excel.
  Para bases cargadas antes, el botón **Buscar repetidos en todas las bases** (o `python manage.py buscar_repetidos`, programable) deja en su base el contacto cargado primero y mueve los repetidos posteriores que aún no tienen vendedor; los ya enviados no se tocan y solo se cuentan.
- Al asignar una base, sus contactos libres se reparten entre los vendedores elegidos (en partes iguales o una cantidad por vendedor); atender una solicitud de base también envía contactos.
- El vendedor categoriza cada cliente en **Mis contactos**: No contestó, No quiere o DAMI sacado (se puede corregir). «Llenar DAMI» prellena el formulario con los datos del contacto.
- **Seguimiento por vendedor**: enviados, gestionados, faltantes y cada categoría. Reportes: *Efectividad de bases* (DAMI sacados ÷ gestionados, con la media de todas las bases) y *Gestión de bases por vendedor*.

## Mis estadísticas (vendedor)

- **Ventas → Mis estadísticas** (`/ventas/mis-estadisticas/`), solo para vendedores y con sus propios datos: DAMI sacados (enviados a cierre, sin anulados), ventas cerradas y monto vendido de los últimos 6 o 12 meses, con la misma regla de fechas que la hoja de producción (`reportes/estadisticas.calcular` filtrado por el vendedor).
- El mes en curso se compara con lo que llevaba el mes anterior **hasta el mismo día** (no contra un mes completo), además del mes anterior completo, su mejor mes y su promedio de los últimos 3 meses.
- Gráficas SVG del servidor (`graficas.barras_mensuales`): DAMI frente a ventas (azul `#2a78d6` / naranja `#eb6834`, validados para daltonismo y contraste) y monto por mes con la línea del promedio. Cada mes tiene tooltip y la tabla «Mes a mes» repite los datos.

## Tablero de ventas (TV del call)

- Menú **Comercial → Tablero de ventas** (`/tablero/`), para administrador y encargados de cierre. Reemplaza al tablero escrito a mano: todos los vendedores con lo vendido en el mes, del primero al último, el total del call y el avance de la meta si la tiene. Los montos salen de la misma vista que el panel comercial y la hoja de producción.
- Pantalla pensada para proyectar por HDMI (1920×1080): sin menú ni avisos flotantes, letra grande y se actualiza sola cada 30 s. Cuando alguien vende, su fila se ilumina con «+$…» y cae confeti; si cambia el primero, el confeti es mayor. Arriba, un podio con los tres primeros que ya vendieron (oro, plata y bronce, con trofeo); debajo, el resto en filas con ventas, % de la meta, en cierre y lo vendido. Con más de 5 en la lista pasa a dos columnas (y a tres con más de 12).
- Al mover el mouse aparecen los controles (se esconden solos a los 3 s): mes, **Destacar** (👑 mejor vendedor, 💎 venta más alta del mes o nada) y pantalla completa. La opción elegida queda en la dirección (`?destacar=mejor|venta|nada`), así que se puede dejar como favorito en el navegador de la TV.

## Notificaciones

- **En el sistema:** la campana abre un panel con las últimas; «Ver todas» lleva a `/notificaciones/`. Cada 20 s se consultan las nuevas y aparecen como avisos flotantes abajo a la derecha.
- **Push del navegador (Web Push):** cada persona las activa en su navegador desde el panel de la campana; llegan como avisos del sistema operativo aunque CALLFLOW esté cerrado (con el navegador abierto). Requieren HTTPS o `localhost` y claves VAPID en el `.env`:
  `python manage.py generar_claves_push --guardar` (una vez por instalación; si se cambian, hay que reactivarlas).
- Toda notificación nace con `push_enviada = false`, también las que crean los triggers (stock bajo). Se envían solas al terminar cualquier petición; para los ratos sin uso, programar cada minuto `python manage.py enviar_push` (el comando `enviar_avisos_agenda` también las envía). Las de más de 15 minutos ya no se envían como push.
- Al cerrar sesión, el navegador deja de recibir los avisos de esa persona.

## Finalizar día (control de salida)

- Vendedores y encargados de cierre marcan la entrada al iniciar sesión por primera vez en el día. Mientras su jornada está abierta, la barra superior muestra **«Finalizar día»**: registra la salida con la hora del servidor y cierra la sesión (si es antes de la hora de salida, queda como salida anticipada). Cerrar sesión sin finalizar pide confirmación y no registra la salida.
- Si pasada la hora de salida del horario + `ASISTENCIA_MARGEN_SALIDA_MINUTOS` (60 por defecto) la jornada sigue sin salida, se crea la novedad **«Sin marcación de salida»** (pendiente de revisión, en Asistencia → Novedades) y se notifica a los administradores. Una incidencia y un aviso por persona y día; si el administrador corrige la salida con un registro manual, la incidencia pendiente desaparece.
- La revisión corre sola cada 5 minutos mientras alguien tenga CALLFLOW abierto; para que no dependa de eso, programar `python manage.py revisar_salidas` (por ejemplo cada 15 minutos). Solo revisa los últimos `ASISTENCIA_DIAS_REVISION_SALIDA` días (7).

## Almuerzo

- Con la jornada abierta, la barra superior muestra **«Salir a almorzar»** (una vez por día): marca la hora y cierra la sesión. La pantalla de acceso muestra un contador (con la hora del servidor) de lo que falta para volver y, si se pasa, cuántos minutos lleva de más, actualizado minuto a minuto.
- El siguiente inicio de sesión registra el regreso. Si vuelve más de `ASISTENCIA_ALMUERZO_TOLERANCIA_MINUTOS` (5) después de la hora de regreso (`ASISTENCIA_ALMUERZO_MINUTOS`, 60 por defecto), se crea la novedad **«Exceso en almuerzo»** con todos los minutos de más y se avisa a los administradores. Volver antes de hora no genera nada.
- Las novedades de almuerzo y de «sin salida» no descuentan en nómina; el administrador las revisa y clasifica en Asistencia → Novedades.

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
