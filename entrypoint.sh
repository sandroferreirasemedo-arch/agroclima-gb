#!/bin/sh
set -e

echo "A aguardar a base de dados PostGIS…"
until python -c "import psycopg, os; psycopg.connect(host=os.environ.get('POSTGRES_HOST','db'), port=os.environ.get('POSTGRES_PORT','5432'), dbname=os.environ['POSTGRES_DB'], user=os.environ['POSTGRES_USER'], password=os.environ['POSTGRES_PASSWORD']).close()" 2>/dev/null; do
  sleep 1
done

python manage.py migrate --noinput
python manage.py collectstatic --noinput >/dev/null

# Primeira execução: carrega as 9 regiões incluídas no projecto
if [ "$(python manage.py shell -c 'from dashboard.models import Region; print(Region.objects.count())' 2>/dev/null | tail -1)" = "0" ]; then
  echo "A carregar os limites regionais…"
  python manage.py load_regions --file data/gnb_adm1_geoboundaries.geojson
fi

# Opcional: calcula em segundo plano as estatísticas mensais dos últimos N meses
if [ -n "$AUTO_STATS_MONTHS" ] && [ -n "$GEE_PROJECT" ]; then
  echo "A calcular estatísticas dos últimos $AUTO_STATS_MONTHS meses em segundo plano…"
  (python manage.py compute_stats --last "$AUTO_STATS_MONTHS" --if-stale || echo "compute_stats falhou — ver logs") &
fi

exec "$@"
