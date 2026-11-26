"""Test environment for the publisher app.

Pytest loads this file before it imports the tests. The assignments below
therefore run before `publisher` is imported, and they replace any real
DATABASE_URL or keys already in the environment. load_dotenv() does not
override variables that are already set, so the developer's .env is ignored.
"""

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

# A real Fernet key (url-safe base64 of 32 bytes), not a production secret.
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["FERNET_KEY"] = Fernet.generate_key().decode("utf-8")

# Temporary SQLite file. Three slashes, then a normal path with forward slashes.
_handle, _db_path = tempfile.mkstemp(prefix="publisher-test-", suffix=".db")
os.close(_handle)
os.environ["DATABASE_URL"] = "sqlite:///" + Path(_db_path).as_posix()
