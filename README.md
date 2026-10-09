# AgroClima Guiné-Bissau

Painel de inteligência agroclimática para a Guiné-Bissau: monitoriza condições agrícolas, variabilidade
climática e risco de seca a partir de três décadas de observação da Terra por satélite.

**Stack:** Django + GeoDjango · Google Earth Engine · PostgreSQL/PostGIS · Leaflet · Chart.js · Docker

## O que o sistema faz

| Módulo | Descrição |
|---|---|
| **Mapa** | Camadas do Earth Engine geradas a pedido para qualquer período: precipitação, anomalias, SPI-3, NDVI, VCI, LST, TCI, VHI e classes de severidade da seca. Máscara opcional de áreas de cultivo. |
| **Área de interesse (AOI)** | Desenhar polígono/rectângulo, clicar numa região ou escolher uma área guardada no PostGIS → indicadores-síntese, séries temporais e % da área em cada classe de seca. Exportação CSV. |
| **Séries temporais** | Precipitação mensal vs climatologia 1991–2020 e NDVI de 16 dias vs referência 2001–2020 (mesmo dia do ano). |
| **Timelapse** | Animação mensal (NDVI, precipitação ou anomalia de NDVI) com leitor no mapa e GIF para descarregar. |
| **Regiões** | Mapa coroplético e classificação das regiões/sectores com médias mensais pré-calculadas e guardadas no PostGIS. |

Predefinições de período pensadas para o calendário agrícola do país: **época chuvosa** (Jun–Out, arroz de
bolanha e de mangal, milho, sorgo) e **campanha do caju** (Dez–Mai, da floração à colheita).

## Dados e metodologia

| Indicador | Fonte (GEE) | Método |
|---|---|---|
| Precipitação | `UCSB-CHG/CHIRPS/DAILY` (0,05°) | Soma diária no período |
| Anomalia de precipitação | CHIRPS | (actual − média do mesmo período em 1991–2020) / média × 100 |
| SPI-1, SPI-3, SPI-6 | CHIRPS | Ajuste gama por píxel aos totais de 1, 3 ou 6 meses (1991–2020, estimador de Thom), com probabilidade de zeros; H = q + (1−q)·G(x) → normal padrão. Mascarado onde >30 % dos anos de referência são secos ou a média é <10 mm/mês |
| NDVI | `MODIS/061/MOD13Q1` (250 m, 16 dias) | Média, só píxeis com SummaryQA ≤ 1 |
| Anomalia de NDVI | MOD13Q1 | NDVI actual − média do mesmo período em 2001–2020 |
| VCI | MOD13Q1 | (NDVI − NDVImin) / (NDVImax − NDVImin) × 100, extremos 2001–2020 |
| LST / TCI | `MODIS/061/MOD11A2` (1 km, 8 dias) | TCI = (LSTmax − LST) / (LSTmax − LSTmin) × 100 |
| VHI | VCI + TCI | 0,5·VCI + 0,5·TCI |
| Severidade da seca | VHI | Classes de Kogan: <10 extrema, <20 severa, <30 moderada, <40 ligeira, ≥40 sem seca |
| Máscara de cultivo | `ESA/WorldCover/v200` | Classe 40 (cropland) |

### Limitações a conhecer

- **SPI na estação seca.** Entre Novembro e Abril quase não chove na Guiné-Bissau; nessas janelas o SPI
  não tem significado e o painel deixa-o em branco ("sem dado"). Use SPI-1/3/6 sobretudo na época chuvosa.
  A fórmula foi validada contra o ajuste gama de máxima verosimilhança do SciPy (diferença ≤ 0,004).
- **Nebulosidade.** De Julho a Setembro a cobertura de nuvens é muito alta; o filtro de qualidade do MODIS
  deixa lacunas e o NDVI pode ficar subestimado. O VHI/LST também sofrem com isto.
- **Cajueiros.** Os pomares de caju aparecem sobretudo como "árvores" no WorldCover, não como "cultivo".
  A máscara de cultivo serve para arroz e culturas anuais; para o caju, analise sem máscara ou use um
  mapa nacional de pomares.
- **Ilhas e litoral.** O CHIRPS a 0,05° (~5,5 km) suaviza o Arquipélago dos Bijagós e as zonas de mangal.
- **Latência.** O CHIRPS final sai ~3 semanas após o fim do mês; o MOD13Q1 ~2 semanas após cada composição.
  As predefinições do painel já recuam até ao último mês com dados prováveis.
- **Limites administrativos.** O FAO GAUL 2015 está desactualizado; para uso institucional carregue os
  limites oficiais (INE Guiné-Bissau) com `load_regions --file`.

## Instalação (Docker)

1. **Earth Engine:** crie (ou use) um projecto Google Cloud registado para Earth Engine, crie uma conta de
   serviço com os papéis *Earth Engine Resource Viewer* e *Service Usage Consumer* e
   descarregue a chave JSON para `secrets/gee-key.json`.
