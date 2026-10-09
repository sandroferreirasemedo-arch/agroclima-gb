from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/layers/", views.layers, name="layers"),
    path("api/layers/<str:name>/", views.layer_tiles, name="layer-tiles"),
    path("api/analyse/", views.analyse, name="analyse"),
    path("api/timelapse/", views.timelapse, name="timelapse"),
    path("api/regions/", views.regions, name="regions"),
    path("api/regions/<int:pk>/history/", views.region_history, name="region-history"),
    path("api/aois/", views.aois, name="aois"),
    path("api/aois/<int:pk>/", views.aoi_delete, name="aoi-delete"),
]
