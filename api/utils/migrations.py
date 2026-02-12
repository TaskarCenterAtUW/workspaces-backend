import os
import subprocess
import sys


def run_migrations():
    """
    Runs Alembic database migrations using sys.executable and module execution.

    This method is more compatible with environments like Vercel where direct
    command execution might be restricted.
    """
    try:
        # Ensure the current directory is in the Python path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, current_dir)

        # Run migrations for each database
        for db_name in ("task", "osm"):
            print(f"Running migrations for '{db_name}' database...")
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-n", db_name, "upgrade", "head"],
                capture_output=True,
                text=True,
                check=True,
            )

            if result.stdout:
                print(f"Migration output ({db_name}):", result.stdout)

        print("Migrations completed successfully!")

    except subprocess.CalledProcessError as e:
        print(f"Migration failed. Error: {e}")
        print("Standard output:", e.stdout)
        print("Standard error:", e.stderr)
        raise
    except Exception as e:
        print(f"An error occurred while running migrations: {e}")
        raise
