/* Funciones compartidas por inicio.html, asesor.html y reportes.html. */
(function () {
  "use strict";
  const C = {};
  C.app = window.Android || null;
  C.$ = (id) => document.getElementById(id);
  C.DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
  C.MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  C.TIPOS = { saliente: "Saliente", entrante: "Entrante", perdida: "Perdida", rechazada: "Rechazada", otra: "Otra" };

  // ------------------------------------------------------------------ fechas y formatos
  const dos = (n) => String(n).padStart(2, "0");
  C.dos = dos;
  C.fechaIso = (d) => d.getFullYear() + "-" + dos(d.getMonth() + 1) + "-" + dos(d.getDate());
  C.horaIso = (d) => dos(d.getHours()) + ":" + dos(d.getMinutes());
  C.deIso = function (f) { const [a, m, d] = f.split("-").map(Number); return new Date(a, m - 1, d); };
  C.fechaCorta = (f) => f.slice(8, 10) + "/" + f.slice(5, 7) + "/" + f.slice(0, 4);
  C.nombreDia = function (fecha) {
    const ayer = new Date(); ayer.setDate(ayer.getDate() - 1);
    const d = C.deIso(fecha), texto = C.DIAS[d.getDay()] + " " + d.getDate() + " de " + C.MESES[d.getMonth()];
    if (fecha === C.fechaIso(new Date())) return "Hoy · " + texto;
    if (fecha === C.fechaIso(ayer)) return "Ayer · " + texto;
    return texto + (d.getFullYear() !== new Date().getFullYear() ? " de " + d.getFullYear() : "");
  };
  C.diaSemana = (f) => { const s = C.DIAS[C.deIso(f).getDay()]; return s.charAt(0).toUpperCase() + s.slice(1); };
  C.periodoTexto = function (desde, hasta) {
    return desde === hasta ? C.fechaCorta(desde) : C.fechaCorta(desde) + " al " + C.fechaCorta(hasta);
  };
  /** 3725 → «1 h 02 min»; 125 → «2 min 05 s»; 40 → «40 s». */
  C.duracion = function (seg) {
    seg = Math.round(seg || 0);
    const h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60), s = seg % 60;
    if (h) return h + " h " + dos(m) + " min";
    if (m) return m + " min " + dos(s) + " s";
    return s + " s";
  };
  /** 125 → «2:05»; 3725 → «1:02:05». */
  C.reloj = function (seg) {
    seg = Math.round(seg || 0);
    const h = Math.floor(seg / 3600), m = Math.floor((seg % 3600) / 60), s = seg % 60;
    return h ? h + ":" + dos(m) + ":" + dos(s) : m + ":" + dos(s);
  };
  C.mostrarNumero = (n) => /^09\d{8}$/.test(n) ? n.slice(0, 3) + " " + n.slice(3, 6) + " " + n.slice(6) : n;
  C.plural = (n, uno, varios) => n + " " + (n === 1 ? uno : varios);
  C.sinTildes = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  /** Periodos rápidos → [desde, hasta]. La semana empieza el lunes. */
  C.rango = function (clave) {
    const hoy = new Date(), d = new Date(hoy);
    if (clave === "ayer") { d.setDate(d.getDate() - 1); return [C.fechaIso(d), C.fechaIso(d)]; }
    if (clave === "semana") { d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return [C.fechaIso(d), C.fechaIso(hoy)]; }
    if (clave === "mes") { d.setDate(1); return [C.fechaIso(d), C.fechaIso(hoy)]; }
    return [C.fechaIso(hoy), C.fechaIso(hoy)];
  };

  // ------------------------------------------------------------------ tiempo muerto
  // Tiempo muerto = tiempo entre el fin de una llamada y el inicio de la siguiente, el mismo día.
  // Solo cuentan como actividad del asesor las llamadas que hizo (salientes, contesten o no) y las que contestó;
  // una entrante perdida o rechazada no corta el tiempo muerto.
  C.PAUSA_LARGA = 8 * 60;    // desde aquí la pausa se marca en rojo (8 min sin timbrar); igual a Vigilante.LIMITE_MIN
  C.LARGA_TXT = (C.PAUSA_LARGA / 60) + " min";
  C.PAUSA_VISIBLE = 5 * 60;  // desde aquí se muestra en la lista del asesor
  C.esActividad = (l) => l.tp === "saliente" || l.d > 0;
  // Hora de almuerzo: no cuenta como tiempo muerto.
  C.ALMUERZO = ["13:00", "14:00"];
  const enMs = (fecha, hhmm) => { const d = C.deIso(fecha); d.setHours(+hhmm.slice(0, 2), +hhmm.slice(3, 5), 0, 0); return d.getTime(); };
  /**
   * Map llamada → segundos de tiempo muerto antes de ella (la primera llamada de cada día no tiene), sin la hora de
   * almuerzo. Además: m.inicio (llamada → ms en que empezó la pausa) y m.almuerzo (llamadas cuya pausa incluía el almuerzo).
   */
  C.tiemposMuertos = function (llamadas) {
    const m = new Map(), porDia = C.agrupar(llamadas.filter(C.esActividad), (l) => l.fecha);
    m.inicio = new Map();
    m.almuerzo = new Set();
    Object.keys(porDia).forEach(function (f) {
      const a0 = enMs(f, C.ALMUERZO[0]), a1 = enMs(f, C.ALMUERZO[1]);
      let fin = null;  // el fin más tardío hasta ahora (por si una llamada se solapa con otra)
      porDia[f].slice().sort((a, b) => a.t - b.t).forEach(function (l) {
        if (fin !== null) {
          const hueco = Math.max(0, l.t - fin), almuerzo = Math.max(0, Math.min(l.t, a1) - Math.max(fin, a0));
          m.set(l, Math.round((hueco - almuerzo) / 1000));
          m.inicio.set(l, fin);
          if (almuerzo > 0) m.almuerzo.add(l);
        }
        fin = Math.max(fin === null ? 0 : fin, l.t + (l.d || 0) * 1000);
      });
    });
    return m;
  };
  C.porcentaje = (x) => Math.round((x || 0) * 100) + " %";

  // ------------------------------------------------------------------ estadísticas
  // Llamada: {t (ms), fecha, hora, n, c, tp: saliente|entrante|perdida|rechazada|otra, d (seg), chip, b (1 = borrada del historial)}
  // Contestada = saliente con duración > 0 (Android solo cuenta el tiempo desde que el cliente contesta).
  C.estadisticas = function (llamadas) {
    const e = { total: llamadas.length, salientes: 0, contestadas: 0, noContestadas: 0, entrantes: 0, perdidas: 0, tiempo: 0,
                conDuracion: 0, numeros: 0, borradas: 0, primera: null, ultima: null, mayorPausa: 0, pausaDesde: "", pausaHasta: "", dias: 0,
                tiempoMuerto: 0, pausas: 0, promedioMuerto: 0, pausasLargas: 0, tiempoPausasLargas: 0, largas: [], ocupacion: 0 };
    const numeros = new Set(), porDia = {};
    llamadas.forEach(function (l) {
      if (l.tp === "saliente") { e.salientes++; if (l.d > 0) e.contestadas++; else e.noContestadas++; }
      else if (l.tp === "entrante") e.entrantes++;
      else if (l.tp === "perdida" || l.tp === "rechazada") e.perdidas++;
      e.tiempo += l.d || 0;
      if (l.d > 0) e.conDuracion++;
      if (l.b) e.borradas++;
      numeros.add(l.n);
      if (!e.primera || l.t < e.primera.t) e.primera = l;
      if (!e.ultima || l.t > e.ultima.t) e.ultima = l;
      (porDia[l.fecha] = porDia[l.fecha] || []).push(l);
    });
    e.numeros = numeros.size;
    e.dias = Object.keys(porDia).length;
    e.promedio = e.conDuracion ? e.tiempo / e.conDuracion : 0;
    // Tiempo muerto: total, promedio entre llamadas, mayor pausa y pausas largas (de C.PAUSA_LARGA o más).
    const muertos = C.tiemposMuertos(llamadas);
    muertos.forEach(function (seg, l) {
      e.pausas++;
      e.tiempoMuerto += seg;
      const desde = C.horaIso(new Date(muertos.inicio.get(l))), almuerzo = muertos.almuerzo.has(l);
      if (seg >= C.PAUSA_LARGA) { e.pausasLargas++; e.tiempoPausasLargas += seg; e.largas.push({ fecha: l.fecha, desde, hasta: l.hora, seg, almuerzo }); }
      if (seg > e.mayorPausa) { e.mayorPausa = seg; e.pausaDesde = desde; e.pausaHasta = l.hora; e.pausaDia = l.fecha; e.pausaAlmuerzo = almuerzo; }
    });
    e.largas.sort((a, b) => (a.fecha + a.hasta).localeCompare(b.fecha + b.hasta));
    e.promedioMuerto = e.pausas ? e.tiempoMuerto / e.pausas : 0;
    // Del tiempo entre la primera y la última llamada de cada día, qué parte fue en llamada.
    e.ocupacion = e.tiempo + e.tiempoMuerto ? e.tiempo / (e.tiempo + e.tiempoMuerto) : 0;
    return e;
  };
  C.agrupar = function (lista, clave) {
    const g = {};
    lista.forEach((x) => { const k = clave(x); (g[k] = g[k] || []).push(x); });
    return g;
  };
  /** Recuadro destacado de tiempo muerto para los PDF (bloque «destacado» de PdfDocumento). */
  C.bloqueTiempoMuerto = function (e, titulo) {
    const varios = e.dias > 1, hay = e.mayorPausa >= 60;
    return { t: "destacado", titulo: titulo || "Tiempo muerto entre llamadas", items: [
      ["Tiempo muerto total", C.duracion(e.tiempoMuerto), C.plural(e.pausas, "pausa entre llamadas", "pausas entre llamadas")],
      ["Tiempo hablado", C.duracion(e.tiempo), "en " + C.plural(e.total, "llamada", "llamadas")],
      ["Tiempo en llamada", C.porcentaje(e.ocupacion), "entre la primera y la última llamada"],
      ["Promedio entre llamadas", e.pausas ? C.duracion(e.promedioMuerto) : "—", "de una llamada a la siguiente"],
      ["Mayor tiempo muerto", hay ? C.duracion(e.mayorPausa) : "—", hay ? "de " + e.pausaDesde + " a " + e.pausaHasta + (varios ? " del " + C.fechaCorta(e.pausaDia).slice(0, 5) : "") + (e.pausaAlmuerzo ? ", sin almuerzo" : "") : ""],
      ["Pausas de " + C.LARGA_TXT + " o más", String(e.pausasLargas), e.pausasLargas ? "suman " + C.duracion(e.tiempoPausasLargas) : "ninguna", e.pausasLargas > 0]
    ], nota: "Tiempo muerto: el tiempo sin llamadas entre el fin de una llamada y el inicio de la siguiente, el mismo día. "
      + "No cuenta la hora de almuerzo (" + C.ALMUERZO[0] + " a " + C.ALMUERZO[1] + ") y no lo cortan las entrantes perdidas o rechazadas. "
      + "En rojo, las pausas de " + C.LARGA_TXT + " o más." };
  };
  C.pausaTexto = (e) => e.mayorPausa >= 60 ? C.duracion(e.mayorPausa) + " (" + e.pausaDesde + "–" + e.pausaHasta + (e.pausaAlmuerzo ? ", sin almuerzo" : "") + ")" : "—";
  /** Duración de una pausa larga, avisando si se le descontó el almuerzo. */
  C.pausaLargaTexto = (p) => C.duracion(p.seg) + (p.almuerzo ? " (sin almuerzo)" : "");

  // ------------------------------------------------------------------ ventanas, avisos y «Atrás»
  let abierta = null;
  C.abrir = function (id) { C.cerrar(); abierta = C.$(id); abierta.hidden = false; };
  C.cerrar = function () { if (abierta) { abierta.hidden = true; abierta = null; } };
  C.hayAbierta = () => !!abierta;
  document.addEventListener("click", function (e) {
    const m = e.target.closest(".fondo-modal");
    if (!m) return;
    if ((e.target === m && !m.hasAttribute("data-fijo")) || e.target.closest("[data-cerrar]")) C.cerrar();
  });
  /** Atrás de Android: primero cierra la ventana abierta; si no, lo que diga la página (C.alVolver). */
  C.alVolver = null;
  window.atras = function () {
    if (abierta) { C.cerrar(); return true; }
    return C.alVolver ? C.alVolver() : false;
  };
  let temporizador = null;
  window.avisar = C.avisar = function (texto, tipo) {
    const a = C.$("aviso");
    if (!a) return;
    a.textContent = texto;
    a.className = "aviso" + (tipo === "error" ? " error" : "");
    clearTimeout(temporizador);
    temporizador = setTimeout(() => { a.className += " oculto"; }, tipo === "error" ? 6000 : 2600);
  };

  C.json = function (texto, defecto) { try { return JSON.parse(texto); } catch (e) { return defecto; } };
  C.leer = function (clave, defecto) { try { const v = JSON.parse(localStorage.getItem(clave)); return v == null ? defecto : v; } catch (e) { return defecto; } };
  C.escribir = function (clave, valor) { try { localStorage.setItem(clave, JSON.stringify(valor)); } catch (e) {} };

  // Íconos de tipo de llamada
  C.ICONOS = {
    saliente: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg>',
    entrante: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M17 7L7 17M15 17H7V9"/></svg>',
    perdida: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7l6 6 4-4 6 6"/></svg>',
    rechazada: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M17 7L7 17M7 7l10 10"/></svg>',
    otra: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="2"/></svg>'
  };

  window.CF = C;
})();
