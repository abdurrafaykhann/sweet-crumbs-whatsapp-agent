"""One command to seed the demo data and start everything:  python -m app.run   (docker compose up does the same in containers)."""
import os


def prepare() -> bool:
    """Create the tables and load the spec's demo data if the database is empty. True when data was loaded."""
    from . import models as m
    from .database import get_engine
    from .seed import seed_if_empty
    m.Base.metadata.create_all(get_engine())
    return seed_if_empty()


def main():
    import uvicorn
    print("demo data loaded" if prepare() else "database already has data, keeping it")
    port = int(os.environ.get("PORT", "8000"))
    print(f"chat page:  http://localhost:{port}/ui    health: http://localhost:{port}/health    (dashboard: http://localhost:{port}/admin)")
    uvicorn.run("app.main:app", host=os.environ.get("HOST", "0.0.0.0"), port=port)


if __name__ == "__main__":
    main()
