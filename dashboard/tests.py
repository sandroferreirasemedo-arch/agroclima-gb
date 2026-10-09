"""Testes do AgroClima (o Earth Engine é simulado; não são precisas credenciais).

    python manage.py test dashboard
"""
import datetime as dt
import io
import json
from pathlib import Path
from unittest import mock

import ee
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from . import gee
from .models import IndicatorStat, Region, SavedAOI
from .views import parse_period, BadRequest

DATA = Path(settings.BASE_DIR) / "data" / "gnb_adm1_geoboundaries.geojson"
POLY = {"type": "Polygon", "coordinates": [[[-15.5, 11.9], [-15.3, 11.9], [-15.3, 12.0], [-15.5, 12.0], [-15.5, 11.9]]]}
FAKE_STATS = {
    "start": "2026-06-01", "end": "2026-08-31", "area_km2": 240.0,
    "indicators": {b: {"mean": 1.0, "min": 0.0, "max": 2.0, "stdDev": 0.5} for b in gee.STAT_BANDS},
    "severity": [{"class": i, "label": gee.SEVERITY_CLASSES[i], "pct": 20.0} for i in range(5)],
}
FAKE_SERIES = {"rain": [], "ndvi": [], "baselines": {"rain": [1991, 2020], "vegetation": [2001, 2020]}}


class DateUtilsTests(TestCase):
    def test_add_months(self):
        self.assertEqual(gee.add_months(dt.date(2026, 1, 15), -2), dt.date(2025, 11, 1))
        self.assertEqual(gee.add_months(dt.date(2026, 12, 1), 1), dt.date(2027, 1, 1))

    def test_months_between(self):
        self.assertEqual(
            gee.months_between(dt.date(2026, 6, 10), dt.date(2026, 9, 1)),
            [dt.date(2026, 6, 1), dt.date(2026, 7, 1), dt.date(2026, 8, 1)],
        )

    def test_shift_year_leap_day(self):
        self.assertEqual(gee._shift_year(dt.date(2024, 2, 29), -1), dt.date(2023, 2, 28))

    def test_parse_period(self):
        self.assertEqual(parse_period({"start": "2026-06-01", "end": "2026-08-31"}),
                         (dt.date(2026, 6, 1), dt.date(2026, 9, 1)))
        for bad in ({"start": "x", "end": "2026-08-31"},
                    {"start": "2026-08-31", "end": "2026-06-01"},
                    {"start": "2026-06-01", "end": "2026-06-05"},
                    {"start": "1975-01-01", "end": "1975-12-31"},
                    {"start": "2026-06-01", "end": (dt.date.today() + dt.timedelta(days=40)).isoformat()}):
            with self.assertRaises(BadRequest):
                parse_period(bad)


class InitTests(TestCase):
    def setUp(self):
        gee._initialized = False

    @override_settings(GEE_SERVICE_ACCOUNT_KEY="", GEE_PROJECT="")
    def test_not_configured(self):
        with self.assertRaises(gee.GEENotConfigured):
            gee.init()

    @override_settings(GEE_SERVICE_ACCOUNT_KEY="/nao/existe.json", GEE_PROJECT="p")
    def test_missing_key_file(self):
        with self.assertRaisesRegex(gee.GEENotConfigured, "não encontrada"):
            gee.init()


