from app.db import session_scope
from app.services.recompute import recompute_all
from app.config import settings

def main():
    print("Forcing recompute...")
    with session_scope() as session:
        revision = recompute_all(
            session,
            iterations=settings.simulation_iterations,
            seed=settings.simulation_seed
        )
    print(f"Recompute finished. New revision: {revision.id}")

if __name__ == "__main__":
    main()
