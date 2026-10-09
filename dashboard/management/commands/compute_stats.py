"""
Pré-calcula médias mensais dos indicadores por região (alimenta o mapa coroplético e o histórico).

  python manage.py compute_stats --start 2024-01 --end 2026-08
  python manage.py compute_stats --last 3                # últimos 3 meses completos (cron mensal)
  python manage.py compute_stats --last 6 --if-stale     # só corre se faltarem meses recentes
  python manage.py compute_stats --start 2026-06 --end 2026-08 --level 2

Meses ainda sem dados publicados (CHIRPS ~3 semanas, MOD13Q1 ~2 semanas de latência) são
ignorados com um aviso; recalcular meses já existentes actualiza os valores.
"""
import datetime as dt

import ee
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max

from dashboard import gee
from dashboard.models import IndicatorStat, Region


def parse_month(s):
    try:
        return dt.date.fromisoformat(s + "-01")
    except ValueError:
        raise CommandError(f"Mês inválido: {s} (use AAAA-MM)")


class Command(BaseCommand):
    help = "Calcula estatísticas zonais mensais (GEE) e grava-as no PostGIS."

    def add_arguments(self, parser):
        parser.add_argument("--start")
        parser.add_argument("--end")
        parser.add_argument("--last", type=int, help="Número de meses completos mais recentes")
        parser.add_argument("--level", type=int, default=1, choices=[0, 1, 2])
        parser.add_argument("--if-stale", action="store_true",
                            help="Não faz nada se já existirem dados de há menos de 2 meses")

    def handle(self, *args, **opt):
        this_month = dt.date.today().replace(day=1)
        if opt["last"]:
            first = gee.add_months(this_month, -opt["last"])
            months = [gee.add_months(first, i) for i in range(opt["last"])]
        elif opt["start"] and opt["end"]:
            s, e = parse_month(opt["start"]), parse_month(opt["end"])
            if e < s:
                raise CommandError("--end é anterior a --start.")
            months = gee.months_between(s, gee.add_months(e, 1))
        else:
            raise CommandError("Use --start/--end ou --last.")

        if opt["if_stale"]:
            latest = IndicatorStat.objects.filter(region__level=opt["level"]).aggregate(m=Max("period_start"))["m"]
            if latest and latest >= gee.add_months(this_month, -2):
                self.stdout.write(f"Estatísticas actualizadas (último mês {latest:%Y-%m}); nada a fazer.")
                return

        regions = list(Region.objects.filter(level=opt["level"]))
        if not regions:
            raise CommandError("Sem regiões nesse nível — execute primeiro `manage.py load_regions`.")

        gee.init()
        fc = gee.regions_collection(regions)

        done = skipped = 0
        for m in months:
            self.stdout.write(f"{m:%Y-%m} … ", ending="")
            try:
                rows = gee.zonal_monthly(fc, m)
            except ee.EEException as exc:
                self.stdout.write(self.style.WARNING(f"erro do Earth Engine, mês ignorado: {exc}"))
                skipped += 1
                continue
            if all(props.get("rain") is None for props in rows):
                self.stdout.write(self.style.WARNING("ainda sem dados publicados, mês ignorado"))
                skipped += 1
                continue
            n = 0
            for props in rows:
                rid = props.get("region_id")
                for band in gee.STAT_BANDS:
                    value = props.get(band)
                    if value is None:
                        continue
                    IndicatorStat.objects.update_or_create(
                        region_id=rid, indicator=band, period_start=m, defaults={"value": value},
                    )
                    n += 1
            done += 1
            self.stdout.write(self.style.SUCCESS(f"{n} valores"))
        self.stdout.write(f"Concluído: {done} meses calculados, {skipped} ignorados.")
