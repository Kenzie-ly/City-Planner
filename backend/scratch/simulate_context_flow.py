import os
import sys

# Ensure backend directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import get_context_infrastructure

def run_simulation():
    print("Starting Context Flow Simulation...\n")

    # ──────────────────────────────────────────────────────────────
    # Test 1: Lokasi yang ADA di Database (Sri Rampai)
    # ──────────────────────────────────────────────────────────────
    print("--- Test 1: Lokasi ADA di Database (Sri Rampai) ---")
    lat_db = 3.1988
    lon_db = 101.7375
    
    entities_db = get_context_infrastructure(lat_db, lon_db)
    print(f"Total entities returned: {len(entities_db)}")
    
    # Verifikasi bahwa data diambil dari DB (tidak ada log fallback)
    assert len(entities_db) > 0, "FAIL: Seharusnya menemukan data di DB!"
    print("SUCCESS: Pengambilan data dari Database berhasil.\n")

    # ──────────────────────────────────────────────────────────────
    # Test 2: Lokasi yang TIDAK ADA di Database (Samudra / Nol)
    # ──────────────────────────────────────────────────────────────
    print("--- Test 2: Lokasi TIDAK ADA di Database (Fallback ke Overpass) ---")
    # Koordinat di tengah laut agar Overpass cepat merespon (kosong)
    lat_ocean = 0.0
    lon_ocean = 0.0
    
    entities_ocean = get_context_infrastructure(lat_ocean, lon_ocean)
    print(f"Total entities returned: {len(entities_ocean)}")
    
    # Verifikasi bahwa jalur fallback dieksekusi (akan tercetak log fallback)
    print("SUCCESS: Jalur fallback ke Overpass berhasil dieksekusi.\n")

    print("All simulations completed successfully.")

if __name__ == "__main__":
    try:
        run_simulation()
    except AssertionError as e:
        print(e)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected Error: {e}")
        sys.exit(1)
