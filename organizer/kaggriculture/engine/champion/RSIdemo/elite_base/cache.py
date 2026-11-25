from pathlib import Path

def native_cache():
    path=Path(__file__).resolve().parent/'_native_cache'
    path.mkdir(exist_ok=True)
    return path