2. Configure o ambiente:
   ```bash
   cp .env.example .env      # preencha GEE_PROJECT, palavras-passe e DJANGO_SECRET_KEY
   docker compose up -d --build
   ```
3. Carregue os limites e pré-calcule estatísticas:
   ```bash
   docker compose exec web python manage.py load_regions              # 9 regiões + contorno nacional
   docker compose exec web python manage.py load_regions --level 2    # sectores (opcional)
   docker compose exec web python manage.py compute_stats --start 2024-01 --end 2026-08
   docker compose exec web python manage.py createsuperuser           # acesso ao /admin
   ```
4. Abra <http://localhost:8000>.

Para manter o painel actualizado, agende mensalmente (dia 25, por exemplo):
`python manage.py compute_stats --last 3`.

### Publicar na web (Render)

1. Envie este projecto para um repositório GitHub (sem a pasta `secrets/` nem o `.env`).
2. Render → **New → Blueprint** → escolha o repositório. O `render.yaml` cria o site e a base PostGIS.
3. Indique `GEE_PROJECT` quando o Render pedir.
4. No serviço `agroclima-gb` → **Environment → Secret Files**: crie `gee-key.json` com o conteúdo da chave JSON.
5. **Manual Deploy → Deploy latest commit**. No primeiro arranque carregam-se as 9 regiões e calculam-se
   em segundo plano as estatísticas dos últimos 6 meses (`AUTO_STATS_MONTHS`).

O plano gratuito adormece após 15 min sem visitas e a base gratuita expira ao fim de 30 dias:
para uso institucional mude os planos em `render.yaml`.

### Limites oficiais a partir de GeoJSON

O projecto já inclui as 9 regiões (geoBoundaries gbOpen, ADM1) em `data/`:
```bash
python manage.py load_regions --file data/gnb_adm1_geoboundaries.geojson --replace
```

```bash
# Ex.: geoBoundaries (gbOpen GNB ADM1) ou ficheiro do INE
python manage.py load_regions --file gnb_adm1.geojson --name-field shapeName --code-field shapeID --replace
python manage.py load_regions --level 2 --file gnb_adm2.geojson --name-field shapeName --code-field shapeID
```

### Desenvolvimento sem Docker

Requer GDAL/GEOS/PROJ e PostgreSQL com PostGIS.
```bash
python -m venv venv && . venv/bin/activate
pip install -r requirements.txt
earthengine authenticate                  # alternativa à conta de serviço
export GEE_PROJECT=o-seu-projecto POSTGRES_HOST=localhost
python manage.py migrate && python manage.py runserver
```

## Testes

```bash
python manage.py test dashboard      # 20 testes; o Earth Engine é simulado, não precisa de credenciais
```

## API

| Método | Endpoint | Descrição |
|---|---|---|
| GET | `/healthz` | Verificação de saúde (usada pelo Render) |
| GET | `/api/layers/` | Catálogo de camadas |
| GET | `/api/layers/<camada>/?start=&end=&mask=none\|cropland` | URL de mosaicos XYZ do GEE + legenda |
| POST | `/api/analyse/` | `{start, end, mask, region_id \| aoi_id \| geometry}` → estatísticas + séries |
| GET | `/api/timelapse/?start=&end=&var=ndvi\|rain\|ndvi_anom` | Fotogramas mensais (mosaicos) + GIF |
| GET | `/api/regions/?level=1&indicator=vhi&period=AAAA-MM` | GeoJSON com valores pré-calculados |
| GET | `/api/regions/<id>/history/` | Histórico mensal de todos os indicadores da região |
| GET/POST | `/api/aois/` | Listar / guardar áreas de interesse |
| DELETE | `/api/aois/<id>/` | Apagar área de interesse (só administradores) |

As datas são inclusivas (`AAAA-MM-DD`). Os resultados do GEE ficam em cache (6 h; mosaicos 2 h).

## Estrutura

```
config/                 definições Django (tudo via variáveis de ambiente)
dashboard/
  gee.py                serviço Earth Engine: indicadores, camadas, estatísticas, séries, timelapse
  models.py             Region, IndicatorStat, SavedAOI (PostGIS, SRID 4326)
  views.py / urls.py    API JSON e página principal
  management/commands/
    load_regions.py     limites administrativos (GEE ou GeoJSON)
    compute_stats.py    estatísticas zonais mensais → PostGIS
  templates/, static/   interface Leaflet + Chart.js
```

## Próximos passos sugeridos

- SPI-12 e SPEI (com evapotranspiração MODIS ou ERA5-Land).
- Sentinel-2 (10 m) para bolanhas e vales, com composições sem nuvens por estação.
- Calendários de cultivo por região e máscara de pomares de caju.
- Alertas automáticos (e-mail/SMS) quando o VHI de uma região desce abaixo de 30.
- Exportação para os ciclos do *Cadre Harmonisé* (CILSS) e partilha via OGC API (Features/Tiles).
