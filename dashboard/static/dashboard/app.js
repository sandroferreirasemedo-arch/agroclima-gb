/* AgroClima Guiné-Bissau — frontend (Leaflet + Chart.js) */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const LAYERS = JSON.parse($("layers-data").textContent);
  const LAYER_META = Object.fromEntries(LAYERS.map((l) => [l.id, l]));

  // Escalas do mapa coroplético (valores mensais por região)
  const CHORO_SCALE = {
    rain: { min: 0, max: 500, palette: LAYER_META.rain.palette, units: "mm" },
    rain_anom: { min: -60, max: 60, palette: LAYER_META.rain_anom.palette, units: "%" },
    spi3: { min: -2, max: 2, palette: LAYER_META.spi3.palette, units: "σ" },
    ndvi: { min: 0, max: 0.9, palette: LAYER_META.ndvi.palette, units: "" },
    ndvi_anom: { min: -0.2, max: 0.2, palette: LAYER_META.ndvi_anom.palette, units: "" },
    vci: { min: 0, max: 100, palette: LAYER_META.vci.palette, units: "%" },
    vhi: { min: 0, max: 100, palette: LAYER_META.vhi.palette, units: "%" },
  };

  const state = {
    tab: "mapa",
    drawing: false,
    mapLegend: null,
    target: null, // {region_id} | {aoi_id} | {geometry}
    targetLabel: "",
    lastResult: null,
    loading: 0,
  };

  // ---------------------------------------------------------------------
  // Utilitários
  // ---------------------------------------------------------------------
  const pad = (n) => String(n).padStart(2, "0");
  const ymd = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`;
  const lastDay = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate(); // m: 1–12
  const addMonths = (y, m, n) => {
    const i = y * 12 + (m - 1) + n;
    return [Math.floor(i / 12), (i % 12) + 1];
  };

  /** Último mês com CHIRPS final provável (~3 semanas de latência). */
  function lastAvailableMonth() {
    const t = new Date();
    const back = t.getDate() >= 20 ? 1 : 2;
    return addMonths(t.getFullYear(), t.getMonth() + 1, -back);
  }

  function setPeriod(start, end) {
    $("start").value = start;
    $("end").value = end;
  }

  function applyPreset(name) {
    const [ly, lm] = lastAvailableMonth();
    const endOf = (y, m) => ymd(y, m, lastDay(y, m));
    if (name === "3m") {
      const [sy, sm] = addMonths(ly, lm, -2);
      setPeriod(ymd(sy, sm, 1), endOf(ly, lm));
    } else if (name === "12m") {
      const [sy, sm] = addMonths(ly, lm, -11);
      setPeriod(ymd(sy, sm, 1), endOf(ly, lm));
    } else if (name === "season") {
      // Época chuvosa: Junho–Outubro
      const y = lm >= 7 ? ly : ly - 1;
      const endM = y === ly ? Math.min(10, lm) : 10;
      setPeriod(ymd(y, 6, 1), endOf(y, endM));
    } else if (name === "cashew") {
      // Floração → colheita do caju: Dezembro–Maio
      const y = lm >= 5 ? ly : ly - 1;
      setPeriod(ymd(y - 1, 12, 1), endOf(y, 5));
    }
  }

  function getCookie(name) {
    const m = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[2]) : "";
  }

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.textContent = msg || "";
    el.classList.toggle("error", isError);
  }

  async function api(url, opts = {}) {
    state.loading++;
    $("loading").hidden = false;
    try {
      const res = await fetch(url, {
        ...opts,
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken"), ...(opts.headers || {}) },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Erro ${res.status}`);
      return data;
    } finally {
      state.loading--;
      if (state.loading <= 0) {
        state.loading = 0;
        $("loading").hidden = true;
      }
    }
  }

  const period = () => ({ start: $("start").value, end: $("end").value });
  const maskValue = () => ($("mask-cropland").checked ? "cropland" : "none");

  function hexToRgb(h) {
    const n = parseInt(h.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function rampColor(palette, t) {
    if (t == null || Number.isNaN(t)) return "#d5dbd8";
    t = Math.max(0, Math.min(1, t));
    const pos = t * (palette.length - 1);
    const i = Math.min(palette.length - 2, Math.floor(pos));
    const f = pos - i;
    const a = hexToRgb(palette[i]);
    const b = hexToRgb(palette[i + 1]);
    return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
  }

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = (v) => typeof v === "number" && !Number.isNaN(v);

  const fmt = (v, d = 1) => (v == null || Number.isNaN(v) ? "—" : Number(v).toLocaleString("pt-PT", { maximumFractionDigits: d, minimumFractionDigits: d }));

  // ---------------------------------------------------------------------
  // Mapa
  // ---------------------------------------------------------------------
  const map = L.map("map", { zoomControl: true, preferCanvas: true }).setView([11.85, -15.2], 8);

  const basemaps = {
    "Claro (CARTO Positron)": L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      attribution: "© OpenStreetMap © CARTO", subdomains: "abcd", maxZoom: 19,
    }),
    "Satélite (Esri)": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      attribution: "Esri, Maxar, Earthstar Geographics", maxZoom: 19,
    }),
    "OpenStreetMap": L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap", maxZoom: 19,
    }),
  };
  basemaps["Claro (CARTO Positron)"].addTo(map);

  map.createPane("ee").style.zIndex = 350;
  map.createPane("bounds").style.zIndex = 420;

  let eeLayer = null;
  const outlineLayer = L.geoJSON(null, {
    pane: "bounds",
    style: { color: "#33413b", weight: 1.2, opacity: 0.8, fill: true, fillOpacity: 0 },
    onEachFeature: (f, lyr) => {
      lyr.bindTooltip(esc(f.properties.name), { permanent: true, direction: "center", className: "region-label" });
      lyr.on("click", () => { if (!state.drawing) selectRegion(f, lyr); });
    },
  }).addTo(map);

  const choroLayer = L.geoJSON(null, { pane: "bounds" });
  const drawn = new L.FeatureGroup().addTo(map);
  const savedLayer = L.geoJSON(null, { pane: "bounds", style: { color: "#b45f06", weight: 2, dashArray: "4 3", fillOpacity: 0.04 } }).addTo(map);

  L.control.layers(basemaps, { "Limites regionais": outlineLayer, "Áreas guardadas": savedLayer }, { position: "topright" }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);

  map.addControl(new L.Control.Draw({
    position: "topleft",
    edit: { featureGroup: drawn, remove: true },
    draw: {
      // showArea: false evita o erro "type is not defined" do leaflet-draw 1.0.4 com Leaflet 1.9
      polygon: { allowIntersection: false, showArea: false, shapeOptions: { color: "#0b6e4f", weight: 2 } },
      rectangle: { showArea: false, shapeOptions: { color: "#0b6e4f", weight: 2 } },
      polyline: false, circle: false, marker: false, circlemarker: false,
    },
  }));

  function setTarget(target, label) {
    state.target = target;
    state.targetLabel = label;
    $("btn-analyse").disabled = !target;
    $("btn-save").disabled = !(target && target.geometry);
    if (target) setStatus(`Seleccionado: ${label}`);
  }

  // Durante o desenho, os contornos das regiões deixam de captar o rato
  // (sem isto só era possível desenhar sobre o mar)
  const setOutlineInteractive = (on) => outlineLayer.eachLayer((l) => {
    l.options.interactive = on;
    if (l._path) l._path.classList.toggle("leaflet-interactive", on);
  });
  map.on(L.Draw.Event.DRAWSTART, () => { state.drawing = true; setOutlineInteractive(false); });
  map.on(L.Draw.Event.DRAWSTOP, () => { setOutlineInteractive(true); setTimeout(() => { state.drawing = false; }, 0); });
  map.on(L.Draw.Event.EDITSTART, () => setOutlineInteractive(false));
  map.on(L.Draw.Event.EDITSTOP, () => setOutlineInteractive(true));

  map.on(L.Draw.Event.CREATED, (e) => {
    drawn.clearLayers();
    drawn.addLayer(e.layer);
    resetOutlineStyle();
    setTarget({ geometry: e.layer.toGeoJSON().geometry }, "Área desenhada");
  });
  map.on(L.Draw.Event.EDITED, () => {
    const lyr = drawn.getLayers()[0];
    if (lyr) setTarget({ geometry: lyr.toGeoJSON().geometry }, "Área desenhada");
  });
  map.on(L.Draw.Event.DELETED, () => {
    if (!drawn.getLayers().length) setTarget(null, "");
  });

  function resetOutlineStyle() {
    outlineLayer.setStyle({ color: "#33413b", weight: 1.2, fillOpacity: 0 });
  }

  function selectRegion(feature, lyr) {
    drawn.clearLayers();
    resetOutlineStyle();
    if (lyr && lyr.setStyle) lyr.setStyle({ color: "#0b6e4f", weight: 3, fillOpacity: 0.08, fillColor: "#0b6e4f" });
    setTarget({ region_id: feature.properties.id }, feature.properties.name);
  }

  // ---------------------------------------------------------------------
  // Camadas do Earth Engine
  // ---------------------------------------------------------------------
  async function loadLayer() {
    const name = $("layer").value;
    const { start, end } = period();
    const q = new URLSearchParams({ start, end, mask: maskValue() });
    setStatus("A gerar camada…");
    try {
      const data = await api(`/api/layers/${name}/?${q}`);
      if (eeLayer) map.removeLayer(eeLayer);
      eeLayer = L.tileLayer(data.url, {
        pane: "ee", opacity: parseFloat($("opacity").value), attribution: "Google Earth Engine",
      });
      if (state.tab !== "timelapse") eeLayer.addTo(map);
      state.mapLegend = [data.legend, `${data.start} → ${data.end}`];
      renderLegend(...state.mapLegend);
      setStatus(`${data.legend.label} · ${data.start} → ${data.end}`);
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  function renderLegend(legend, subtitle) {
    const el = $("legend");
    const units = legend.units ? ` (${legend.units})` : "";
    let body;
    if (legend.classes) {
      body = `<div class="classes">${legend.classes.map((c, i) => `<div><span style="background:${legend.palette[i]}"></span>${c}</div>`).join("")}</div>`;
    } else {
      const mid = (legend.min + legend.max) / 2;
      body = `<div class="bar" style="background:linear-gradient(90deg,${legend.palette.join(",")})"></div>
        <div class="ticks"><span>${legend.min}</span><span>${+mid.toFixed(2)}</span><span>${legend.max}</span></div>`;
    }
    el.innerHTML = `<h3>${legend.label}${units}</h3>${body}${subtitle ? `<div class="period">${subtitle}</div>` : ""}`;
    el.hidden = false;
  }

  $("opacity").addEventListener("input", (e) => {
    const v = parseFloat(e.target.value);
    if (eeLayer) eeLayer.setOpacity(v);
    if (tl.layers[tl.index]) tl.layers[tl.index].setOpacity(v);
  });

  // ---------------------------------------------------------------------
  // Análise de AOI / região
  // ---------------------------------------------------------------------
  let charts = {};

  async function analyse() {
    if (!state.target) return;
    setStatus(`A analisar ${state.targetLabel}…`);
    try {
      const data = await api("/api/analyse/", {
        method: "POST",
        body: JSON.stringify({ ...period(), mask: maskValue(), ...state.target }),
      });
      state.lastResult = data;
      renderResults(data);
      setStatus(`Análise concluída: ${data.target.name}`);
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  function kpi(label, value, sub, cls = "") {
    return `<div class="kpi ${cls}"><div class="k">${label}</div><div class="v">${value}</div><div class="s">${sub || ""}</div></div>`;
  }

  function renderResults(data) {
    const s = data.statistics;
    const ind = s.indicators;
    const sign = (v, neutral = 0) => (!isNum(v) ? "" : v >= neutral ? "pos" : "neg");
    const below = (v, t, cls = "neg") => (!isNum(v) ? "" : v < t ? cls : "pos");
    const spiTxt = (v) => (!isNum(v) ? "sem dado (estação seca)" : v <= -2 ? "extremamente seco" : v <= -1.5 ? "muito seco" : v <= -1 ? "moderadamente seco" : v >= 1 ? "húmido" : "normal");
    const severe = s.severity.filter((c) => c.class >= 3).reduce((a, c) => a + c.pct, 0);
    const anyDrought = s.severity.filter((c) => c.class >= 1).reduce((a, c) => a + c.pct, 0);
    const mask = maskValue() === "cropland" ? " · só cultivo" : "";

    $("results").hidden = false;
    $("res-title").textContent = data.target.name;
    $("res-sub").textContent = `${s.start} → ${s.end} · ${fmt(s.area_km2, 0)} km²${mask}`;

    $("kpis").innerHTML = [
      kpi("Precipitação acumulada", `${fmt(ind.rain.mean, 0)} mm`, `mín ${fmt(ind.rain.min, 0)} · máx ${fmt(ind.rain.max, 0)}`),
      kpi("Anomalia de precipitação", `${ind.rain_anom.mean > 0 ? "+" : ""}${fmt(ind.rain_anom.mean)} %`, "vs climatologia", sign(ind.rain_anom.mean)),
      kpi("SPI-3", fmt(ind.spi3.mean, 2), spiTxt(ind.spi3.mean), isNum(ind.spi3.mean) && ind.spi3.mean <= -1.5 ? "bad" : below(ind.spi3.mean, -1)),
      kpi("NDVI médio", fmt(ind.ndvi.mean, 3), `σ ${fmt(ind.ndvi.stdDev, 3)}`),
      kpi("Anomalia de NDVI", `${ind.ndvi_anom.mean > 0 ? "+" : ""}${fmt(ind.ndvi_anom.mean, 3)}`, "vs referência", sign(ind.ndvi_anom.mean)),
      kpi("VCI", `${fmt(ind.vci.mean, 0)} %`, "condição da vegetação", below(ind.vci.mean, 35)),
      kpi("VHI", `${fmt(ind.vhi.mean, 0)} %`, "saúde da vegetação", isNum(ind.vhi.mean) && ind.vhi.mean < 30 ? "bad" : below(ind.vhi.mean, 40)),
      kpi("Área em seca", `${fmt(anyDrought, 0)} %`, `${fmt(severe, 0)} % severa ou extrema`, severe >= 10 ? "bad" : anyDrought >= 30 ? "neg" : "pos"),
    ].join("");

    drawCharts(data);
  }

  function chartDefaults() {
    Chart.defaults.color = "#5c6b64";
    Chart.defaults.borderColor = "#e6ebe8";
    Chart.defaults.font.family = "Roboto, system-ui, sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
  }

  function drawCharts(data) {
    Object.values(charts).forEach((c) => c.destroy());
    const rain = data.series.rain;
    const ndvi = data.series.ndvi;
    const rb = data.series.baselines.rain;
    const vb = data.series.baselines.vegetation;

    charts.rain = new Chart($("chart-rain"), {
      data: {
        labels: rain.map((r) => r.date),
        datasets: [
          {
            type: "bar", label: "Observado (mm)", data: rain.map((r) => r.rain),
            backgroundColor: rain.map((r) => (r.rain != null && r.climatology != null && r.rain < r.climatology * 0.8 ? "#d9822b" : "#2f7fb5")),
            borderRadius: 3,
          },
          {
            type: "line", label: `Climatologia ${rb[0]}–${rb[1]}`, data: rain.map((r) => r.climatology),
            borderColor: "#1b2621", borderDash: [4, 3], borderWidth: 1.5, pointRadius: 0, tension: 0.3,
          },
        ],
      },
      options: { interaction: { mode: "index", intersect: false }, scales: { y: { beginAtZero: true, title: { display: true, text: "mm" } } } },
    });

    charts.ndvi = new Chart($("chart-ndvi"), {
      type: "line",
      data: {
        labels: ndvi.map((r) => r.date),
        datasets: [
          { label: "NDVI", data: ndvi.map((r) => r.ndvi), borderColor: "#0b6e4f", backgroundColor: "rgba(11,110,79,.10)", borderWidth: 2, fill: "+1", pointRadius: 1.5, tension: 0.25 },
          { label: `Referência ${vb[0]}–${vb[1]}`, data: ndvi.map((r) => r.baseline), borderColor: "#1b2621", borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0, tension: 0.25 },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: { y: { suggestedMin: 0.1, suggestedMax: 0.8 }, x: { ticks: { maxTicksLimit: 10 } } },
      },
    });

    const sev = data.statistics.severity;
    charts.sev = new Chart($("chart-sev"), {
      type: "bar",
      data: {
        labels: sev.map((c) => c.label),
        datasets: [{ data: sev.map((c) => c.pct), backgroundColor: LAYER_META.severity.palette, borderColor: "#c4cdc9", borderWidth: 1, borderRadius: 3 }],
      },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.raw} % da área` } } },
        scales: { x: { beginAtZero: true, max: 100, title: { display: true, text: "%" } } },
      },
    });
  }

  function exportCsv() {
    const d = state.lastResult;
    if (!d) return;
    const lines = [["serie", "data", "valor", "referencia"].join(",")];
    d.series.rain.forEach((r) => lines.push(["precipitacao_mm", r.date, r.rain ?? "", r.climatology ?? ""].join(",")));
    d.series.ndvi.forEach((r) => lines.push(["ndvi", r.date, r.ndvi ?? "", r.baseline ?? ""].join(",")));
    Object.entries(d.statistics.indicators).forEach(([k, v]) => lines.push([`media_periodo_${k}`, d.statistics.start, v.mean ?? "", ""].join(",")));
    d.statistics.severity.forEach((c) => lines.push([`seca_pct_${c.label}`, d.statistics.start, c.pct, ""].join(",")));
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `agroclima_${d.target.name.replace(/\W+/g, "_")}_${d.statistics.start}_${d.statistics.end}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // ---------------------------------------------------------------------
  // Áreas guardadas
  // ---------------------------------------------------------------------
  let savedAois = [];

  async function loadSavedAois() {
    try {
      const fc = await api("/api/aois/");
      savedAois = fc.features;
      savedLayer.clearLayers().addData(fc);
      $("saved-aois").innerHTML = '<option value="">— Áreas guardadas —</option>' +
        savedAois.map((f) => `<option value="${f.id}">${esc(f.properties.name)} (${fmt(f.properties.area_km2, 0)} km²)</option>`).join("");
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  async function saveAoi() {
    const name = $("aoi-name").value.trim();
    if (!name) { setStatus("Dê um nome à área antes de guardar.", true); return; }
    try {
      await api("/api/aois/", { method: "POST", body: JSON.stringify({ name, geometry: state.target.geometry }) });
      $("aoi-name").value = "";
      setStatus(`Área "${name}" guardada.`);
      loadSavedAois();
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  $("saved-aois").addEventListener("change", (e) => {
    const f = savedAois.find((a) => String(a.id) === e.target.value);
    if (!f) return;
    drawn.clearLayers();
    resetOutlineStyle();
    map.fitBounds(L.geoJSON(f).getBounds(), { padding: [30, 30] });
    setTarget({ aoi_id: f.id }, f.properties.name);
  });

  // ---------------------------------------------------------------------
  // Timelapse
  // ---------------------------------------------------------------------
  const tl = { frames: [], layers: [], index: 0, timer: null };

  function tlClear() {
    tlStop();
    tl.layers.forEach((l) => l && map.removeLayer(l));
    tl.frames = []; tl.layers = []; tl.index = 0;
  }

  function tlEnsure(i) {
    if (!tl.layers[i]) {
      tl.layers[i] = L.tileLayer(tl.frames[i].url, { pane: "ee", opacity: 0, attribution: "Google Earth Engine" }).addTo(map);
    }
    return tl.layers[i];
  }

  function tlShow(i) {
    if (!tl.frames.length) return;
    tl.index = (i + tl.frames.length) % tl.frames.length;
    const op = parseFloat($("opacity").value);
    tlEnsure(tl.index).setOpacity(op);
    tl.layers.forEach((l, k) => { if (l && k !== tl.index) l.setOpacity(0); });
    tlEnsure((tl.index + 1) % tl.frames.length); // pré-carrega o fotograma seguinte
    $("tl-date").textContent = tl.frames[tl.index].date;
    $("tl-slider").value = tl.index;
    $("tl-progress").textContent = `Fotograma ${tl.index + 1} de ${tl.frames.length}`;
  }

  function tlStop() {
    clearInterval(tl.timer);
    tl.timer = null;
    $("tl-play").textContent = "▶";
  }

  function tlPlay() {
    if (tl.timer) { tlStop(); return; }
    $("tl-play").textContent = "⏸";
    tl.timer = setInterval(() => tlShow(tl.index + 1), parseInt($("tl-speed").value, 10));
  }

  async function tlLoad() {
    const { start, end } = period();
    const q = new URLSearchParams({ start, end, var: $("tl-var").value });
    setStatus("A preparar fotogramas do timelapse…");
    try {
      const data = await api(`/api/timelapse/?${q}`);
      tlClear();
      tl.frames = data.frames;
      ["tl-slider", "tl-prev", "tl-play", "tl-next"].forEach((id) => ($(id).disabled = !tl.frames.length));
      $("tl-slider").max = Math.max(0, tl.frames.length - 1);
      $("tl-gif").href = data.gif_url;
      $("tl-gif").classList.toggle("hidden", !data.gif_url);
      renderLegend({ ...data.legend, units: "" }, `${tl.frames[0]?.date} → ${tl.frames.at(-1)?.date}`);
      tlShow(0);
      setStatus(`${data.label}: ${tl.frames.length} fotogramas`);
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  $("btn-tl-load").addEventListener("click", tlLoad);
  $("tl-play").addEventListener("click", tlPlay);
  $("tl-prev").addEventListener("click", () => { tlStop(); tlShow(tl.index - 1); });
  $("tl-next").addEventListener("click", () => { tlStop(); tlShow(tl.index + 1); });
  $("tl-slider").addEventListener("input", (e) => { tlStop(); tlShow(parseInt(e.target.value, 10)); });
  $("tl-speed").addEventListener("change", () => { if (tl.timer) { tlStop(); tlPlay(); } });

  // ---------------------------------------------------------------------
  // Regiões (coroplético a partir do PostGIS)
  // ---------------------------------------------------------------------
  async function loadOutlines() {
    try {
      const fc = await api("/api/regions/?level=1");
      outlineLayer.clearLayers().addData(fc);
      if (fc.features.length) map.fitBounds(outlineLayer.getBounds(), { padding: [10, 10] });
      else setStatus("Sem limites carregados — execute `manage.py load_regions`.", true);
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  async function loadChoropleth(keepPeriod = true) {
    const indicator = $("reg-indicator").value;
    const level = $("reg-level").value;
    const q = new URLSearchParams({ level, indicator });
    if (keepPeriod && $("reg-period").value) q.set("period", $("reg-period").value);
    try {
      const fc = await api(`/api/regions/?${q}`);
      const sel = $("reg-period");
      const current = fc.period;
      sel.innerHTML = fc.available_periods.slice().reverse().map((p) => `<option value="${p}"${p === current ? " selected" : ""}>${p}</option>`).join("")
        || '<option value="">Sem dados — execute compute_stats</option>';

      const sc = CHORO_SCALE[indicator];
      const color = (v) => rampColor(sc.palette, v == null ? null : (v - sc.min) / (sc.max - sc.min));
      choroLayer.clearLayers();
      choroLayer.options.style = (f) => ({ color: "#ffffff", weight: 1.5, fillColor: color(f.properties.value), fillOpacity: f.properties.value == null ? 0.25 : 0.85 });
      choroLayer.options.onEachFeature = (f, lyr) => {
        lyr.bindTooltip(`<b>${esc(f.properties.name)}</b><br>${fmt(f.properties.value, indicator.startsWith("ndvi") ? 3 : 1)} ${sc.units}`, { sticky: true });
        lyr.on("click", () => { setTarget({ region_id: f.properties.id }, f.properties.name); analyse(); });
      };
      choroLayer.addData(fc);

      const label = $("reg-indicator").selectedOptions[0].textContent;
      renderLegend({ label, palette: sc.palette, min: sc.min, max: sc.max, units: "" }, current ? `Média mensal · ${current}` : "");

      const ranked = fc.features.slice().sort((a, b) => (a.properties.value ?? Infinity) - (b.properties.value ?? Infinity));
      $("ranking").innerHTML = ranked.map((f) => `<li data-id="${f.id}"><span class="sw" style="background:${color(f.properties.value)}"></span>${esc(f.properties.name)}<span class="v">${fmt(f.properties.value, indicator.startsWith("ndvi") ? 3 : 1)}</span></li>`).join("");
      $("ranking").querySelectorAll("li").forEach((li) => li.addEventListener("click", () => {
        const f = fc.features.find((x) => String(x.id) === li.dataset.id);
        map.fitBounds(L.geoJSON(f).getBounds(), { padding: [40, 40] });
      }));
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  $("reg-indicator").addEventListener("change", () => loadChoropleth(false));
  $("reg-level").addEventListener("change", () => loadChoropleth(false));
  $("reg-period").addEventListener("change", () => loadChoropleth(true));

  // ---------------------------------------------------------------------
  // Separadores
  // ---------------------------------------------------------------------
  function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== tab));

    if (tab !== "timelapse") tlStop();
    tl.layers.forEach((l, k) => l && l.setOpacity(tab === "timelapse" && k === tl.index ? parseFloat($("opacity").value) : 0));
    if (eeLayer) (tab === "mapa" ? eeLayer.addTo(map) : map.removeLayer(eeLayer));

    if (tab === "regioes") {
      map.removeLayer(outlineLayer);
      choroLayer.addTo(map);
      loadChoropleth(true);
    } else {
      map.removeLayer(choroLayer);
      outlineLayer.addTo(map);
      if (tab === "mapa" && state.mapLegend) renderLegend(...state.mapLegend);
    }
  }

  function loadLegendForCurrent() {
    const meta = LAYER_META[$("layer").value];
    if (meta && meta.min != null) renderLegend(meta, "");
  }

  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => applyPreset(c.dataset.preset)));

  $("btn-layer").addEventListener("click", loadLayer);
  $("btn-analyse").addEventListener("click", analyse);
  $("btn-save").addEventListener("click", saveAoi);
  $("btn-csv").addEventListener("click", exportCsv);
  $("btn-close-results").addEventListener("click", () => { $("results").hidden = true; setTimeout(() => map.invalidateSize(), 50); });
  $("btn-clear").addEventListener("click", () => {
    drawn.clearLayers();
    resetOutlineStyle();
    $("saved-aois").value = "";
    setTarget(null, "");
    setStatus("");
  });

  // Ajusta o mapa quando o painel de resultados abre/fecha
  new MutationObserver(() => setTimeout(() => map.invalidateSize(), 50)).observe($("results"), { attributes: true, attributeFilter: ["hidden"] });

  // ---------------------------------------------------------------------
  // Arranque
  // ---------------------------------------------------------------------
  chartDefaults();
  applyPreset("season");
  loadOutlines();
  loadSavedAois();
  loadLayer();
})();
