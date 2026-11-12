/*
 * Mensajería tipo Messenger (TI, administradores y supervisores).
 * - Burbujas agrupadas por persona y separadores de día («Hoy», «Ayer»…).
 * - Escribir fácil: Enter envía, la caja crece, emojis, 👍 rápido, pegar o arrastrar imágenes, «escribiendo…».
 * - Chat flotante (abajo a la derecha) en todas las pantallas: lista de chats y ventanitas que se
 *   minimizan, se cierran y siguen abiertas al cambiar de página (se recuerdan en este navegador).
 */
(function () {
  "use strict";

  var MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  var CLAVE = "cf-chats-abiertos";

  function csrf() {
    try { return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || ""; } catch (e) { return ""; }
  }
  function normalizar(t) {
    return (t || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").trim();
  }

  // ------------------------------------------------------------------ burbujas y fechas
  function etiquetaDia(fecha) {
    var hoy = new Date(); hoy.setHours(0, 0, 0, 0);
    var dia = new Date(fecha); dia.setHours(0, 0, 0, 0);
    var dias = Math.round((hoy - dia) / 86400000);
    if (dias === 0) return "Hoy";
    if (dias === 1) return "Ayer";
    return dia.getDate() + " de " + MESES[dia.getMonth()] + (dia.getFullYear() !== hoy.getFullYear() ? " de " + dia.getFullYear() : "");
  }

  function agrupar(hilo) {
    if (!hilo) return;
    hilo.querySelectorAll(".separador-fecha").forEach(function (s) { s.remove(); });
    var vistos = {};
    hilo.querySelectorAll(".mensaje").forEach(function (m) {  // sin repetidos (envío y sondeo a la vez)
      if (vistos[m.dataset.id]) m.remove(); else vistos[m.dataset.id] = true;
    });
    var mensajes = hilo.querySelectorAll(".mensaje");
    if (mensajes.length) { var vacio = hilo.querySelector(".inicio-chat"); if (vacio) vacio.remove(); }
    var anterior = null, diaAnterior = null;
    mensajes.forEach(function (m) {
      var hora = new Date(m.dataset.ts), dia = hora.toDateString();
      if (dia !== diaAnterior) {
        var sep = document.createElement("div");
        sep.className = "separador-fecha";
        sep.textContent = etiquetaDia(hora);
        m.parentNode.insertBefore(sep, m);
        if (anterior) anterior.classList.add("ultimo");
        anterior = null;
      }
      var seguido = anterior && anterior.dataset.autor === m.dataset.autor && hora - new Date(anterior.dataset.ts) < 5 * 60000;
      m.classList.toggle("primero", !seguido);
      m.classList.remove("ultimo");
      if (anterior) anterior.classList.toggle("ultimo", !seguido);
      anterior = m; diaAnterior = dia;
    });
    if (anterior) anterior.classList.add("ultimo");
  }
  function alFinal(hilo) { if (hilo) hilo.scrollTop = hilo.scrollHeight; }

  // ------------------------------------------------------------------ escribir
  function prepararComposer(form) {
    if (!form || form.dataset.listo) return;
    form.dataset.listo = "1";
    var caja = form.querySelector("textarea"), archivo = form.querySelector("input[type=file]");
    var previa = form.querySelector(".vista-previa-adjunto"), selector = form.querySelector(".selector-emojis");
    var ventana = form.closest(".ventana-chat"), ultimoAviso = 0;

    function actualizar() {
      var hay = caja.value.trim() !== "" || (archivo.files && archivo.files.length > 0);
      form.classList.toggle("con-texto", hay);
      caja.style.height = "auto";
      caja.style.height = Math.min(caja.scrollHeight, 140) + "px";
    }
    function mostrarPrevia() {
      var f = archivo.files && archivo.files[0];
      previa.hidden = !f;
      if (f) {
        previa.querySelector(".nombre").textContent = f.name;
        var img = previa.querySelector("img"), esImagen = f.type.indexOf("image/") === 0;
        img.hidden = !esImagen;
        previa.querySelector(".icono-archivo").hidden = esImagen;
        if (esImagen) img.src = URL.createObjectURL(f);
      }
      actualizar();
    }
    function ponerArchivo(f) {
      if (!f) return;
      var dt = new DataTransfer();
      dt.items.add(f.name ? f : new File([f], "captura.png", {type: f.type || "image/png"}));
      archivo.files = dt.files;
      mostrarPrevia();
      caja.focus();
    }

    caja.addEventListener("input", function () {
      actualizar();
      var ahora = Date.now();  // «escribiendo…»: un aviso cada 3 s como mucho
      if (caja.value && ahora - ultimoAviso > 3000) {
        ultimoAviso = ahora;
        fetch(form.dataset.urlEscribiendo, {method: "POST", headers: {"X-CSRFToken": csrf()}}).catch(function () {});
      }
    });
    caja.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (form.classList.contains("con-texto")) form.requestSubmit(); }
    });
    caja.addEventListener("paste", function (e) {
      var items = (e.clipboardData && e.clipboardData.items) || [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].kind === "file") { ponerArchivo(items[i].getAsFile()); e.preventDefault(); return; }
      }
    });
    archivo.addEventListener("change", mostrarPrevia);
    previa.querySelector(".quitar").addEventListener("click", function () { archivo.value = ""; mostrarPrevia(); });
    ventana.addEventListener("dragover", function (e) {
      if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, "Files") >= 0) { e.preventDefault(); ventana.classList.add("soltando"); }
    });
    ventana.addEventListener("dragleave", function (e) { if (!ventana.contains(e.relatedTarget)) ventana.classList.remove("soltando"); });
    ventana.addEventListener("drop", function (e) {
      ventana.classList.remove("soltando");
      if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); ponerArchivo(e.dataTransfer.files[0]); }
    });
    form.querySelector(".me-gusta").addEventListener("click", function () { caja.value = "👍"; actualizar(); form.requestSubmit(); });
    form.querySelector(".boton-emoji").addEventListener("click", function () { selector.hidden = !selector.hidden; });
    selector.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-emoji]");
      if (!b) return;
      var i = caja.selectionStart || caja.value.length;
      caja.value = caja.value.slice(0, i) + b.dataset.emoji + caja.value.slice(caja.selectionEnd || i);
      caja.focus();
      caja.selectionStart = caja.selectionEnd = i + b.dataset.emoji.length;
      actualizar();
    });
    form.addEventListener("htmx:afterRequest", function (e) {
      if (e.detail.successful && !form.querySelector(".error-envio .alert")) {
        form.reset(); archivo.value = ""; mostrarPrevia(); selector.hidden = true; actualizar();
      }
    });
    actualizar();
  }

  function prepararVentana(v) {
    if (!v) return;
    var hilo = v.querySelector(".hilo");
    agrupar(hilo);
    alFinal(hilo);
    prepararComposer(v.querySelector("form.escribir"));
    var imagenes = hilo.querySelectorAll("img");
    imagenes.forEach(function (img) { img.addEventListener("load", function () { alFinal(hilo); }, {once: true}); });
  }

  // Mensajes nuevos (envío o sondeo): agrupar y bajar al último.
  document.body.addEventListener("htmx:oobAfterSwap", function (e) {
    var t = e.detail.target;
    if (t && t.classList && t.classList.contains("hilo")) {
      agrupar(t); alFinal(t);
      t.querySelectorAll("img").forEach(function (img) { img.addEventListener("load", function () { alFinal(t); }, {once: true}); });
    }
  });

  // ------------------------------------------------------------------ buscar en el panel de chats
  function filtrar(panel) {
    var campo = panel.querySelector("[data-buscar-chats]");
    if (!campo) return;
    var q = normalizar(campo.value), hay = false;
    panel.querySelectorAll(".item-chat, .persona").forEach(function (el) {
      var ok = !q || normalizar(el.dataset.buscar).indexOf(q) >= 0;
      el.hidden = !ok;
      if (ok) hay = true;
    });
    var sin = panel.querySelector(".sin-resultados");
    if (sin) sin.hidden = hay || !q;
  }
  document.addEventListener("input", function (e) {
    if (e.target.matches("[data-buscar-chats]")) filtrar(e.target.closest(".panel-chats"));
    if (e.target.matches("[data-buscar-miembros]")) {
      var q = normalizar(e.target.value);
      e.target.closest("dialog").querySelectorAll(".elegir-persona").forEach(function (el) { el.hidden = q && normalizar(el.dataset.buscar).indexOf(q) < 0; });
    }
  });
  document.body.addEventListener("htmx:afterSwap", function (e) {
    var panel = e.detail.target.closest && e.detail.target.closest(".panel-chats");
    if (panel) filtrar(panel);
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-nuevo-grupo]");
    if (b) { b.closest(".panel-chats").querySelector(".dialogo-grupo").showModal(); return; }
    var c = e.target.closest("[data-cerrar-dialogo]");
    if (c) c.closest("dialog").close();
  });

  // ------------------------------------------------------------------ página Mensajes
  document.querySelectorAll(".ventana-chat.pagina").forEach(prepararVentana);

  // ------------------------------------------------------------------ chat flotante (todas las pantallas)
  var dock = document.getElementById("dock-chats");
  var estado = [];
  window.CallflowChat = {leer: function () { return "1"; }};
  if (!dock) return;

  var zona = document.getElementById("dock-ventanas"), panel = document.getElementById("dock-panel");
  var boton = document.getElementById("dock-boton");
  var maximo = function () { return window.innerWidth < 768 ? 1 : (window.innerWidth < 1200 ? 2 : 3); };
  try { estado = JSON.parse(localStorage.getItem(CLAVE)) || []; } catch (e) { estado = []; }
  function guardar() { try { localStorage.setItem(CLAVE, JSON.stringify(estado)); } catch (e) {} }
  function ventanaDe(pk) { return zona.querySelector('.ventana-chat[data-chat="' + pk + '"]'); }

  function abrir(pk, minimizada) {
    pk = String(pk);
    togglePanel(false);
    var v = ventanaDe(pk);
    if (v) {
      v.classList.remove("minimizada");
      marcar(pk, false);
      var caja = v.querySelector("textarea"); if (caja) caja.focus();
      return;
    }
    while (estado.length >= maximo()) cerrar(estado[0].pk);
    estado.push({pk: pk, min: !!minimizada});
    guardar();
    htmx.ajax("GET", dock.dataset.urlVentana.replace("/0/", "/" + pk + "/"), {target: zona, swap: "beforeend"}).then(function () {
      var nueva = ventanaDe(pk);
      if (!nueva) { cerrar(pk); return; }  // ya no participa en ese chat
      if (minimizada) nueva.classList.add("minimizada");
      prepararVentana(nueva);
      if (!minimizada) { var caja = nueva.querySelector("textarea"); if (caja) caja.focus(); }
    }).catch(function () { cerrar(pk); });
  }
  function cerrar(pk) {
    pk = String(pk);
    var v = ventanaDe(pk); if (v) v.remove();
    estado = estado.filter(function (x) { return x.pk !== pk; });
    guardar();
  }
  function marcar(pk, minimizada) {
    estado.forEach(function (x) { if (x.pk === String(pk)) x.min = minimizada; });
    guardar();
  }
  function minimizar(pk) {
    var v = ventanaDe(pk), min = v.classList.toggle("minimizada");
    marcar(pk, min);
    if (!min) alFinal(v.querySelector(".hilo"));
  }
  function togglePanel(abrirlo) {
    if (abrirlo === undefined) abrirlo = panel.hidden;
    if (abrirlo && panel.hidden) {
      htmx.ajax("GET", dock.dataset.urlPanel, {target: panel, swap: "innerHTML"}).then(function () {
        var q = panel.querySelector("[data-buscar-chats]"); if (q) q.focus();
      });
    }
    panel.hidden = !abrirlo;
    boton.setAttribute("aria-expanded", abrirlo ? "true" : "false");
  }
  window.CallflowChat.leer = function (pk) {
    var v = ventanaDe(pk);
    return v && v.classList.contains("minimizada") ? "0" : "1";
  };

  boton.addEventListener("click", function () { togglePanel(); });
  document.querySelectorAll("[data-abrir-dock]").forEach(function (a) {
    a.addEventListener("click", function (e) { e.preventDefault(); togglePanel(true); });
  });
  dock.addEventListener("click", function (e) {
    var accion = e.target.closest("[data-accion]");
    if (accion) {
      var pk = accion.closest(".ventana-chat").dataset.chat;
      if (accion.dataset.accion === "cerrar") cerrar(pk); else minimizar(pk);
      return;
    }
    var item = e.target.closest("[data-abrir-chat]");
    if (item) { e.preventDefault(); abrir(item.dataset.abrirChat); return; }
    var cabecera = e.target.closest(".ventana-chat.minimizada .cabecera-chat");
    if (cabecera && !e.target.closest("a")) minimizar(cabecera.closest(".ventana-chat").dataset.chat);
  });
  document.body.addEventListener("abrirChat", function (e) {
    document.querySelectorAll("dialog[open]").forEach(function (d) { d.close(); });
    abrir(e.detail.pk);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) togglePanel(false); });
  document.addEventListener("click", function (e) {
    if (!panel.hidden && !dock.contains(e.target) && !e.target.closest("[data-abrir-dock]") && !e.target.closest("dialog")) togglePanel(false);
  });

  // Las ventanitas que estaban abiertas en la página anterior.
  var previas = estado.slice();
  estado = [];
  previas.forEach(function (x) { abrir(x.pk, x.min); });
})();
