#!/usr/bin/env python3
"""
download_mapdata.py
-------------------
Télécharge UNE SEULE FOIS les données cartographiques Natural Earth :
  - Trait de côte haute résolution (10m)
  - Bathymétrie (200m, 1000m, 2000m, 4000m)
  - Pays / frontières
  - Lacs

Les convertit en fichiers .npy (numpy) ultra-rapides à charger.
Stockage : ~/science/mapdata/

Lancer avec internet, une seule fois.
Ensuite nmea_process.py trace les cartes hors ligne en < 0.5s.

Usage :
    python3 download_mapdata.py
"""

import urllib.request
import json
import numpy as np
import os
import zipfile
import io

MAP_DIR = os.path.expanduser("~/science/mapdata")
os.makedirs(MAP_DIR, exist_ok=True)

# =========================================================
# SOURCES Natural Earth (GeoJSON via github)
# =========================================================

DATASETS = [
    {
        "name": "coastline",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson",
        "desc": "Trait de côte 10m",
    },
    {
        "name": "land",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson",
        "desc": "Terres 10m",
    },
    {
        "name": "ocean",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_ocean.geojson",
        "desc": "Océan 10m",
    },
    {
        "name": "countries",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson",
        "desc": "Frontières pays 10m",
    },
    {
        "name": "lakes",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_lakes.geojson",
        "desc": "Lacs 10m",
    },
    {
        "name": "rivers",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_rivers_lake_centerlines.geojson",
        "desc": "Rivières 10m",
    },
    {
        "name": "bathy_200",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_bathymetry_K_200.geojson",
        "desc": "Bathymétrie 200m",
        "alt" : "https://raw.githubusercontent.com/martynafford/natural-earth-geojson/master/10m/physical/ne_10m_bathymetry_K_200.geojson",
    },
    {
        "name": "bathy_500",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_bathymetry_J_500.geojson",
        "desc": "Bathymétrie 500m",
        "alt" : "https://raw.githubusercontent.com/martynafford/natural-earth-geojson/master/10m/physical/ne_10m_bathymetry_J_500.geojson",
    },
    {
        "name": "bathy_1000",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_bathymetry_J_1000.geojson",
        "desc": "Bathymétrie 1000m",
        "alt" : "https://raw.githubusercontent.com/martynafford/natural-earth-geojson/master/10m/physical/ne_10m_bathymetry_J_1000.geojson",
    },
    {
        "name": "bathy_2000",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_bathymetry_I_2000.geojson",
        "desc": "Bathymétrie 2000m",
        "alt" : "https://raw.githubusercontent.com/martynafford/natural-earth-geojson/master/10m/physical/ne_10m_bathymetry_I_2000.geojson",
    },
    {
        "name": "bathy_3000",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_bathymetry_H_3000.geojson",
        "desc": "Bathymétrie 3000m",
        "alt" : "https://raw.githubusercontent.com/martynafford/natural-earth-geojson/master/10m/physical/ne_10m_bathymetry_H_3000.geojson",
    },
    {
        "name": "bathy_4000",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_bathymetry_G_4000.geojson",
        "desc": "Bathymétrie 4000m",
        "alt" : "https://raw.githubusercontent.com/martynafford/natural-earth-geojson/master/10m/physical/ne_10m_bathymetry_G_4000.geojson",
    },
    {
        "name": "bathy_5000",
        "url" : "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_bathymetry_F_5000.geojson",
        "desc": "Bathymétrie 5000m",
        "alt" : "https://raw.githubusercontent.com/martynafford/natural-earth-geojson/master/10m/physical/ne_10m_bathymetry_F_5000.geojson",
    },
]


# =========================================================
# PARSER GEOJSON -> liste de polylignes numpy
# =========================================================

def geojson_to_polylines(data):
    """
    Extrait toutes les polylignes d'un GeoJSON.
    Retourne une liste de arrays numpy shape (N,2) [lon, lat].
    Les polylignes sont séparées par un array contenant [nan, nan]
    pour un tracé matplotlib efficace en un seul appel plot().
    """
    lines = []

    def add_coords(coords):
        if not coords:
            return
        arr = np.array(coords, dtype=np.float32)
        if arr.ndim == 1:
            return
        lines.append(arr)
        lines.append(np.array([[np.nan, np.nan]], dtype=np.float32))

    def process_geometry(geom):
        if geom is None:
            return
        gt = geom.get("type", "")
        c  = geom.get("coordinates", [])
        if gt == "LineString":
            add_coords(c)
        elif gt == "MultiLineString":
            for seg in c:
                add_coords(seg)
        elif gt == "Polygon":
            for ring in c:
                add_coords(ring)
        elif gt == "MultiPolygon":
            for poly in c:
                for ring in poly:
                    add_coords(ring)
        elif gt == "GeometryCollection":
            for g in geom.get("geometries", []):
                process_geometry(g)

    features = data.get("features", [])
    if not features:
        process_geometry(data.get("geometry", {}))
    else:
        for feat in features:
            process_geometry(feat.get("geometry", {}))

    if not lines:
        return np.empty((0, 2), dtype=np.float32)

    combined = np.concatenate(lines, axis=0)
    return combined


# =========================================================
# TELECHARGEMENT ET CONVERSION
# =========================================================

print(f"\nStockage dans : {MAP_DIR}\n")

for ds in DATASETS:
    out_path = os.path.join(MAP_DIR, ds["name"] + ".npy")

    if os.path.exists(out_path):
        print(f"  [SKIP] {ds['name']} — déjà téléchargé")
        continue

    print(f"  Téléchargement {ds['desc']}...", end=" ", flush=True)
    try:
        urls_to_try = [ds["url"]]
        if "alt" in ds:
            urls_to_try.append(ds["alt"])

        raw = None
        for url in urls_to_try:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read()
                break
            except Exception as e:
                continue

        if raw is None:
            print(f"ERREUR : toutes les URLs ont échoué")
            continue

        data     = json.loads(raw)
        polydata = geojson_to_polylines(data)
        np.save(out_path, polydata)
        size_kb  = os.path.getsize(out_path) // 1024
        print(f"OK  ({len(polydata):,} points, {size_kb} Ko)")

    except Exception as e:
        print(f"ERREUR : {e}")

print(f"\nTerminé. Relancez nmea_process.py — les cartes seront instantanées.")
print(f"Données dans : {MAP_DIR}")
