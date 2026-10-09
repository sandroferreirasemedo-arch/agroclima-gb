import django.contrib.gis.db.models.fields
import django.db.models.deletion
from django.contrib.postgres.operations import CreateExtension
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        CreateExtension("postgis"),
        migrations.CreateModel(
            name="Region",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("code", models.CharField(max_length=40, unique=True)),
                ("level", models.PositiveSmallIntegerField(choices=[(0, "País"), (1, "Região"), (2, "Sector")], db_index=True, default=1)),
                ("geom", django.contrib.gis.db.models.fields.MultiPolygonField(srid=4326)),
                ("area_km2", models.FloatField(blank=True, null=True)),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="children", to="dashboard.region")),
            ],
            options={"ordering": ["level", "name"], "verbose_name": "região", "verbose_name_plural": "regiões"},
        ),
        migrations.CreateModel(
            name="SavedAOI",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True)),
                ("geom", django.contrib.gis.db.models.fields.MultiPolygonField(srid=4326)),
                ("area_km2", models.FloatField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "área de interesse", "verbose_name_plural": "áreas de interesse"},
        ),
        migrations.CreateModel(
            name="IndicatorStat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("indicator", models.CharField(choices=[("rain", "Precipitação (mm)"), ("rain_anom", "Anomalia de precipitação (%)"), ("spi3", "SPI-3 aproximado"), ("ndvi", "NDVI"), ("ndvi_anom", "Anomalia de NDVI"), ("vci", "VCI"), ("vhi", "VHI")], db_index=True, max_length=20)),
                ("period_start", models.DateField(db_index=True)),
                ("value", models.FloatField(null=True)),
                ("computed_at", models.DateTimeField(auto_now=True)),
                ("region", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="stats", to="dashboard.region")),
            ],
            options={"ordering": ["region", "indicator", "period_start"]},
        ),
        migrations.AddConstraint(
            model_name="indicatorstat",
            constraint=models.UniqueConstraint(fields=("region", "indicator", "period_start"), name="uniq_region_indicator_period"),
        ),
    ]
