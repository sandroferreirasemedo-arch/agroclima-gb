import datetime as dt
import functools
import json
import logging

import ee
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.db.models import Max
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import gee
from .models import IndicatorStat, Region, SavedAOI

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
class BadRequest(ValueError):
    pass


def api(view):
    """Converte erros em respostas JSON legíveis pelo frontend."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except gee.GEENotConfigured as exc:
            return JsonResponse({"error": str(exc)}, status=503)
        except (BadRequest, ValueError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except ee.EEException as exc:
            log.exception("Erro do Earth Engine")
            return JsonResponse({"error": friendly_ee_error(exc)}, status=502)
        except Http404:
            return JsonResponse({"error": "Recurso não encontrado."}, status=404)
        except PermissionDenied as exc:
            return JsonResponse({"error": str(exc) or "Sem permissão."}, status=403)
        except Exception:
            log.exception("Erro inesperado em %s", request.path)
            return JsonResponse({"error": "Erro interno do servidor. Consulte os logs."}, status=500)

    return wrapper


def friendly_ee_error(exc) -> str:
    msg = str(exc)
    low = msg.lower()
    if "not registered" in low or "permission" in low or "403" in low:
        return ("Earth Engine recusou o acesso: confirme que o projecto está registado em "
                "code.earthengine.google.com/register e que a conta de serviço tem os papéis "
                "'Earth Engine Resource Viewer' e 'Service Usage Consumer'. Detalhe: " + msg)
    if "timed out" in low or "computation timed out" in low:
        return "O Earth Engine demorou demasiado. Reduza o período ou a área e tente de novo."
    if "too many concurrent" in low or "quota" in low:
        return "Limite de pedidos do Earth Engine atingido. Aguarde um minuto e tente de novo."
    return f"Earth Engine: {msg}"


def parse_period(data):
    """Datas inclusivas da interface → (início, fim exclusivo) para o GEE."""
    try:
        start = dt.date.fromisoformat(data.get("start", ""))
        end = dt.date.fromisoformat(data.get("end", ""))
    except ValueError:
        raise BadRequest("Datas inválidas: use o formato AAAA-MM-DD.")
    if end < start:
        raise BadRequest("A data final é anterior à data inicial.")
    if start < dt.date(1981, 1, 1):
        raise BadRequest("O CHIRPS começa em 1981.")
    if (end - start).days < 15:
        raise BadRequest("Escolha um período de pelo menos 16 dias (as composições MODIS são de 16 dias).")
    if end - dt.timedelta(days=1) > dt.date.today():
        raise BadRequest("A data final não pode estar no futuro.")
    if (end - start).days > settings.MAX_PERIOD_DAYS:
        raise BadRequest(f"Período demasiado longo (máximo {settings.MAX_PERIOD_DAYS} dias).")
    return start, end + dt.timedelta(days=1)


def region_geojson(region, tolerance=0.001):
    return json.loads(region.geom.simplify(tolerance, preserve_topology=True).geojson)


def validate_user_geometry(geojson):
    if not geojson:
        raise BadRequest("Indique uma geometria ou uma região.")
    try:
        if isinstance(geojson, str):
            geojson = json.loads(geojson)
        geos = GEOSGeometry(json.dumps(geojson.get("geometry", geojson)), srid=4326)
    except Exception:
        raise BadRequest("GeoJSON inválido.")
    if geos.geom_type not in ("Polygon", "MultiPolygon"):
        raise BadRequest("A área de interesse tem de ser um polígono.")
    if not geos.valid:
        geos = geos.buffer(0)
    area = geos.transform(32628, clone=True).area / 1e6  # UTM 28N cobre a Guiné-Bissau
    if area > settings.MAX_AOI_KM2:
        raise BadRequest(f"Área demasiado grande ({area:,.0f} km²; máximo {settings.MAX_AOI_KM2:,.0f} km²).")
    return geos, area


def resolve_target(body):
    """Devolve (geojson, descrição) a partir de region_id ou de uma geometria desenhada."""
    if body.get("region_id"):
        region = get_object_or_404(Region, pk=body["region_id"])
        return region_geojson(region), {"type": "region", "id": region.pk, "name": region.name}
    if body.get("aoi_id"):
        aoi = get_object_or_404(SavedAOI, pk=body["aoi_id"])
        return json.loads(aoi.geom.geojson), {"type": "aoi", "id": aoi.pk, "name": aoi.name}
    geos, area = validate_user_geometry(body.get("geometry"))
    return json.loads(geos.geojson), {"type": "drawn", "name": "Área desenhada", "area_km2": round(area, 1)}


def read_json(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        raise BadRequest("Corpo JSON inválido.")


# ---------------------------------------------------------------------------
# Verificação de saúde (antes da validação de host; usada pelo Render)
# ---------------------------------------------------------------------------
class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/healthz":
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------
@ensure_csrf_cookie
def index(request):
    return render(request, "dashboard/index.html", {
        "layers": gee.layer_catalog(),
        "timelapse_vars": gee.TIMELAPSE_VARS,
        "indicators": IndicatorStat.INDICATORS,
        "rain_baseline": settings.RAIN_BASELINE,
        "veg_baseline": settings.VEG_BASELINE,
    })


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@require_GET
@api
def layers(request):
    return JsonResponse({"layers": gee.layer_catalog()})


@require_GET
@api
def layer_tiles(request, name):
    if name not in gee.LAYERS:
        raise BadRequest(f"Camada desconhecida: {name}")
    start, end = parse_period(request.GET)
    mask = request.GET.get("mask", "none")
    return JsonResponse(gee.get_layer(name, start, end, mask))


@require_POST
@api
def analyse(request):
    """Estatísticas + séries temporais para uma região, AOI guardada ou polígono desenhado."""
    body = read_json(request)
    start, end = parse_period(body)
    geojson, target = resolve_target(body)
    mask = body.get("mask", "none")
    return JsonResponse({
        "target": target,
        "statistics": gee.aoi_statistics(geojson, start, end, mask),
        "series": gee.time_series(geojson, start, end),
    })


@require_GET
@api
def timelapse(request):
    start, end = parse_period(request.GET)
    return JsonResponse(gee.timelapse(request.GET.get("var", "ndvi"), start, end))


@require_GET
@api
def regions(request):
    """GeoJSON das regiões com o último valor pré-calculado do indicador escolhido."""
    try:
        level = int(request.GET.get("level", 1))
    except ValueError:
        raise BadRequest("Nível inválido.")
    indicator = request.GET.get("indicator", "vhi")
    period = request.GET.get("period")  # AAAA-MM opcional

    qs = Region.objects.filter(level=level)
    stats = IndicatorStat.objects.filter(indicator=indicator, region__level=level)
    if period:
        try:
            p = dt.date.fromisoformat(period + "-01")
        except ValueError:
            raise BadRequest("Período inválido: use AAAA-MM.")
    else:
        p = stats.aggregate(m=Max("period_start"))["m"]
    values = {s.region_id: s.value for s in stats.filter(period_start=p)} if p else {}
    periods = sorted({d.strftime("%Y-%m") for d in stats.values_list("period_start", flat=True).distinct()})

    features = [{
        "type": "Feature",
        "id": r.pk,
        "geometry": region_geojson(r, 0.002),
        "properties": {
            "id": r.pk, "name": r.name, "code": r.code, "area_km2": r.area_km2,
            "value": values.get(r.pk),
        },
    } for r in qs]
    return JsonResponse({
        "type": "FeatureCollection",
        "features": features,
        "indicator": indicator,
        "period": p.strftime("%Y-%m") if p else None,
        "available_periods": periods,
    })


@require_GET
@api
def region_history(request, pk):
    region = get_object_or_404(Region, pk=pk)
    series = {}
    for s in region.stats.order_by("period_start"):
        series.setdefault(s.indicator, []).append({"date": s.period_start.strftime("%Y-%m"), "value": s.value})
    return JsonResponse({"region": {"id": region.pk, "name": region.name}, "series": series})


@require_http_methods(["GET", "POST"])
@api
def aois(request):
    if request.method == "GET":
        return JsonResponse({"type": "FeatureCollection", "features": [{
            "type": "Feature", "id": a.pk, "geometry": json.loads(a.geom.geojson),
            "properties": {"id": a.pk, "name": a.name, "description": a.description, "area_km2": a.area_km2},
        } for a in SavedAOI.objects.all()[:200]]})

    body = read_json(request)
    name = (body.get("name") or "").strip()
    if not name:
        raise BadRequest("Dê um nome à área de interesse.")
    geos, area = validate_user_geometry(body.get("geometry"))
    if geos.geom_type == "Polygon":
        geos = MultiPolygon(geos, srid=4326)
    aoi = SavedAOI.objects.create(name=name[:120], description=body.get("description", ""), geom=geos,
                                  area_km2=round(area, 2))
    return JsonResponse({"id": aoi.pk, "name": aoi.name, "area_km2": aoi.area_km2}, status=201)


@require_http_methods(["DELETE"])
@api
def aoi_delete(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied("Só administradores podem apagar áreas (use /admin).")
    get_object_or_404(SavedAOI, pk=pk).delete()
    return JsonResponse({"deleted": pk})
