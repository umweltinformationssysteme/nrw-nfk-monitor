import os
import urllib.request
import geopandas as gpd
import xarray as xr
import rioxarray
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from PIL import Image

def main():
    url = "https://files.ufz.de/~drought/nFK_0_25_daily_n14.nc"
    nc_file = "nFK_0_25_daily_n14.nc"
    geojson_file = "bundeslaender_simplify20.geojson"
    gif_path = "nrw_nfk_timeseries.gif"
    
    # 1. Aktuelle NetCDF-Datei herunterladen
    print("Lade tagesaktuelle NetCDF-Datei vom UFZ herunter...", flush=True)
    urllib.request.urlretrieve(url, nc_file)
    
    # 2. GeoJSON laden und nach NRW filtern
    print("Lade GeoJSON und filtere nach NRW...", flush=True)
    gdf = gpd.read_file(geojson_file)
    nrw = gdf[gdf['GEN'] == 'Nordrhein-Westfalen']
    
    if nrw.empty:
        raise ValueError("Nordrhein-Westfalen wurde nicht im GeoJSON gefunden! Bitte Prüfen Sie das Attribut 'GEN'.")
    
    # 3. NetCDF-Daten mit xarray öffnen
    print("Öffne und analysiere NetCDF-Datei...", flush=True)
    ds = xr.open_dataset(nc_file, decode_coords="all")
    
    # Die tatsächliche Datenvariable ermitteln (Metadaten/Hilfsvariablen ausschließen)
    exclude_vars = {'crs', 'time', 'lat', 'lon', 'latitude', 'longitude', 'x', 'y', 'height', 'spatial_ref', 'grid_mapping', 'transverse_mercator'}
    var_name = [v for v in ds.data_vars if v not in exclude_vars][0]
    print(f"Erkannte Datenvariable: {var_name}", flush=True)
    
    da = ds[var_name]
    
    # Automatische Erkennung der räumlichen Dimensionen durch rioxarray absichern
    x_dim = da.rio.x_dim
    y_dim = da.rio.y_dim
    
    # Fallback-Suche nach Dimensionen
    if not x_dim or not y_dim:
        for dim in da.dims:
            if any(k in str(dim).lower() for k in ['x', 'lon', 'east', 'easting']):
                x_dim = dim
            if any(k in str(dim).lower() for k in ['y', 'lat', 'north', 'northing']):
                y_dim = dim
        if x_dim and y_dim:
            da = da.rio.set_spatial_dims(x_dim=x_dim, y_dim=y_dim)

    print(f"Verwendete räumliche Dimensionen: x={x_dim}, y={y_dim}", flush=True)

    # Maximalen X-Wert ermitteln zur eindeutigen CRS-Bestimmung
    x_coords = da[x_dim].values if x_dim else []
    x_max = float(x_coords.max()) if len(x_coords) > 0 else 0
    print(f"Maximaler Koordinatenwert der X-Achse: {x_max}", flush=True)
    
    # --- PRÄZISE CRS-BESTIMMUNG ---
    # Wir prüfen, ob das System einen echten, validen EPSG-Code auflösen kann
    epsg_code = None
    if da.rio.crs:
        try:
            epsg_code = da.rio.crs.to_epsg()
        except Exception:
            pass

    # Wenn kein valider EPSG-Code existiert oder die Projektion als "undefined" markiert ist:
    if epsg_code is not None and "undefined" not in str(da.rio.crs).lower():
        print(f"-> Nativ erkanntes valides CRS: EPSG:{epsg_code}", flush=True)
    else:
        # Werte im 4-Millionen-Bereich auf der X-Achse kennzeichnen unmissverständlich Gauss-Krüger Zone 4
        if 4000000 < x_max < 5000000:
            print("-> Match: Wertebereich entspricht Gauss-Krüger Zone 4. Überschreibe mit EPSG:31468.", flush=True)
            da = da.rio.write_crs("EPSG:31468")
        elif x_max > 180:
            print(f"-> Unbekanntes Meterraster (X-Max: {x_max}). Versuche Standard-Reprojektion.", flush=True)
        else:
            print("-> Koordinaten liegen bereits in Grad vor. Setze EPSG:4326.", flush=True)
            da = da.rio.write_crs("EPSG:4326")

    # --- AKTIVE REPROJEKTION NACH WGS84 (GRAD) ---
    print("-> Reprojiziere Rasterdaten aktiv nach EPSG:4326 (WGS84 Grad)...", flush=True)
    da_gps = da.rio.reproject("EPSG:4326")

    # Das NRW-GeoJSON ebenfalls auf das gleiche System (WGS84 Grad) festlegen
    nrw = nrw.to_crs("EPSG:4326")
    
    # 4. Rasterdaten exakt auf die NRW-Grenzen zuschneiden (Clipping)
    print("Schneide Rasterdaten auf NRW-Umring zu...", flush=True)
    clipped_da = da_gps.rio.clip(nrw.geometry, crs="EPSG:4326", drop=True)
    
    # 5. Zeitraum filtern (auf die letzten 14 Tage begrenzen)
    num_days = min(14, len(clipped_da['time']))
    data_subset = clipped_da.isel(time=slice(-num_days, None))
    print(f"Erstelle {num_days} Frames für die Animation...", flush=True)
    
    # Farbskala definieren
    custom_colors = [
        '#F03B20', '#FFAA01', '#FFE9A8', '#FCFFEA', '#F0F9E8', '#D3EFCA',
        '#91D7C6', '#57B6AE', '#2A8EC2', '#2373BE', '#08539D'
    ]
    boundaries = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110]
    
    cmap = mcolors.ListedColormap(custom_colors)
    cmap.set_over('#07417B')
    norm = mcolors.BoundaryNorm(boundaries, cmap.N)
    
    os.makedirs("frames", exist_ok=True)
    frame_files = []
    
    # 6. Einzelne Karten-Frames generieren
    for i, t in enumerate(data_subset['time']):
        date_str = str(t.values)[:10]
        fig, ax = plt.subplots(figsize=(10, 8), dpi=150)
        
        data_subset.isel(time=i).plot(
            ax=ax,
            cmap=cmap,
            norm=norm,
            extend='max',
            cbar_kwargs={
                'label': 'Nutzbare Feldkapazität (%)', 
                'pad': 0.02,
                'ticks': boundaries
            }
        )
        
        nrw.plot(ax=ax, facecolor="none", edgecolor="black", linewidth=1.5)
        ax.set_title(f"Nutzbare Feldkapazität (0-25 cm) - NRW\nStand: {date_str}", fontsize=14, fontweight='bold')
        ax.set_axis_off()
        
        frame_file = f"frames/frame_{i:02d}.png"
        plt.savefig(frame_file, bbox_inches='tight')
        plt.close()
        frame_files.append(frame_file)
        
    # 7. Animiertes GIF erzeugen
    print("Erstelle animiertes GIF...", flush=True)
    imgs = [Image.open(f) for f in frame_files]
    imgs[0].save(
        gif_path,
        save_all=True,
        append_images=imgs[1:],
        duration=600,
        loop=0
    )
    
    # Aufräumen
    print("Bereinige temporäre Dateien...", flush=True)
    for f in frame_files:
        os.remove(f)
    os.rmdir("frames")
    os.remove(nc_file)
    
    print(f"Erfolgreich erstellt: {gif_path}", flush=True)

if __name__ == "__main__":
    main()
