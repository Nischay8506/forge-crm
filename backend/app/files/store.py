"""Stored files: raw emails and attachments (decision N9 A).

One interface over where the bytes live: a local folder in development and on a PC
(FILE_STORE_DIR, default backend/var/files), an S3-compatible bucket in deployment
(added with deployment; the interface is the same).

Every file is encrypted before it reaches the store, with AES-256-GCM under a key
of its own. That key is kept in the file's row, itself encrypted ("wrapped") under
EVIDENCE_KEYS, the same keys as the audit evidence (Step 4), so there is no extra
key to back up, and rotating the master key only re-wraps the small file keys
(`uv run python -m app.cli rotate-file-keys`). The file's key and its wrapping are
bound to its tenant and id, so bytes copied to another file fail to decrypt.

Files are never opened inside a browser page: the API only offers them as a
download (app/api/v1/enquiries.py). Removing a file (retention, erasure) deletes
the bytes and records when and why; the row stays.
"""

import base64
import hashlib
import os
import re
import secrets as _random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.evidence import EvidenceKeyError, ring
from app.config import get_settings
from app.core.errors import AppError
from app.models import StoredFile

VERSION = "v1"
KINDS = ("raw_email", "attachment", "upload")


class FileStoreError(Exception):
    """The store could not be reached, or a file failed its integrity check."""


class Store(Protocol):
    name: str

    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class LocalStore:
    """Files in a folder on this machine."""

    name = "local"

    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise FileStoreError("Bad storage key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + f".{_random.token_hex(4)}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as e:
            raise FileStoreError("The file is missing from the file store") from e

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass


def store() -> Store:
    return LocalStore(get_settings().file_store_dir)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _aad(kind: str, tenant_id: uuid.UUID, file_id: uuid.UUID) -> bytes:
    return f"{kind}:{tenant_id}:{file_id}".encode()


_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")


def safe_name(name: str | None, default: str = "file") -> str:
    """A filename without folders or characters that cause trouble on any system."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE.sub("_", base).strip(" .")
    return (base or default)[:200]


def _content_type(value: str | None) -> str:
    v = (value or "").split(";")[0].strip().lower()
    return v[:150] if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", v) else \
        "application/octet-stream"


def _wrap(tenant_id: uuid.UUID, file_id: uuid.UUID, key: bytes) -> tuple[str, str]:
    r = ring()
    nonce = _random.token_bytes(12)
    ct = AESGCM(r.keys[r.current]).encrypt(nonce, key, _aad("file_key", tenant_id, file_id))
    return r.current, ".".join((VERSION, _b64(nonce), _b64(ct)))


def _unwrap(f: StoredFile) -> bytes:
    r = ring()
    if f.key_id not in r.keys:
        raise EvidenceKeyError(f"file {f.id} was wrapped with key {f.key_id!r}, which is no "
                               "longer in EVIDENCE_KEYS")
    _, nonce, ct = f.wrapped_key.split(".")
    try:
        return AESGCM(r.keys[f.key_id]).decrypt(_unb64(nonce), _unb64(ct),
                                                _aad("file_key", f.tenant_id, f.id))
    except (InvalidTag, ValueError) as e:
        raise FileStoreError(f"the key of file {f.id} failed its integrity check") from e


async def save(session: AsyncSession, tenant_id: uuid.UUID, data: bytes, *, filename: str,
               content_type: str | None, kind: str, created_by: str) -> StoredFile:
    if kind not in KINDS:
        raise AppError("Unknown file kind")
    file_id = uuid.uuid4()
    key = AESGCM.generate_key(bit_length=256)
    nonce = _random.token_bytes(12)
    blob = nonce + AESGCM(key).encrypt(nonce, data, _aad("file", tenant_id, file_id))
    kid, wrapped = _wrap(tenant_id, file_id, key)
    now = datetime.now(UTC)
    s = store()
    storage_key = f"{tenant_id}/{now:%Y/%m}/{file_id}.bin"
    s.put(storage_key, blob)
    f = StoredFile(id=file_id, tenant_id=tenant_id, kind=kind, filename=safe_name(filename),
                   content_type=_content_type(content_type), size_bytes=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), store=s.name,
                   storage_key=storage_key, key_id=kid, wrapped_key=wrapped,
                   created_by=created_by, created_at=now)
    session.add(f)
    await session.flush()
    return f


def read(f: StoredFile) -> bytes:
    if f.removed_at is not None:
        raise AppError(f"This file was removed ({f.removed_reason or 'no reason given'})")
    blob = store().get(f.storage_key)
    try:
        data = AESGCM(_unwrap(f)).decrypt(blob[:12], blob[12:], _aad("file", f.tenant_id, f.id))
    except (InvalidTag, ValueError) as e:
        raise FileStoreError(f"file {f.id} failed its integrity check") from e
    if hashlib.sha256(data).hexdigest() != f.sha256:
        raise FileStoreError(f"file {f.id} does not match its fingerprint")
    return data


async def remove(session: AsyncSession, f: StoredFile, reason: str) -> bool:
    """Delete the bytes and record why. The row stays."""
    if f.removed_at is not None:
        return False
    store().delete(f.storage_key)
    f.removed_at, f.removed_reason = datetime.now(UTC), reason[:200]
    await session.flush()
    return True


async def rewrap_all() -> dict[str, object]:
    """Re-wrap every file's key under the current EVIDENCE_KEYS key."""
    from sqlalchemy import select

    from app.db import get_sessionmaker, tenant_session
    from app.models import Tenant

    r = ring()
    async with get_sessionmaker()() as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    rewrapped = 0
    for tid in tenant_ids:
        async with tenant_session(tid) as s:
            for f in await s.scalars(select(StoredFile).where(StoredFile.key_id != r.current)):
                key = _unwrap(f)
                f.key_id, f.wrapped_key = _wrap(tid, f.id, key)
                rewrapped += 1
    return {"current": r.current, "rewrapped": rewrapped}
