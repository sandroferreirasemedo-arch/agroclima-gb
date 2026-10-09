"""
Serviço Google Earth Engine do AgroClima Guiné-Bissau.

Indicadores
-----------
rain        Precipitação acumulada no período (CHIRPS diário, mm)
rain_anom   Anomalia da precipitação face à climatologia do mesmo período (%)
spi1/3/6    Standardized Precipitation Index (McKee et al., 1993) para 1, 3 e 6 meses que
            terminam no fim do período: ajuste gama por píxel (estimador de Thom) aos totais
            CHIRPS 1991–2020, com probabilidade de zeros, e transformação para a normal padrão
ndvi        NDVI médio (MODIS MOD13Q1, 250 m, QA bom/marginal)
ndvi_anom   NDVI actual − NDVI médio de referência para as mesmas datas
vci         Vegetation Condition Index (Kogan) — 0 a 100
lst         Temperatura da superfície diurna (MODIS MOD11A2, °C)
tci         Temperature Condition Index — 0 a 100
vhi         Vegetation Health Index = 0,5·VCI + 0,5·TCI
severity    Classes de severidade da seca a partir do VHI (0 sem seca … 4 extrema)

Todas as funções recebem datas Python (`datetime.date`) com fim EXCLUSIVO.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor

import ee
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

CHIRPS = "UCSB-CHG/CHIRPS/DAILY"
MOD13Q1 = "MODIS/061/MOD13Q1"
MOD11A2 = "MODIS/061/MOD11A2"
WORLDCOVER = "ESA/WorldCover/v200"


class GEENotConfigured(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------
_initialized = False


def init():
    global _initialized
    if _initialized:
        return
    key_path = settings.GEE_SERVICE_ACCOUNT_KEY
    project = settings.GEE_PROJECT
    if key_path and not os.path.exists(key_path):
        raise GEENotConfigured(
            f"Chave do Earth Engine não encontrada em {key_path}. No Render, crie o Secret File 'gee-key.json'; "
            "em Docker, coloque-a em secrets/gee-key.json."
        )
    if not key_path and not project:
        raise GEENotConfigured(
            "Google Earth Engine não configurado: defina GEE_PROJECT e GEE_SERVICE_ACCOUNT_KEY."
        )
    try:
        if key_path:
            with open(key_path) as fh:
                info = json.load(fh)
            credentials = ee.ServiceAccountCredentials(info["client_email"], key_path)
            ee.Initialize(credentials, project=project or info.get("project_id"))
        else:
            # Credenciais de `earthengine authenticate` (desenvolvimento local)
            ee.Initialize(project=project)
    except (ValueError, KeyError) as exc:
        raise GEENotConfigured(f"Chave do Earth Engine inválida ({exc}). Use o ficheiro JSON da conta de serviço.")
    except ee.EEException:
        raise
    except Exception as exc:  # erros de autenticação do google-auth, rede, etc.
        raise GEENotConfigured(f"Falha ao autenticar no Earth Engine: {exc}")
    _initialized = True
    log.info("Earth Engine inicializado (projecto %s)", project or "da conta de serviço")


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def _shift_year(d: dt.date, years: int) -> dt.date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 de Fevereiro
        return d.replace(year=d.year + years, day=28)


def _iso(d: dt.date) -> str:
    return d.isoformat()


def add_months(d: dt.date, n: int) -> dt.date:
    """Primeiro dia do mês deslocado n meses."""
    idx = d.year * 12 + (d.month - 1) + n
    return dt.date(idx // 12, idx % 12 + 1, 1)


def months_between(start: dt.date, end: dt.date) -> list[dt.date]:
    """Primeiros dias de cada mês que intersecta [start, end)."""
    cur = start.replace(day=1)
    out = []
    while cur < end:
        out.append(cur)
        cur = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def cache_key(*parts) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    return "gee:" + hashlib.sha1(raw.encode()).hexdigest()


def cached(key_parts, fn, timeout=60 * 60 * 6):
    key = cache_key(*key_parts)
    hit = cache.get(key)
    if hit is not None:
        return hit
    value = fn()
    cache.set(key, value, timeout)
    return value


def to_ee_geometry(geojson: dict | str) -> ee.Geometry:
    if isinstance(geojson, str):
        geojson = json.loads(geojson)
    if geojson.get("type") == "Feature":
        geojson = geojson["geometry"]
    return ee.Geometry(geojson, None, False)


def country_geometry() -> ee.Geometry:
    """Fronteira nacional: usa PostGIS se já carregada, senão o asset administrativo do GEE."""
    from .models import Region  # import tardio para evitar ciclos

    country = Region.objects.filter(level=Region.LEVEL_COUNTRY).first()
    if country:
        return to_ee_geometry(country.geom.simplify(0.002, preserve_topology=True).geojson)
    fc = ee.FeatureCollection(settings.GEE_ADMIN_L1_ASSET).filter(
        ee.Filter.eq(settings.GEE_COUNTRY_FIELD, settings.GEE_COUNTRY_NAME)
    )
    return fc.geometry().simplify(250)


# ---------------------------------------------------------------------------
# Colecções de base
# ---------------------------------------------------------------------------
def chirps() -> ee.ImageCollection:
    return ee.ImageCollection(CHIRPS).select("precipitation")


def ndvi_col() -> ee.ImageCollection:
    def prep(img):
        qa_ok = img.select("SummaryQA").lte(1)  # 0 = bom, 1 = marginal
        return (
            img.select("NDVI").multiply(0.0001).updateMask(qa_ok)
            .rename("NDVI")
            .set("system:time_start", img.get("system:time_start"))
        )

    return ee.ImageCollection(MOD13Q1).map(prep)


def lst_col() -> ee.ImageCollection:
    def prep(img):
        return (
            img.select("LST_Day_1km").multiply(0.02).subtract(273.15)
            .rename("LST")
            .set("system:time_start", img.get("system:time_start"))
        )

    return ee.ImageCollection(MOD11A2).map(prep)


def _empty(band: str) -> ee.Image:
    return ee.Image.constant(0).rename(band).updateMask(0).toFloat()


def safe_reduce(col: ee.ImageCollection, band: str, how: str = "mean") -> ee.Image:
    """sum/mean/min/max de uma colecção que pode estar vazia (ex.: mês ainda sem dados CHIRPS/MODIS).

    Sem isto o Earth Engine devolve uma imagem sem bandas e o pedido falha com
    "Image has no bands" / "Dictionary.get: key not found".
    """
    reduced = getattr(col, how)().rename(band)
    return ee.Image(ee.Algorithms.If(col.size().gt(0), reduced, _empty(band)))


def cropland_mask() -> ee.Image:
    """Máscara de cultivo (ESA WorldCover 2021, classe 40). Nota: cajueiros tendem a aparecer como árvores."""
    return ee.ImageCollection(WORLDCOVER).first().select("Map").eq(40)


def _per_year(col: ee.ImageCollection, start: dt.date, end: dt.date, years, how="mean",
              band: str = "v") -> ee.ImageCollection:
    """Composições do mesmo intervalo de calendário para cada ano de referência."""
    imgs = []
    for y in range(years[0], years[1] + 1):
        off = y - start.year
        sub = col.filterDate(_iso(_shift_year(start, off)), _iso(_shift_year(end, off)))
        imgs.append(safe_reduce(sub, band, "sum" if how == "sum" else "mean"))
    return ee.ImageCollection(imgs)


# ---------------------------------------------------------------------------
# Indicadores
# ---------------------------------------------------------------------------
def rain_total(start, end) -> ee.Image:
    return safe_reduce(chirps().filterDate(_iso(start), _iso(end)), "rain", "sum")


def rain_anomaly_pct(start, end) -> ee.Image:
    clim = _per_year(chirps(), start, end, settings.RAIN_BASELINE, "sum").mean()
    cur = rain_total(start, end)
    return cur.subtract(clim).divide(clim).multiply(100).updateMask(clim.gt(1)).rename("rain_anom")


def spi3_approx(start, end) -> ee.Image:  # mantido para comparação; a camada usa spi_gamma
    """Anomalia padronizada da precipitação acumulada nos 3 meses que terminam em `end`."""
    last = (end - dt.timedelta(days=1)).replace(day=1)  # mês que contém o último dia do período
    s3 = add_months(last, -2)
    e3 = add_months(last, 1)
    stack = _per_year(chirps(), s3, e3, settings.RAIN_BASELINE, "sum")
    mean = stack.mean()
    std = stack.reduce(ee.Reducer.stdDev())
    cur = safe_reduce(chirps().filterDate(_iso(s3), _iso(e3)), "p", "sum")
    return cur.subtract(mean).divide(std).updateMask(std.gt(0.5)).rename("spi3")


# --- SPI com ajuste gama ----------------------------------------------------
_GAMMAINC_X_FIRST = None


def _gammainc_x_first() -> bool:
    """Confirma a ordem dos argumentos de gammainc no servidor (a documentação é ambígua).

    P(a=2, x=1) = 0,2642 e P(a=1, x=2) = 0,8647: um único pedido distingue as convenções.
    """
    global _GAMMAINC_X_FIRST
    if _GAMMAINC_X_FIRST is None:
        hit = cache.get("gee:gammainc_x_first")
        if hit is None:
            v = ee.Number(1).gammainc(2).getInfo()
            if abs(v - (1 - 2 * math.exp(-1))) < 1e-3:
                hit = True    # gammainc(x, a)
            elif abs(v - (1 - math.exp(-2))) < 1e-3:
                hit = False   # gammainc(a, x)
            else:
                raise ee.EEException(f"Resultado inesperado de gammainc: {v}")
            cache.set("gee:gammainc_x_first", hit, None)
        _GAMMAINC_X_FIRST = hit
    return _GAMMAINC_X_FIRST


def gamma_cdf(alpha: ee.Image, x_scaled: ee.Image) -> ee.Image:
    """Função de distribuição gama regularizada P(alpha, x/beta)."""
    return x_scaled.gammainc(alpha) if _gammainc_x_first() else alpha.gammainc(x_scaled)


def spi_gamma(start, end, scale: int = 3) -> ee.Image:
    """SPI de `scale` meses que termina no último mês do período."""
    last = (end - dt.timedelta(days=1)).replace(day=1)
    s, e = add_months(last, -(scale - 1)), add_months(last, 1)
    stack = _per_year(chirps(), s, e, settings.RAIN_BASELINE, "sum", band="p")

    q = stack.map(lambda i: i.eq(0).rename("p")).mean()                    # probabilidade de zero
    pos = stack.map(lambda i: i.updateMask(i.gt(0)))
    mean_pos = pos.mean()
    mean_log = pos.map(lambda i: i.log()).mean()
    a = mean_pos.log().subtract(mean_log).max(1e-6)                       # A = ln(x̄) − mean(ln x)
    alpha = a.multiply(4.0 / 3.0).add(1).sqrt().add(1).divide(a.multiply(4))  # Thom (1958)
    beta = mean_pos.divide(alpha)

    x = safe_reduce(chirps().filterDate(_iso(s), _iso(e)), "p", "sum")
    g = gamma_cdf(alpha, x.divide(beta))
    h = q.add(ee.Image(1).subtract(q).multiply(g)).clamp(0.00135, 0.99865)  # limita a ±3
    spi = h.multiply(2).subtract(1).erfInv().multiply(math.sqrt(2))

    # SPI não é fiável quando a janela é quase sempre seca (estação seca na Guiné-Bissau)
    valid = q.lte(0.3).And(stack.mean().gt(10 * scale))
    return spi.updateMask(valid).updateMask(x.mask()).rename(f"spi{scale}")


def ndvi_mean(start, end) -> ee.Image:
    return safe_reduce(ndvi_col().filterDate(_iso(start), _iso(end)), "ndvi")


def ndvi_anomaly(start, end) -> ee.Image:
    base = _per_year(ndvi_col(), start, end, settings.VEG_BASELINE).mean()
    return ndvi_mean(start, end).subtract(base).rename("ndvi_anom")


def vci(start, end) -> ee.Image:
    stack = _per_year(ndvi_col(), start, end, settings.VEG_BASELINE)
    mn, mx = stack.min(), stack.max()
    cur = ndvi_mean(start, end)
    return cur.subtract(mn).divide(mx.subtract(mn)).multiply(100).clamp(0, 100).rename("vci")


def lst_mean(start, end) -> ee.Image:
    return safe_reduce(lst_col().filterDate(_iso(start), _iso(end)), "lst")


def tci(start, end) -> ee.Image:
    stack = _per_year(lst_col(), start, end, settings.VEG_BASELINE)
    mn, mx = stack.min(), stack.max()
    cur = lst_mean(start, end)
    return mx.subtract(cur).divide(mx.subtract(mn)).multiply(100).clamp(0, 100).rename("tci")


def vhi(start, end) -> ee.Image:
    # VCI (250 m) e TCI (1 km) combinam-se à escala de saída pedida pelo GEE
    return vci(start, end).multiply(0.5).add(tci(start, end).multiply(0.5)).rename("vhi")


def drought_severity(vhi_img: ee.Image) -> ee.Image:
    """Classes de Kogan: ≥40 sem seca (0), <40 ligeira (1), <30 moderada (2), <20 severa (3), <10 extrema (4)."""
    sev = (
        ee.Image(0)
        .where(vhi_img.lt(40), 1)
        .where(vhi_img.lt(30), 2)
        .where(vhi_img.lt(20), 3)
        .where(vhi_img.lt(10), 4)
    )
    return sev.updateMask(vhi_img.mask()).rename("severity").toByte()


# ---------------------------------------------------------------------------
# Catálogo de camadas
# ---------------------------------------------------------------------------
P_RAIN = ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"]
P_DIV_RAIN = ["#8c510a", "#d8b365", "#f6e8c3", "#f5f5f5", "#c7eae5", "#5ab4ac", "#01665e"]
P_NDVI = ["#a50026", "#f46d43", "#fee08b", "#d9ef8b", "#66bd63", "#006837"]
P_DIV_VEG = ["#8e0152", "#de77ae", "#fde0ef", "#f7f7f7", "#e6f5d0", "#7fbc41", "#276419"]
P_COND = ["#7f0000", "#d7301f", "#fc8d59", "#fdd49e", "#d9f0a3", "#41ab5d", "#005a32"]
P_LST = ["#313695", "#74add1", "#ffffbf", "#f46d43", "#a50026"]
P_SEV = ["#e5f5e0", "#ffffb2", "#fecc5c", "#fd8d3c", "#bd0026"]

SEVERITY_CLASSES = ["Sem seca", "Ligeira", "Moderada", "Severa", "Extrema"]

LAYERS = {
    "rain": {"group": "Precipitação", "label": "Precipitação acumulada", "units": "mm", "palette": P_RAIN},
    "rain_anom": {"group": "Precipitação", "label": "Anomalia de precipitação", "units": "%", "palette": P_DIV_RAIN,
                  "min": -60, "max": 60},
    "spi1": {"group": "Precipitação", "label": "SPI-1 (gama)", "units": "", "palette": P_DIV_RAIN,
             "min": -2, "max": 2},
    "spi3": {"group": "Precipitação", "label": "SPI-3 (gama)", "units": "", "palette": P_DIV_RAIN,
             "min": -2, "max": 2},
    "spi6": {"group": "Precipitação", "label": "SPI-6 (gama)", "units": "", "palette": P_DIV_RAIN,
             "min": -2, "max": 2},
    "ndvi": {"group": "Vegetação", "label": "NDVI", "units": "", "palette": P_NDVI, "min": 0, "max": 0.9},
    "ndvi_anom": {"group": "Vegetação", "label": "Anomalia de NDVI", "units": "", "palette": P_DIV_VEG,
                  "min": -0.2, "max": 0.2},
    "vci": {"group": "Vegetação", "label": "VCI — Condição da vegetação", "units": "%", "palette": P_COND,
            "min": 0, "max": 100},
    "lst": {"group": "Temperatura e seca", "label": "Temperatura da superfície (dia)", "units": "°C",
            "palette": P_LST, "min": 20, "max": 50},
    "tci": {"group": "Temperatura e seca", "label": "TCI — Condição térmica", "units": "%", "palette": P_COND,
            "min": 0, "max": 100},
    "vhi": {"group": "Temperatura e seca", "label": "VHI — Saúde da vegetação", "units": "%", "palette": P_COND,
            "min": 0, "max": 100},
    "severity": {"group": "Temperatura e seca", "label": "Severidade da seca (VHI)", "units": "", "palette": P_SEV,
                 "min": 0, "max": 4, "classes": SEVERITY_CLASSES},
}

BUILDERS = {
    "rain": rain_total,
    "rain_anom": rain_anomaly_pct,
    "spi1": lambda s, e: spi_gamma(s, e, 1),
    "spi3": lambda s, e: spi_gamma(s, e, 3),
    "spi6": lambda s, e: spi_gamma(s, e, 6),
    "ndvi": ndvi_mean,
    "ndvi_anom": ndvi_anomaly,
    "vci": vci,
    "lst": lst_mean,
    "tci": tci,
    "vhi": vhi,
    "severity": lambda s, e: drought_severity(vhi(s, e)),
}


def layer_catalog():
    return [{"id": k, **{kk: vv for kk, vv in v.items()}} for k, v in LAYERS.items()]


def _vis_for(name, start, end):
    meta = LAYERS[name]
    vis = {"palette": meta["palette"]}
    if name == "rain":
        months = max(1, round((end - start).days / 30.4))
        vis.update(min=0, max=min(3000, max(100, 380 * months)))  # pico mensal ~400 mm em Agosto
    else:
        vis.update(min=meta["min"], max=meta["max"])
    return vis


def build_layer(name, start, end, mask="none") -> tuple[ee.Image, dict]:
    if name not in BUILDERS:
        raise ValueError(f"Camada desconhecida: {name}")
    img = BUILDERS[name](start, end)
    if mask == "cropland":
        img = img.updateMask(cropland_mask())
    return img.clip(country_geometry()), _vis_for(name, start, end)


def tile_url(img: ee.Image, vis: dict) -> str:
    return img.getMapId(vis)["tile_fetcher"].url_format


def get_layer(name, start, end, mask="none") -> dict:
    def compute():
        init()
        img, vis = build_layer(name, start, end, mask)
        return {
            "layer": name,
            "url": tile_url(img, vis),
            "legend": {**LAYERS[name], "min": vis["min"], "max": vis["max"]},
            "start": _iso(start),
            "end": _iso(end - dt.timedelta(days=1)),
        }

    # Mapids do GEE expiram após algumas horas → cache de 2 h
    return cached(("layer", name, start, end, mask), compute, timeout=60 * 60 * 2)


# ---------------------------------------------------------------------------
# Estatísticas de AOI / região
# ---------------------------------------------------------------------------
STAT_BANDS = ["rain", "rain_anom", "spi3", "ndvi", "ndvi_anom", "vci", "vhi"]


def indicator_stack(start, end) -> ee.Image:
    v = vhi(start, end)
    return ee.Image.cat([
        rain_total(start, end),
        rain_anomaly_pct(start, end),
        spi_gamma(start, end, 3),
        ndvi_mean(start, end),
        ndvi_anomaly(start, end),
        vci(start, end),
        v,
    ])


def aoi_statistics(geojson, start, end, mask="none") -> dict:
    def compute():
        init()
        geom = to_ee_geometry(geojson)
        stack = indicator_stack(start, end)
        sev = drought_severity(stack.select("vhi"))
        if mask == "cropland":
            cm = cropland_mask()
            stack, sev = stack.updateMask(cm), sev.updateMask(cm)
        reducer = (
            ee.Reducer.mean()
            .combine(ee.Reducer.minMax(), sharedInputs=True)
            .combine(ee.Reducer.stdDev(), sharedInputs=True)
        )
        stats = stack.reduceRegion(reducer, geom, 1000, bestEffort=True, maxPixels=1e10)
        hist = sev.reduceRegion(ee.Reducer.frequencyHistogram(), geom, 1000, bestEffort=True, maxPixels=1e10)
        result = ee.Dictionary({
            "stats": stats,
            "severity": hist.get("severity"),
            "area_km2": geom.area(10).divide(1e6),
        }).getInfo()

        raw = result.get("stats") or {}
        out = {}
        for b in STAT_BANDS:
            out[b] = {k: raw.get(f"{b}_{k}") for k in ("mean", "min", "max", "stdDev")}
        counts = {int(float(k)): v for k, v in (result.get("severity") or {}).items()}
        total = sum(counts.values()) or 1
        severity = [
            {"class": i, "label": SEVERITY_CLASSES[i], "pct": round(100 * counts.get(i, 0) / total, 1)}
            for i in range(5)
        ]
        return {
            "start": _iso(start),
            "end": _iso(end - dt.timedelta(days=1)),
            "area_km2": result.get("area_km2"),
            "indicators": out,
            "severity": severity,
        }

    return cached(("aoi", geojson, start, end, mask), compute)


# ---------------------------------------------------------------------------
# Séries temporais
# ---------------------------------------------------------------------------
SERIES_MAX_MONTHS = 60


def time_series(geojson, start, end) -> dict:
    def compute():
        init()
        geom = to_ee_geometry(geojson)
        rb0, rb1 = settings.RAIN_BASELINE
        vb0, vb1 = settings.VEG_BASELINE
        n_rain_years = rb1 - rb0 + 1

        # Janela das séries: no máximo os últimos 60 meses do período (custo do Earth Engine)
        months_all = months_between(start, end)[-SERIES_MAX_MONTHS:]
        months = [m.isoformat() for m in months_all]
        ndvi_start = months_all[0] if months_all else start

        def monthly(ms):
            s = ee.Date(ms)
            total = safe_reduce(chirps().filterDate(s, s.advance(1, "month")), "precipitation", "sum")
            val = total.reduceRegion(ee.Reducer.mean(), geom, 5566, bestEffort=True).get("precipitation")
            return ee.Feature(None, {"date": s.format("YYYY-MM"), "rain": val})

        def clim(m):
            m = ee.Number(m)
            col = chirps().filter(ee.Filter.calendarRange(rb0, rb1, "year")).filter(
                ee.Filter.calendarRange(m, m, "month"))
            val = col.sum().divide(n_rain_years).reduceRegion(
                ee.Reducer.mean(), geom, 5566, bestEffort=True).get("precipitation")
            return ee.Feature(None, {"month": m, "rain": val})

        def ndvi_feat(img):
            img = ee.Image(img)
            doy = img.date().getRelative("day", "year").add(1)
            base = (
                ndvi_col()
                .filter(ee.Filter.calendarRange(vb0, vb1, "year"))
                .filter(ee.Filter.calendarRange(doy, doy, "day_of_year"))
                .mean()
            )
            val = img.reduceRegion(ee.Reducer.mean(), geom, 500, bestEffort=True).get("NDVI")
            bval = base.reduceRegion(ee.Reducer.mean(), geom, 500, bestEffort=True).get("NDVI")
            return ee.Feature(None, {"date": img.date().format("YYYY-MM-dd"), "ndvi": val, "baseline": bval})

        payload = ee.Dictionary({
            "rain": ee.FeatureCollection(ee.List(months).map(monthly)),
            "clim": ee.FeatureCollection(ee.List.sequence(1, 12).map(clim)),
            "ndvi": ndvi_col().filterDate(_iso(ndvi_start), _iso(end)).map(ndvi_feat),
        }).getInfo()

        def props(fc):
            return [f["properties"] for f in fc["features"]]

        clim_by_month = {int(p["month"]): p["rain"] for p in props(payload["clim"])}
        rain = []
        for p in props(payload["rain"]):
            m = int(p["date"][5:7])
            rain.append({"date": p["date"], "rain": p.get("rain"), "climatology": clim_by_month.get(m)})
        ndvi = [p for p in props(payload["ndvi"]) if p.get("ndvi") is not None]
        return {
            "rain": rain,
            "ndvi": ndvi,
            "baselines": {"rain": [rb0, rb1], "vegetation": [vb0, vb1]},
        }

    return cached(("ts", geojson, start, end), compute)


# ---------------------------------------------------------------------------
# Timelapse
# ---------------------------------------------------------------------------
TIMELAPSE_VARS = {
    "ndvi": {"label": "NDVI mensal", "vis": {"min": 0, "max": 0.9, "palette": P_NDVI}},
    "rain": {"label": "Precipitação mensal (mm)", "vis": {"min": 0, "max": 600, "palette": P_RAIN}},
    "ndvi_anom": {"label": "Anomalia de NDVI mensal", "vis": {"min": -0.2, "max": 0.2, "palette": P_DIV_VEG}},
}


def _monthly_image(var, m0: dt.date, m1: dt.date) -> ee.Image:
    if var == "ndvi":
        return ndvi_mean(m0, m1)
    if var == "rain":
        return rain_total(m0, m1)
    if var == "ndvi_anom":
        return ndvi_anomaly(m0, m1)
    raise ValueError(f"Variável de timelapse desconhecida: {var}")


def timelapse(var, start, end) -> dict:
    if var not in TIMELAPSE_VARS:
        raise ValueError(f"Variável de timelapse desconhecida: {var}")
    months = months_between(start, end)[-settings.MAX_TIMELAPSE_FRAMES:]

    def compute():
        init()
        region = country_geometry()
        vis = TIMELAPSE_VARS[var]["vis"]

        def frame(m0):
            m1 = (m0.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            img = _monthly_image(var, m0, m1).clip(region)
            return {"date": m0.strftime("%Y-%m"), "url": tile_url(img, vis)}

        with ThreadPoolExecutor(max_workers=6) as pool:
            frames = list(pool.map(frame, months))

        # GIF para descarregar / partilhar
        vis_col = ee.ImageCollection([
            _monthly_image(var, m, (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1))
            .visualize(**vis).clip(region)
            for m in months
        ])
        gif = vis_col.getVideoThumbURL({
            "dimensions": 720,
            "region": region.bounds(),
            "framesPerSecond": 2,
            "crs": "EPSG:3857",
        })
        return {
            "variable": var,
            "label": TIMELAPSE_VARS[var]["label"],
            "legend": {**vis, "label": TIMELAPSE_VARS[var]["label"]},
            "frames": frames,
            "gif_url": gif,
        }

    return cached(("tl", var, months[0] if months else None, months[-1] if months else None), compute,
                  timeout=60 * 60 * 2)


# ---------------------------------------------------------------------------
# Estatísticas zonais mensais (para o comando compute_stats)
# ---------------------------------------------------------------------------
def regions_collection(regions) -> ee.FeatureCollection:
    """Regiões do PostGIS → FeatureCollection do Earth Engine (propriedade region_id)."""
    return ee.FeatureCollection([
        ee.Feature(to_ee_geometry(r.geom.simplify(0.002, preserve_topology=True).geojson), {"region_id": r.pk})
        for r in regions
    ])


def zonal_monthly(regions_fc: ee.FeatureCollection, month_start: dt.date) -> list[dict]:
    init()
    month_end = (month_start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    stack = indicator_stack(month_start, month_end)
    reduced = stack.reduceRegions(collection=regions_fc, reducer=ee.Reducer.mean(), scale=1000, tileScale=4)
    reduced = reduced.map(lambda f: ee.Feature(None, f.toDictionary()))
    return [f["properties"] for f in reduced.getInfo()["features"]]
