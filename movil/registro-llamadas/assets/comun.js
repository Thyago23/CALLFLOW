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

  // ------------------------------------------------------------------ estadísticas
  // Llamada: {t (ms), fecha, hora, n, c, tp: saliente|entrante|perdida|rechazada|otra, d (seg), chip, b (1 = borrada del historial)}
  // Contestada = saliente con duración > 0 (Android solo cuenta el tiempo desde que el cliente contesta).
  C.estadisticas = function (llamadas) {
    const e = { total: llamadas.length, salientes: 0, contestadas: 0, noContestadas: 0, entrantes: 0, perdidas: 0, tiempo: 0,
                conDuracion: 0, numeros: 0, borradas: 0, primera: null, ultima: null, mayorPausa: 0, pausaDesde: "", pausaHasta: "", dias: 0 };
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
    // Mayor pausa: el tiempo más largo entre el fin de una llamada y el inicio de la siguiente, el mismo día.
    Object.keys(porDia).forEach(function (f) {
      const ls = porDia[f].slice().sort((a, b) => a.t - b.t);
      for (let i = 1; i < ls.length; i++) {
        const fin = ls[i - 1].t + (ls[i - 1].d || 0) * 1000, pausa = (ls[i].t - fin) / 1000;
        if (pausa > e.mayorPausa) { e.mayorPausa = pausa; e.pausaDesde = C.horaIso(new Date(fin)); e.pausaHasta = ls[i].hora; e.pausaDia = f; }
      }
    });
    return e;
  };
  C.agrupar = function (lista, clave) {
    const g = {};
    lista.forEach((x) => { const k = clave(x); (g[k] = g[k] || []).push(x); });
    return g;
  };
  C.pausaTexto = (e) => e.mayorPausa >= 60 ? C.duracion(e.mayorPausa) + " (" + e.pausaDesde + "–" + e.pausaHasta + ")" : "—";

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
