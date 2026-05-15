import os
import sys

# Ensure backend directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reliability import (
    validate_geo_consistency,
    _station_like_anchor,
    _rewrite_unsupported_numeric_claims,
)

def run_simulation():
    print("Starting Reliability Logic Simulation...\n")

    # ──────────────────────────────────────────────────────────────
    # Simulasi 1: Penanganan List Bersarang (Nested List) di route_geometry
    # ──────────────────────────────────────────────────────────────
    print("--- Test 1: Penanganan Nested List di route_geometry ---")
    # Data simulasi menggunakan list of lists (seperti segmen polyline)
    raw_data = [{
        "route_geometry": [[{"lat": 3.14, "lng": 101.68}, {"lat": 3.15, "lng": 101.69}]],
        "candidates": []
    }]
    
    result = validate_geo_consistency(
        {"lat": 3.1390, "lng": 101.6869}, # City center
        {"location_label": "KL", "lat": 3.14, "lon": 101.68}, # Hotspot
        raw_data
    )
    
    print(f"Hasil Pass Check: {result.pass_check}")
    print(f"Checked Points: {result.checked_points}")
    print(f"Warnings: {result.warnings}")
    
    # Jika logic error belum diperbaiki, pass_check akan False karena route_points kosong
    assert result.pass_check == True, "FAIL: Nested list gagal diproses!"
    print("SUCCESS: Fungsi sekarang bisa membaca list bersarang!\n")

    # ──────────────────────────────────────────────────────────────
    # Simulasi 2: Penanganan String di road_a_queries (Bukan List)
    # ──────────────────────────────────────────────────────────────
    print("--- Test 2: Penanganan String di road_a_queries ---")
    selected_micro = {
        "road_a_queries": "LRT Ampang", # String, bukan list ["LRT Ampang"]
        "road_b_queries": []
    }
    
    is_anchor = _station_like_anchor(selected_micro)
    print(f"Apakah terdeteksi stasiun: {is_anchor}")
    
    # Jika logic error belum diperbaiki, string akan dipecah per huruf dan gagal mendeteksi "lrt"
    assert is_anchor == True, "FAIL: String dideteksi sebagai karakter terpisah!"
    print("SUCCESS: String ditangani dengan benar tanpa pecah karakter!\n")

    # ──────────────────────────────────────────────────────────────
    # Simulasi 3: Sensor Kata 'meters' (Bukan cuma 'm')
    # ──────────────────────────────────────────────────────────────
    print("--- Test 3: Sensor Kata 'meters' ---")
    text = "The corridor is 500 meters long and takes 10 minutes."
    removed_claims = []
    
    # Kita kosongkan allowed_facts agar semua angka disensor
    rewritten = _rewrite_unsupported_numeric_claims(text, [], removed_claims)
    
    print(f"Original: {text}")
    print(f"Hasil Sensor: {rewritten}")
    print(f"Removed Claims: {removed_claims}")
    
    # Jika regex belum diperbaiki, kata "meters" tidak akan tersensor
    assert "500 meters" not in rewritten, "FAIL: Kata 'meters' lolos dari sensor!"
    print("SUCCESS: Kata 'meters' sekarang berhasil disensor!\n")

    print("All simulations passed 100%! Logic errors eliminated.")

if __name__ == "__main__":
    try:
        run_simulation()
    except AssertionError as e:
        print(e)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected Error: {e}")
        sys.exit(1)
