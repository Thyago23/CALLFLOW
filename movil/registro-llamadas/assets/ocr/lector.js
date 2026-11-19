/*
 * Lector de texto de las fotos del registro de llamadas (Tesseract, en el teléfono, sin internet).
 * La página se sirve desde https://appassets.androidplatform.net (MainActivity la entrega desde assets), así
 * Tesseract puede usar su Worker. Uso: window.LectorFotos.leer(blob, progreso) → Promise<[{t, x0, y0, x1, y1, c}]>.
 */
(function () {
  "use strict";
  const BASE = new URL("ocr/", location.href).href;
  let lector = null;
  let alProgreso = null;

  function obtener() {
    if (!lector) {
      lector = Tesseract.createWorker("spa", 1, {
        workerPath: BASE + "worker.min.js",
        corePath: BASE,
        langPath: BASE,
        workerBlobURL: false,
        cacheMethod: "none",
        logger: function (m) { if (alProgreso && m.status === "recognizing text") alProgreso(m.progress); }
      }).then(async function (w) {
        // 11 = texto disperso: una pantalla de celular no es un párrafo, son filas sueltas.
        await w.setParameters({ tessedit_pageseg_mode: "11", preserve_interword_spaces: "1" });
        return w;
      }).catch(function (e) { lector = null; throw e; });
    }
    return lector;
  }

  /** Escala de grises, tamaño cómodo para leer y, si la pantalla es oscura (texto claro), se invierte. */
  async function preparar(blob) {
    const img = await createImageBitmap(blob);
    const lado = Math.max(img.width, img.height);
    let escala = lado > 2400 ? 2400 / lado : 1;
    if (img.width * escala < 1000) escala = 1000 / img.width;  // capturas pequeñas: se agrandan
    const c = document.createElement("canvas");
    c.width = Math.round(img.width * escala);
    c.height = Math.round(img.height * escala);
    const g = c.getContext("2d", { willReadFrequently: true });
    g.drawImage(img, 0, 0, c.width, c.height);
    const datos = g.getImageData(0, 0, c.width, c.height), px = datos.data;
    let suma = 0;
    for (let i = 0; i < px.length; i += 4) {
      const l = 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
      px[i] = px[i + 1] = px[i + 2] = l;
      suma += l;
    }
    const invertir = suma / (px.length / 4) < 110;
    // Contraste: estira el gris hacia blanco y negro para que los números se lean mejor.
    for (let i = 0; i < px.length; i += 4) {
      let l = invertir ? 255 - px[i] : px[i];
      l = Math.max(0, Math.min(255, (l - 128) * 1.35 + 128));
      px[i] = px[i + 1] = px[i + 2] = l;
    }
    g.putImageData(datos, 0, 0);
    return c;
  }

  async function leer(blob, progreso) {
    const lienzo = await preparar(blob);
    const w = await obtener();
    alProgreso = progreso || null;
    try {
      const r = await w.recognize(lienzo);
      return (r.data.words || []).map(function (p) {
        return { t: p.text, x0: p.bbox.x0, y0: p.bbox.y0, x1: p.bbox.x1, y1: p.bbox.y1, c: p.confidence };
      });
    } finally {
      alProgreso = null;
    }
  }

  window.LectorFotos = { leer: leer };
})();
