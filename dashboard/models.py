from django.contrib.gis.db import models


class Region(models.Model):
    """Unidade administrativa da Guiné-Bissau (país, região ou sector)."""

    LEVEL_COUNTRY, LEVEL_REGION, LEVEL_SECTOR = 0, 1, 2
    LEVELS = [(0, "País"), (1, "Região"), (2, "Sector")]

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=40, unique=True)
    level = models.PositiveSmallIntegerField(choices=LEVELS, default=LEVEL_REGION, db_index=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    geom = models.MultiPolygonField(srid=4326)
    area_km2 = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["level", "name"]
        verbose_name = "região"
        verbose_name_plural = "regiões"

    def __str__(self):
        return f"{self.name} ({self.get_level_display()})"


class IndicatorStat(models.Model):
    """Valor médio mensal de um indicador agroclimático numa região (pré-calculado via GEE)."""

    INDICATORS = [
        ("rain", "Precipitação (mm)"),
        ("rain_anom", "Anomalia de precipitação (%)"),
        ("spi3", "SPI-3"),
        ("ndvi", "NDVI"),
        ("ndvi_anom", "Anomalia de NDVI"),
        ("vci", "VCI"),
        ("vhi", "VHI"),
    ]

    region = models.ForeignKey(Region, on_delete=models.CASCADE, related_name="stats")
    indicator = models.CharField(max_length=20, choices=INDICATORS, db_index=True)
    period_start = models.DateField(db_index=True)
    value = models.FloatField(null=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["region", "indicator", "period_start"], name="uniq_region_indicator_period")
        ]
        ordering = ["region", "indicator", "period_start"]

    def __str__(self):
        return f"{self.region.name} · {self.indicator} · {self.period_start:%Y-%m}: {self.value}"


class SavedAOI(models.Model):
    """Área de interesse desenhada e guardada pelos utilizadores."""

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    geom = models.MultiPolygonField(srid=4326)
    area_km2 = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "área de interesse"
        verbose_name_plural = "áreas de interesse"

    def __str__(self):
        return self.name
