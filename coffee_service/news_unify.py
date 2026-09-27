"""Regenerate the unified Jev CSV without an API request."""
from coffee_service import jev, jev_store


def run(data=jev_store.DATA):
    with jev._cache_lock(data / "responses.json"):
        return jev_store.export(data)


if __name__ == "__main__":
    print(f"Exported {len(run())} Jev analysis attempts")