class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_regions", file=str(DATA), stdout=io.StringIO())
        cls.oio = Region.objects.get(name="Oio")
        IndicatorStat.objects.create(region=cls.oio, indicator="vhi", period_start=dt.date(2026, 8, 1), value=32.5)

    def test_regions_loaded(self):
        self.assertEqual(Region.objects.filter(level=1).count(), 9)
        self.assertTrue(Region.objects.filter(level=0, code="GNB").exists())

    def test_index(self):
        r = self.client.get("/")
        self.assertContains(r, "AgroClima")
        self.assertContains(r, "SPI-3 (gama)")

    def test_healthz_ignores_host(self):
        r = self.client.get("/healthz", HTTP_HOST="10.0.0.5:10000")
        self.assertEqual(r.status_code, 200)

    def test_regions_geojson(self):
        d = self.client.get("/api/regions/?level=1&indicator=vhi").json()
        self.assertEqual(len(d["features"]), 9)
        self.assertEqual(d["period"], "2026-08")
        vals = {f["properties"]["name"]: f["properties"]["value"] for f in d["features"]}
        self.assertEqual(vals["Oio"], 32.5)
        self.assertEqual(self.client.get("/api/regions/?level=abc").status_code, 400)

    def test_aoi_create_and_protected_delete(self):
        r = self.client.post("/api/aois/", json.dumps({"name": "<img src=x>", "geometry": POLY}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201)
        aid = r.json()["id"]
        self.assertEqual(self.client.delete(f"/api/aois/{aid}/").status_code, 403)
        staff = User.objects.create_user("admin", password="x", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.delete(f"/api/aois/{aid}/").status_code, 200)
        self.assertFalse(SavedAOI.objects.exists())

    def test_aoi_too_large(self):
        big = {"type": "Polygon", "coordinates": [[[-20, 8], [-10, 8], [-10, 16], [-20, 16], [-20, 8]]]}
        r = self.client.post("/api/analyse/", json.dumps({"start": "2026-06-01", "end": "2026-08-31", "geometry": big}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("demasiado grande", r.json()["error"])

    def test_unknown_layer_and_404(self):
        self.assertEqual(self.client.get("/api/layers/xyz/?start=2026-06-01&end=2026-08-31").status_code, 400)
        self.assertEqual(self.client.get("/api/regions/999999/history/").status_code, 404)

    @mock.patch.object(gee, "time_series", return_value=FAKE_SERIES)
    @mock.patch.object(gee, "aoi_statistics", return_value=FAKE_STATS)
    def test_analyse_region(self, m_stats, m_ts):
        r = self.client.post("/api/analyse/", json.dumps({"start": "2026-06-01", "end": "2026-08-31",
                                                          "region_id": self.oio.pk}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["target"]["name"], "Oio")
        _, start, end, mask = m_stats.call_args.args
        self.assertEqual((start, end, mask), (dt.date(2026, 6, 1), dt.date(2026, 9, 1), "none"))

    @mock.patch.object(gee, "get_layer", side_effect=gee.GEENotConfigured("sem chave"))
    def test_gee_not_configured_is_503(self, _):
        r = self.client.get("/api/layers/ndvi/?start=2026-06-01&end=2026-08-31")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"], "sem chave")

    @mock.patch.object(gee, "get_layer", side_effect=ee.EEException("Project is not registered to use Earth Engine"))
    def test_ee_error_is_friendly(self, _):
        r = self.client.get("/api/layers/ndvi/?start=2026-06-01&end=2026-08-31")
        self.assertEqual(r.status_code, 502)
        self.assertIn("code.earthengine.google.com/register", r.json()["error"])

    @mock.patch.object(gee, "timelapse", side_effect=RuntimeError("boom"))
    def test_unexpected_error_is_json_500(self, _):
        r = self.client.get("/api/timelapse/?start=2026-01-01&end=2026-08-31&var=ndvi")
        self.assertEqual(r.status_code, 500)
        self.assertIn("error", r.json())


class ComputeStatsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_regions", file=str(DATA), stdout=io.StringIO())

    def _fake_zonal(self, fc, month):
        if month == dt.date(2026, 8, 1):  # mês ainda sem dados
            return [{"region_id": r.pk, "rain": None} for r in Region.objects.filter(level=1)]
        return [{"region_id": r.pk, **{b: 10.0 for b in gee.STAT_BANDS}} for r in Region.objects.filter(level=1)]

    def test_compute_and_skip_missing_month(self):
        out = io.StringIO()
        with mock.patch.object(gee, "init"), mock.patch.object(gee, "regions_collection"), \
                mock.patch.object(gee, "zonal_monthly", side_effect=self._fake_zonal):
            call_command("compute_stats", start="2026-06", end="2026-08", stdout=out)
        self.assertEqual(IndicatorStat.objects.count(), 2 * 9 * len(gee.STAT_BANDS))
        self.assertFalse(IndicatorStat.objects.filter(period_start=dt.date(2026, 8, 1)).exists())
        self.assertIn("ainda sem dados", out.getvalue())

    def test_ee_error_skips_month(self):
        out = io.StringIO()
        with mock.patch.object(gee, "init"), mock.patch.object(gee, "regions_collection"), \
                mock.patch.object(gee, "zonal_monthly", side_effect=ee.EEException("Computation timed out")):
            call_command("compute_stats", start="2026-06", end="2026-06", stdout=out)
        self.assertIn("1 ignorados", out.getvalue())

    def test_if_stale(self):
        recent = gee.add_months(dt.date.today().replace(day=1), -1)
        IndicatorStat.objects.create(region=Region.objects.filter(level=1).first(), indicator="vhi",
                                     period_start=recent, value=50)
        out = io.StringIO()
        with mock.patch.object(gee, "zonal_monthly") as zm:
            call_command("compute_stats", last=6, if_stale=True, stdout=out)
        zm.assert_not_called()
        self.assertIn("nada a fazer", out.getvalue())
