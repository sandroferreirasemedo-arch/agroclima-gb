from django.contrib.gis import admin

from .models import IndicatorStat, Region, SavedAOI


@admin.register(Region)
class RegionAdmin(admin.GISModelAdmin):
    list_display = ("name", "code", "level", "parent", "area_km2")
    list_filter = ("level",)
    search_fields = ("name", "code")


@admin.register(IndicatorStat)
class IndicatorStatAdmin(admin.ModelAdmin):
    list_display = ("region", "indicator", "period_start", "value", "computed_at")
    list_filter = ("indicator", "region__level", "region")
    date_hierarchy = "period_start"


@admin.register(SavedAOI)
class SavedAOIAdmin(admin.GISModelAdmin):
    list_display = ("name", "area_km2", "created_at")
    search_fields = ("name",)
