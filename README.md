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
- **Nómina**: el neto siempre cuadra (`ingresos − deducciones`); un periodo cerrado queda congelado; tras aprobar un periodo no se cambian sus novedades; la solicitud de pago exige factura (PDF/imagen) del mismo empleado y no puede superar el neto.
- **Documentos**: solo PDF, JPG y PNG de hasta 10 MB; sin duplicados por huella SHA-256; borrado lógico.
- **Auditoría** (sección 6): los cambios en empleados, salarios, comisiones, nómina, producción, cierres, metas y usuarios quedan en `core_registrocambio` (sin contraseñas). La bitácora no se puede modificar ni borrar.

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

## Respaldos (sección 6)

- Respaldo: `powershell -ExecutionPolicy Bypass -File scripts\respaldar_bd.ps1` (guarda en `respaldos\`, conserva los últimos 30 y verifica que cada archivo se pueda leer). Conviene programarlo a diario en el Programador de tareas de Windows.
- Restauración de prueba en una base aparte: `scripts\restaurar_bd.ps1 -Archivo respaldos\<archivo>.dump`
  Crea `callflow_restaurada` sin tocar la base en uso. Para comprobarla: `$env:DB_NAME='callflow_restaurada'; python manage.py verificar_bd`.
  Probado el 2026-11-05: la base restaurada quedó idéntica a la original (52 tablas, 253 restricciones, 29 triggers, mismas filas) y pasó las 31 verificaciones.

## Pendientes de la propuesta (sección 10)

Los campos definitivos del DAMI van por ahora en `formulariodami.datos_adicionales` (JSONB); las fórmulas de nómina se registran en `nomina_reglacalculo.parametros`. Cuando la empresa los defina, se agregan como columnas con una migración.
