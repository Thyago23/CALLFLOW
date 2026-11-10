/*
 * Push del navegador (Web Push): botón para activarlo en este equipo, en el panel de la campana y
 * en /notificaciones/. Los avisos dentro de la página los muestra el sistema de avisos flotantes
 * (contadores cada 15 s); el push cubre cuando CALLFLOW está minimizado o cerrado.
 * Todo el texto se inserta con textContent, nunca como HTML.
 */
(() => {
  "use strict";
  const cfg = document.getElementById("config-notificaciones");
  if (!cfg) return;
  const URLS = cfg.dataset;
  const compatible = window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  let estado = "cargando";

  function csrf() {
    try {
      return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || "";
    } catch (e) {
      return "";
    }
  }

  async function enviar(url, datos) {
    const respuesta = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(datos),
    });
    if (!respuesta.ok) throw new Error("HTTP " + respuesta.status);
  }

  function crear(etiqueta, clases, texto) {
    const el = document.createElement(etiqueta);
    if (clases) el.className = clases;
    if (texto !== undefined) el.textContent = texto;
    return el;
  }

  function claveServidor(base64) {
    const relleno = "=".repeat((4 - (base64.length % 4)) % 4);
    const datos = atob((base64 + relleno).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(datos, (c) => c.charCodeAt(0));
  }

  async function suscripcionActual() {
    if (!compatible) return null;
    const registro = await navigator.serviceWorker.getRegistration("/");
    return registro ? registro.pushManager.getSubscription() : null;
  }

  const TEXTOS = {
    activo: ["bi-bell-fill text-success", "Avisos activados en este equipo"],
    inactivo: ["bi-bell", "Reciba avisos aunque CALLFLOW esté cerrado"],
    bloqueado: ["bi-bell-slash", "Bloqueados en el navegador: permítalos desde el candado de la barra de direcciones."],
    "sin-configurar": ["bi-info-circle", "El administrador aún no configuró los avisos push."],
    "no-compatible": ["bi-info-circle", "Este navegador o conexión no admite avisos push (se necesita HTTPS)."],
  };

  function pintar() {
    document.querySelectorAll("[data-estado-push]").forEach((zona) => {
      zona.replaceChildren();
      // En la campana la opción desaparece una vez activados (se puede desactivar en /notificaciones/).
      zona.hidden = estado === "activo" && zona.hasAttribute("data-ocultar-si-activo");
      if (!TEXTOS[estado]) return;
      const [icono, mensaje] = TEXTOS[estado];
      const fila = crear("div", "d-flex align-items-center justify-content-between gap-2");
      const texto = crear("span", "text-body-secondary");
      const i = crear("i", `bi ${icono} me-1`);
      i.setAttribute("aria-hidden", "true");
      texto.append(i, mensaje);
      fila.append(texto);
      if (estado === "activo" || estado === "inactivo") {
        const boton = crear("button", estado === "activo" ? "btn btn-sm btn-link p-0 text-decoration-none" : "btn btn-sm btn-primary text-nowrap",
          estado === "activo" ? "Desactivar" : "Activar");
        boton.type = "button";
        boton.addEventListener("click", estado === "activo" ? desactivar : activar);
        fila.append(boton);
      }
      zona.append(fila);
    });
  }

  async function refrescar() {
    if (!compatible) estado = "no-compatible";
    else if (!URLS.vapid) estado = "sin-configurar";
    else if (Notification.permission === "denied") estado = "bloqueado";
    else {
      const suscripcion = await suscripcionActual();
      const activo = Boolean(suscripcion) && Notification.permission === "granted";
      if (activo) enviar(URLS.suscribir, suscripcion.toJSON()).catch(() => {});  // queda a nombre de quien tiene la sesión
      estado = activo ? "activo" : "inactivo";
    }
    pintar();
  }

  async function activar(evento) {
    evento.currentTarget.disabled = true;
    try {
      if (await Notification.requestPermission() === "granted") {
        const registro = await navigator.serviceWorker.register(URLS.sw, { scope: "/" });
        await navigator.serviceWorker.ready;
        const suscripcion = await registro.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: claveServidor(URLS.vapid) });
        await enviar(URLS.suscribir, suscripcion.toJSON());
      }
    } catch (e) {
      console.error("No se pudo activar el push", e);
    }
    refrescar();
  }

  async function desactivar() {
    const suscripcion = await suscripcionActual();
    if (suscripcion) {
      await enviar(URLS.desuscribir, { endpoint: suscripcion.endpoint }).catch(() => {});
      await suscripcion.unsubscribe();
    }
    refrescar();
  }

  // El panel de la campana llega por HTMX: se vuelve a pintar el estado cuando aparece.
  document.body.addEventListener("htmx:afterSwap", pintar);

  // Al cerrar sesión, este navegador deja de recibir los avisos de esa persona: se borra la suscripción en
  // el servidor, pero el navegador la conserva para no tener que «Activar» otra vez en cada inicio de sesión
  // (al volver a entrar, refrescar() la registra a nombre de quien tenga la sesión).
  document.querySelectorAll("form[data-cerrar-sesion]").forEach((form) => {
    form.addEventListener("submit", (evento) => {
      // defaultPrevented: la ventana de confirmación frenó el envío; se espera a que la persona acepte.
      if (!compatible || form.dataset.listo || evento.defaultPrevented) return;
      evento.preventDefault();  // hay que frenarlo antes de cualquier await
      (async () => {
        try {
          const suscripcion = await suscripcionActual();
          if (suscripcion) await enviar(URLS.desuscribir, { endpoint: suscripcion.endpoint }).catch(() => {});
        } catch (e) {
          /* aunque falle, se cierra la sesión */
        } finally {
          form.dataset.listo = "1";
          form.submit();
        }
      })();
    });
  });

  refrescar();
})();
