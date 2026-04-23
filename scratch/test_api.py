import requests
import json

lat, lon = '3.1393715', '101.7312000'
overpass_query = f"""
[out:json][timeout:25];
(
  node["railway"="station"](around:800,{lat},{lon});
  way["railway"="station"](around:800,{lat},{lon});
  node["highway"="bus_stop"](around:800,{lat},{lon});
  node["amenity"="bus_station"](around:800,{lat},{lon});
  node["building"](around:800,{lat},{lon});
  way["building"](around:800,{lat},{lon});
  node["amenity"~"school|university|hospital|mall|clinic|office"](around:800,{lat},{lon});
  way["amenity"~"school|university|hospital|mall|clinic|office"](around:800,{lat},{lon});
);
out tags center;
"""

try:
    resp = requests.post("https://overpass-api.de/api/interpreter", data=overpass_query, timeout=25)
    print(f"Status Code: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Error Body: {resp.text}")
    else:
        data = resp.json()
        print(f"Elements returned: {len(data.get('elements', []))}")
except Exception as e:
    print(f"Exception: {e}")
