"""
Carrega os limites administrativos da Guiné-Bissau para o PostGIS.

  python manage.py load_regions                       # regiões (nível 1) a partir do GEE
  python manage.py load_regions --level 2             # sectores (nível 2) a partir do GEE
  python manage.py load_regions --file gnb_adm1.geojson --name-field shapeName --code-field shapeISO
                                                      # a partir de um GeoJSON (ex.: geoBoundaries, INE)

O contorno nacional (nível 0) é criado automaticamente pela união das regiões.
"""
import json

from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from dashboard import gee
from dashboard.models import Region

GAUL_FIELDS = {
    1: {"name": "ADM1_NAME", "code": "ADM1_CODE", "parent": None},
    2: {"name": "ADM2_NAME", "code": "ADM2_CODE", "parent": "ADM1_CODE"},
}


def to_multipolygon(geom_dict):
    g = GEOSGeometry(json.dumps(geom_dict), srid=4326)
    if not g.valid:
        g = g.buffer(0)
    if g.geom_type == "Polygon":
        g = MultiPolygon(g, srid=4326)
    elif g.geom_type == "GeometryCollection":
        polys = [p for p in g if p.geom_type in ("Polygon", "MultiPolygon")]
        flat = []
        for p in polys:
            flat.extend(list(p) if p.geom_type == "MultiPolygon" else [p])
        g = MultiPolygon(*flat, srid=4326)
    if g.geom_type != "MultiPolygon":
        raise CommandError(f"Geometria inesperada: {g.geom_type}")
    return g


def area_km2(g):
    return round(g.transform(32628, clone=True).area / 1e6, 2)


class Command(BaseCommand):
    help = "Carrega regiões/sectores da Guiné-Bissau para o PostGIS (GEE ou GeoJSON)."

    def add_arguments(self, parser):
        parser.add_argument("--level", type=int, default=1, choices=[1, 2])
        parser.add_argument("--file", help="GeoJSON local em vez do GEE")
        parser.add_argument("--name-field", default="shapeName")
        parser.add_argument("--code-field", default="shapeID")
        parser.add_argument("--parent-field", help="Campo com o código da região-mãe (nível 2)")
        parser.add_argument("--replace", action="store_true", help="Apaga as unidades existentes desse nível")

    def handle(self, *args, **opt):
        level = opt["level"]
        if opt["file"]:
            with open(opt["file"], encoding="utf-8") as fh:
                features = json.load(fh)["features"]
            fields = {"name": opt["name_field"], "code": opt["code_field"], "parent": opt["parent_field"]}
            prefix = "F"
        else:
            gee.init()
            import ee

            asset = settings.GEE_ADMIN_L1_ASSET if level == 1 else settings.GEE_ADMIN_L2_ASSET
            fc = ee.FeatureCollection(asset).filter(
                ee.Filter.eq(settings.GEE_COUNTRY_FIELD, settings.GEE_COUNTRY_NAME))
            fc = fc.map(lambda f: f.simplify(50))
            self.stdout.write(f"A descarregar {asset} …")
            features = fc.getInfo()["features"]
            fields = GAUL_FIELDS[level]
            prefix = "GAUL"

        if not features:
            raise CommandError("Nenhuma unidade encontrada — verifique o asset/ficheiro e o nome do país.")

        with transaction.atomic():
            if opt["replace"]:
                Region.objects.filter(level=level).delete()
            n = 0
            for f in features:
                props = f["properties"]
                name = str(props.get(fields["name"], "")).strip() or "Sem nome"
                code = f"{prefix}{level}-{props.get(fields['code'])}"
                geom = to_multipolygon(f["geometry"])
                parent = None
                if level == 2 and fields.get("parent") and props.get(fields["parent"]) is not None:
                    parent = Region.objects.filter(level=1, code=f"{prefix}1-{props[fields['parent']]}").first()
                Region.objects.update_or_create(
                    code=code,
                    defaults={"name": name, "level": level, "geom": geom, "area_km2": area_km2(geom),
                              "parent": parent},
                )
                n += 1
            self.stdout.write(self.style.SUCCESS(f"{n} unidades de nível {level} carregadas."))

            if level == 1:
                union = None
                for r in Region.objects.filter(level=1):
                    union = r.geom if union is None else union.union(r.geom)
                country = to_multipolygon(json.loads(union.geojson))
                Region.objects.update_or_create(
                    code="GNB",
                    defaults={"name": "Guiné-Bissau", "level": 0, "geom": country, "area_km2": area_km2(country)},
                )
                Region.objects.filter(level=1).update(parent=Region.objects.get(code="GNB"))
                self.stdout.write(self.style.SUCCESS("Contorno nacional (GNB) actualizado."))
