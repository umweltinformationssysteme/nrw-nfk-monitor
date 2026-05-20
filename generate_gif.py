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
    print("Lade tagesaktuelle NetCDF-Datei vom UFZ herunter...")
    urllib.request.urlretrieve(url, nc_file)
    
    # 2. GeoJSON laden und nach NRW filtern
    print("Lade GeoJSON und filtere nach NRW...")
    gdf = gpd.read_file(geojson_file)
    nrw = gdf[gdf['GEN'] == 'Nordrhein-Westfalen']
    
    if nrw.empty:
        raise ValueError("Nordrhein-Westfalen wurde nicht im GeoJSON gefunden! Bitte Prüfen Sie das Attribut 'GEN'.")
    
    # 3. NetCDF-Daten mit xarray öffnen
    print("Öffne und analysiere NetCDF-Datei...")
    ds = xr.open_dataset(nc_file, decode_coords="all")
    
    # Räumliche Dimensionen aus den echten Dimensionen (ds.dims) ableiten
    x_dim = next((d for d in ds.dims if d.lower() in ['x', 'lon', 'longitude']), None)
    y_dim = next((d for d in ds.dims if d.lower() in ['y', 'lat', 'latitude']), None)
    
    if x_dim and y_dim:
        print(f"Setze räumliche Dimensionen: x={x_dim}, y={y_dim}")
        ds = ds.rio.set_spatial_dims(x_dim=x_dim, y_dim=y_dim)
    
    # --- KORREKTUR FÜR WIDERSPRÜCHLICHE METADATEN ---
    if x_dim and x_dim in ds:
        x_max = float(ds[x_dim].values.max())
        
        # Wenn die Koordinatenwerte im Grad-Bereich liegen (-180 bis 180),
        # überschreiben wir das fehlerhafte "Meter"-CRS aus der Datei mit WGS84.
        if -180 <= x_max <= 180:
            ds.rio.write_crs("EPSG:4326", override_crs=True, inplace=True)
            print("-> Fehlerhaftes Datei-CRS ignoriert. Erzwinge WGS84 (EPSG:4326), da Daten in Grad vorliegen.")
        else:
            # Falls die Werte wirklich riesig sind (Meter), prüfen wir auf das "undefined" Problem
            if not ds.rio.crs or "undefined" in str(ds.rio.crs).lower():
                ds.rio.write_crs("EPSG:3035", override_crs=True, inplace=True)
                print("-> Undefined Meter-CRS ersetzt durch Standard EPSG:3035 (LAEA).")
            else:
                print(f"-> Behalte gültiges Datei-CRS: {ds.rio.crs}")
    else:
        ds.rio.write_crs("EPSG:4326", override_crs=True, inplace=True)
        print("-> Keine Dimensionen zur Prüfung gefunden. Setze Standard EPSG:4326.")
    # ------------------------------------------------
        
    # Das NRW-GeoJSON exakt an das Koordinatensystem (CRS) des Rasters anpassen
    nrw = nrw.to_crs(ds.rio.crs)
    
    # Die tatsächliche Datenvariable ermitteln (Metadaten-Variablen ausschließen)
    exclude_vars = {'crs', 'time', 'lat', 'lon', 'latitude', 'longitude', 'x', 'y', 'height', 'spatial_ref'}
    var_name = [v for v in ds.data_vars if v not in exclude_vars][0]
    print(f"Erkannte Datenvariable: {var_name}")
    
    # 4. Rasterdaten exakt auf die NRW-Grenzen zuschneiden (Clipping)
    print("Schneide Rasterdaten auf NRW-Umring zu...")
    clipped_da = ds[var_name].rio.clip(nrw.geometry, crs=ds.rio.crs, drop=True)
    
    # 5. Zeitraum filtern (auf die letzten 14 Tage begrenzen)
    num_days = min(14, len(clipped_da['time']))
    data_subset = clipped_da.isel(time=slice(-num_days, None))
    print(f"Erstelle {num_days} Frames für die Animation...")
    
    # -------------------------------------------------------------------------
    # DEFINITION DEINER SPEZIFISCHEN FARBSKALA
    # -------------------------------------------------------------------------
    custom_colors = [
        '#F03B20', '#FFAA01', '#FFE9A8', '#FCFFEA', '#F0F9E8', '#D3EFCA',
        '#91D7C6', '#57B6AE', '#2A8EC2', '#2373BE', '#08539D'
    ]
    boundaries = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110]
    
    cmap = mcolors.ListedColormap(custom_colors)
    cmap.set_over('#07417B')
    norm = mcolors.BoundaryNorm(boundaries, cmap.N)
    # -------------------------------------------------------------------------
    
    # Temporären Ordner für die Einzelbilder anlegen
    os.makedirs("frames", exist_ok=True)
    frame_files = []
    
    # 6. Einzelne Karten-Frames generieren
    for i, t in enumerate(data_subset['time']):
        date_str = str(t.values)[:10]  # Format: YYYY-MM-DD
        
        fig, ax = plt.subplots(figsize=(10, 8), dpi=150)
        
        # Plotten mit der maßgeschneiderten Farbskala
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
        
        # Grenzen von NRW als schwarze Linie darüberlegen
        nrw.plot(ax=ax, facecolor="none", edgecolor="black", linewidth=1.5)
        
        ax.set_title(f"Nutzbare Feldkapazität (0-25 cm) - NRW\nStand: {date_str}", fontsize=14, fontweight='bold')
        ax.set_axis_off()
        
        frame_file = f"frames/frame_{i:02d}.png"
        plt.savefig(frame_file, bbox_inches='tight')
        plt.close()
        frame_files.append(frame_file)
        
    # 7. Animiertes GIF erzeugen
    print("Erstelle animiertes GIF...")
    imgs = [Image.open(f) for f in frame_files]
    imgs[0].save(
        gif_path,
        save_all=True,
        append_images=imgs[1:],
        duration=600,  # 600ms pro Tag
        loop=0         # Endlosschleife
    )
    
    # Aufräumen von temporären Dateien
    print("Bereinige temporäre Dateien...")
    for f in frame_files:
        os.remove(f)
    os.rmdir("frames")
    os.remove(nc_file)
    
    print(f"Erfolgreich erstellt: {gif_path}")

if __name__ == "__main__":
    main()
