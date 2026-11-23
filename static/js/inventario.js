/* Bodega (inventario): vista de tarjetas/lista, ventana rápida de movimientos y ayudas del formulario
   (− / + en la cantidad, atajos, motivos sugeridos y vista previa del stock que quedará). */
(function () {
  "use strict";
  const SUMAN = ["ENTRADA", "AJUSTE_POSITIVO"];
  const CON_MOTIVO = ["AJUSTE_POSITIVO", "AJUSTE_NEGATIVO"];
  const datosMateriales = (function () {
    const el = document.getElementById("datos-materiales");
    try { return el ? JSON.parse(el.textContent) : {}; } catch (e) { return {}; }
  })();

  function numero(valor) {
    const n = parseFloat(String(valor || "").replace(",", "."));
    return isNaN(n) ? null : n;
  }
  function formato(n) { return n.toLocaleString("es-EC", {maximumFractionDigits: 2}); }

  // ------------------------------------------------------------------ formulario de movimiento
  function tipoElegido(form) {
    const marcado = form.querySelector("input[name=tipo]:checked");
    return marcado ? marcado.value : "";
  }

  function actualizar(form) {
    const tipo = tipoElegido(form);
    const stock = numero(form.dataset.stock);
    const minimo = numero(form.dataset.minimo) || 0;
    const unidad = form.dataset.unidad || "";
    const cantidad = numero(form.querySelector("input[name=cantidad]").value);

    // Atajos y motivos sugeridos: solo los que aplican al tipo elegido.
    form.querySelectorAll("[data-para]").forEach(function (b) {
      b.hidden = !!tipo && b.dataset.para.split(" ").indexOf(tipo) === -1;
    });
    const obligatorio = CON_MOTIVO.indexOf(tipo) !== -1;
    form.querySelectorAll("[data-motivo-obligatorio]").forEach(function (el) { el.classList.toggle("d-none", !obligatorio); });
    form.querySelectorAll("[data-motivo-opcional]").forEach(function (el) { el.classList.toggle("d-none", obligatorio); });

    const previa = form.querySelector("[data-vista-previa]");
    if (!previa) return;
    const antes = previa.querySelector("[data-antes]");
    const despues = previa.querySelector("[data-despues]");
    const aviso = previa.querySelector("[data-aviso]");
    previa.classList.remove("ok", "bajo", "error");
    if (stock === null) {
      antes.textContent = "—"; despues.textContent = "—";
      aviso.textContent = "Elija un material para ver cómo queda el stock.";
      return;
    }
    antes.textContent = formato(stock) + " " + unidad;
    if (!tipo || cantidad === null || cantidad <= 0) {
      despues.textContent = "—"; aviso.textContent = "";
      return;
    }
    const resultado = SUMAN.indexOf(tipo) !== -1 ? stock + cantidad : stock - cantidad;
    despues.textContent = formato(resultado) + " " + unidad;
    if (resultado < 0) {
      previa.classList.add("error");
      aviso.textContent = "No alcanza: solo hay " + formato(stock) + ".";
    } else if (resultado <= minimo) {
      previa.classList.add("bajo");
      aviso.textContent = "Quedará en o bajo el mínimo (" + formato(minimo) + "): se avisará a los administradores.";
    } else {
      previa.classList.add("ok");
      aviso.textContent = "";
    }
  }

  function elegirMaterial(form, select) {
    const datos = datosMateriales[select.value];
    form.dataset.stock = datos ? datos.stock : "";
    form.dataset.minimo = datos ? datos.minimo : "";
    form.dataset.unidad = datos ? datos.unidad : "";
    const elegido = form.querySelector("[data-material-elegido]");
    if (elegido) {
      elegido.hidden = !datos;
      if (datos) elegido.textContent = datos.categoria + " · hay " + formato(datos.stock) + " " + datos.unidad + " · mínimo " + formato(datos.minimo);
    }
    actualizar(form);
  }

  function preparar(raiz) {
    (raiz || document).querySelectorAll("form[data-movimiento]").forEach(function (form) {
      const select = form.querySelector("select[name=material]");
      if (select) elegirMaterial(form, select); else actualizar(form);
    });
  }

  document.addEventListener("click", function (e) {
    const form = e.target.closest("form[data-movimiento]");
    if (!form) return;
    const campo = form.querySelector("input[name=cantidad]");
    const paso = e.target.closest("[data-paso]");
    const atajo = e.target.closest("[data-cantidad]");
    const motivo = e.target.closest("[data-motivo]");
    if (paso) {
      const nuevo = Math.max(0, (numero(campo.value) || 0) + Number(paso.dataset.paso));
      campo.value = nuevo || "";
    } else if (atajo) {
      campo.value = atajo.dataset.cantidad === "todo" ? (numero(form.dataset.stock) || "") : atajo.dataset.cantidad;
    } else if (motivo) {
      const entrada = form.querySelector("input[name=motivo]");
      entrada.value = motivo.dataset.motivo;
      entrada.focus();
    } else {
      return;
    }
    actualizar(form);
  });

  document.addEventListener("input", function (e) {
    const form = e.target.closest && e.target.closest("form[data-movimiento]");
    if (form) actualizar(form);
  });
  document.addEventListener("change", function (e) {
    const form = e.target.closest && e.target.closest("form[data-movimiento]");
    if (!form) return;
    if (e.target.name === "material") elegirMaterial(form, e.target); else actualizar(form);
  });

  // Filtro de la lista de materiales del formulario completo (escribe y la lista se reduce).
  document.addEventListener("input", function (e) {
    if (!e.target.matches || !e.target.matches("[data-filtrar-select]")) return;
    const select = document.getElementById(e.target.dataset.filtrarSelect);
    const texto = e.target.value.trim().toLowerCase();
    let primera = null;
    Array.from(select.options).forEach(function (op) {
      if (!op.value) return;
      const visible = !texto || op.text.toLowerCase().indexOf(texto) !== -1;
      op.hidden = !visible;
      if (visible && !primera) primera = op;
    });
    const actual = select.selectedOptions[0];
    if (texto && primera && (!actual || !actual.value || actual.hidden)) {
      select.value = primera.value;
      select.dispatchEvent(new Event("change", {bubbles: true}));
    }
  });

  // ------------------------------------------------------------------ ventana rápida
  const modal = document.getElementById("modal-movimiento");
  if (modal) {
    const cuerpo = document.getElementById("modal-movimiento-cuerpo");
    const cargando = cuerpo.innerHTML;
    modal.addEventListener("hidden.bs.modal", function () { cuerpo.innerHTML = cargando; });
    document.body.addEventListener("htmx:afterSwap", function (e) {
      if (e.detail.target !== cuerpo) return;
      preparar(cuerpo);
      // El cursor va directo a la cantidad (o al primer error).
      const campo = cuerpo.querySelector("[aria-invalid=true]") || cuerpo.querySelector("input[name=cantidad]");
      if (campo) setTimeout(function () { campo.focus(); if (campo.select) campo.select(); }, 150);
    });
    document.body.addEventListener("inventarioActualizado", function () {
      const instancia = bootstrap.Modal.getInstance(modal);
      if (instancia) instancia.hide();
      const filtros = document.getElementById("filtros-bodega");
      if (filtros) htmx.trigger(filtros, "submit");
    });
  }

  // ------------------------------------------------------------------ vista tarjetas / lista (se recuerda por navegador)
  const contenedor = document.getElementById("tabla-materiales");
  const botonesVista = document.querySelectorAll(".selector-vista [data-vista]");
  function ponerVista(vista) {
    if (!contenedor) return;
    contenedor.dataset.vistaActual = vista;
    botonesVista.forEach(function (b) {
      const activo = b.dataset.vista === vista;
      b.classList.toggle("active", activo);
      b.setAttribute("aria-pressed", String(activo));
    });
  }
  if (contenedor && botonesVista.length) {
    let guardada = "tarjetas";
    try { guardada = localStorage.getItem("cf-bodega-vista") || "tarjetas"; } catch (e) {}
    ponerVista(guardada);
    botonesVista.forEach(function (b) {
      b.addEventListener("click", function () {
        ponerVista(b.dataset.vista);
        try { localStorage.setItem("cf-bodega-vista", b.dataset.vista); } catch (e) {}
      });
    });
  }

  // «/» lleva al buscador de la bodega.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    const activo = document.activeElement;
    if (activo && (activo.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(activo.tagName))) return;
    const buscador = document.getElementById("busqueda");
    if (buscador) { e.preventDefault(); buscador.focus(); buscador.select(); }
  });

  preparar(document);
})();
