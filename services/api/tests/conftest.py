import os

# This file is loaded before test modules, so application imports never point at
# the developer's PostgreSQL database and tests do not depend on Docker.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
