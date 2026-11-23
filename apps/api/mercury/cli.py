import argparse
import secrets
from pathlib import Path

from alembic import command
from alembic.config import Config

from mercury.config import Settings
from mercury.db import Database
from mercury.seed import seed
from mercury.workflow import checkpointer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["configure", "migrate", "seed", "check"])
    args = parser.parse_args()
    if args.command == "configure":
        path = Path(".env")
        if path.exists():
            print(".env already exists; preserved.")
            return
        template = Path(".env.example").read_text(encoding="utf-8")
        template = template.replace(
            "MERCURY_JWT_SECRET=\n", "MERCURY_JWT_SECRET=" + secrets.token_urlsafe(48) + "\n"
        )
        template = template.replace("MERCURY_DEMO_PASSWORD=\n", "MERCURY_DEMO_PASSWORD=MercuryDemo-2026!\n")
        path.write_text(template, encoding="utf-8")
        print("Created ignored .env with a random signing secret. Demo-only password: MercuryDemo-2026!")
        return
    settings = Settings()
    db = Database(settings)
    if args.command == "migrate":
        command.upgrade(Config("alembic.ini"), "head")
        if db.engine.dialect.name == "postgresql":
            with checkpointer(db) as saver:
                saver.setup()
        print("Migrations complete.")
    elif args.command == "seed":
        seed(db)
        print("Idempotent synthetic seed complete; no real services contacted.")
    else:
        from sqlalchemy import text

        with db.session() as s:
            s.execute(text("SELECT 1"))
        print(
            f"Database reachable; environment={settings.env}, business={settings.business_mode}, model={settings.model_mode}"
        )


if __name__ == "__main__":
    main()
