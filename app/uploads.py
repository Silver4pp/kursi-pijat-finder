"""Upload path helpers shared by the web layer and domain services.

Storage names are generated server-side, but the database is still untrusted
input at read time: a bad row must never be able to point outside the upload
directory. Every read and delete goes through upload_path().
"""

import re
from pathlib import Path

from fastapi import HTTPException

from .db import UPLOAD_DIR

# Generated names look like "staged-<32 hex>.jpg". Anything else is rejected.
_STORAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,200}\.[a-z0-9]{1,5}$")


def is_safe_storage_name(name: str | None) -> bool:
    return bool(name) and bool(_STORAGE_NAME.fullmatch(name))


def upload_path(storage_name: str | None) -> Path:
    """Resolve a stored filename, refusing anything outside UPLOAD_DIR."""
    if not is_safe_storage_name(storage_name):
        raise HTTPException(404, "Foto tidak ditemukan")
    path = (UPLOAD_DIR / storage_name).resolve()
    if path.parent != UPLOAD_DIR.resolve():
        raise HTTPException(404, "Foto tidak ditemukan")
    return path
