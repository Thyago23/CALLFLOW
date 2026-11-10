/*
 * Service worker de CALLFLOW: recibe las notificaciones push y las muestra como avisos del
 * sistema operativo. Si la persona tiene CALLFLOW abierto y a la vista, no se repite el aviso:
 * ya lo ve dentro del sistema.
 */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (evento) => evento.waitUntil(self.clients.claim()));

self.addEventListener("push", (evento) => {
  let datos = {};
  try {
    datos = evento.data ? evento.data.json() : {};
  } catch (e) {
    datos = { title: "CALLFLOW", body: evento.data ? evento.data.text() : "" };
  }
  evento.waitUntil((async () => {
    const ventanas = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    if (ventanas.some((v) => v.visibilityState === "visible" && v.focused)) return;
    await self.registration.showNotification(datos.title || "CALLFLOW", {
      body: datos.body || "",
      icon: datos.icon || undefined,
      badge: datos.icon || undefined,
      tag: datos.tag,
      data: { url: datos.url || "/notificaciones/" },
      lang: "es",
    });
  })());
});

self.addEventListener("notificationclick", (evento) => {
  evento.notification.close();
  const destino = new URL(evento.notification.data && evento.notification.data.url || "/notificaciones/", self.location.origin);
  evento.waitUntil((async () => {
    const ventanas = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const ventana of ventanas) {
      if (new URL(ventana.url).origin === destino.origin && "focus" in ventana) {
        await ventana.focus();
        return ventana.navigate(destino.href);
      }
    }
    return self.clients.openWindow(destino.href);
  })());
});
