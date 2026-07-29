"""core.py — the ONE blob layer (S3 in prod, local filesystem in dev).

WHY THIS EXISTS
---------------
Relational state moved to RDS (`db/`), which unpinned the ECS service from one
task. Blobs were the other half of the same problem: the 3-D pipeline writes an
uploaded photo, intermediate images and the finished GLB under the task's data
directory. With more than one task that breaks in a way users see immediately —
task A runs the reconstruction, the browser polls or fetches the model, the ALB
routes it to task B, and the GLB is simply not there. A 404 for a model that
demonstrably exists.

So blobs go to **S3**, addressed by key rather than by path:

    NXR_S3_BUCKET set    ->  S3 (or any S3-compatible endpoint, e.g. MinIO)
    unset                ->  local filesystem under NXR_DATA_DIR/blobs

Both backends implement the same tiny interface, so nothing above this module
knows which one is live.

WHAT THIS IS *NOT*
------------------
Not a filesystem. There is no seek, no partial write, no directory rename. The
3-D stages still write to real local files while they work — mid-pipeline scratch
is genuinely ephemeral and streaming every trimesh export through S3 would be
slower and more fragile. What changes is that finished artifacts are PUBLISHED
here, and reads resolve here first. Local disk becomes a cache, not the record.

KEYS
----
Forward-slash separated, no leading slash, e.g.::

    threed/jobs/8f2a1c/artifacts/export/model.glb

Keys are validated (`_safe_key`) because several of them are built from
client-supplied paths; `..` traversal must not be able to escape the prefix, on
either backend.
"""
from __future__ import annotations

import mimetypes
import os
import posixpath
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

from paths import DATA_DIR

S3 = "s3"
LOCAL = "local"


def bucket() -> str:
    return (os.environ.get("NXR_S3_BUCKET") or "").strip()


def backend() -> str:
    return S3 if bucket() else LOCAL


def is_s3() -> bool:
    return backend() == S3


def key_prefix() -> str:
    """Optional prefix inside the bucket, so one bucket can host several
    environments (`staging/`, `prod/`) without them colliding."""
    p = (os.environ.get("NXR_S3_PREFIX") or "").strip().strip("/")
    return f"{p}/" if p else ""


def local_root() -> Path:
    """Where the local backend keeps blobs (also the S3 backend's scratch)."""
    root = os.environ.get("NXR_BLOB_DIR")
    return Path(root) if root else (DATA_DIR / "blobs")


class BlobNotFound(KeyError):
    """No object at that key."""


def _safe_key(key: str) -> str:
    """Normalise a key and refuse anything that escapes the root.

    These keys carry client-supplied path fragments (the artifact route takes
    `{path:path}`), so this is a security boundary, not tidiness.
    """
    k = str(key).replace("\\", "/").lstrip("/")
    normalised = posixpath.normpath(k)
    if normalised in (".", "/") or normalised.startswith("../") or normalised == "..":
        raise ValueError(f"unsafe blob key: {key!r}")
    return normalised


def content_type_for(key: str) -> str:
    """Best-effort MIME type. GLB is not in Python's table on every platform,
    and serving it as octet-stream makes some three.js loaders refuse it."""
    if key.endswith(".glb"):
        return "model/gltf-binary"
    if key.endswith(".gltf"):
        return "model/gltf+json"
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


# ── Backends ────────────────────────────────────────────────────────────
class ObjectStore:
    """put / get / exists / delete / list, and a URL when the backend has one."""

    name = "abstract"

    def put(self, key: str, data: bytes, *, content_type: str = "") -> str: ...
    def put_file(self, key: str, path: Path, *, content_type: str = "") -> str: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str = "") -> Iterator[str]: ...

    def download(self, key: str, dest: Path) -> Path:
        """Materialise a blob as a local file (for code that needs a real path)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.get(key))
        return dest

    def url(self, key: str, *, expires: int = 3600) -> str | None:
        """A directly-fetchable URL, or None if the backend has none (local),
        in which case the caller should stream the bytes itself."""
        return None

    def stats(self) -> dict:
        return {"backend": self.name}


class LocalObjectStore(ObjectStore):
    """Filesystem-backed. The default for local dev and the single-task deploy —
    identical semantics, no AWS account needed."""

    name = LOCAL

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else local_root()

    def _path(self, key: str) -> Path:
        return self.root / _safe_key(key)

    def put(self, key: str, data: bytes, *, content_type: str = "") -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if Path(path).resolve() != p.resolve():
            shutil.copyfile(path, p)
        return key

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise BlobNotFound(key)
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def list(self, prefix: str = "") -> Iterator[str]:
        base = self.root / _safe_key(prefix) if prefix else self.root
        if not base.exists():
            return
        for p in sorted(base.rglob("*")):
            if p.is_file():
                yield str(p.relative_to(self.root)).replace("\\", "/")

    def local_path(self, key: str) -> Path | None:
        p = self._path(key)
        return p if p.is_file() else None

    def stats(self) -> dict:
        return {"backend": self.name, "root": str(self.root)}


class S3ObjectStore(ObjectStore):
    """S3, or anything speaking its API (MinIO locally — same code path)."""

    name = S3

    def __init__(self, bucket_name: str, prefix: str = ""):
        self.bucket = bucket_name
        self.prefix = prefix
        self._client = None
        self._lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import boto3
                    from botocore.config import Config
                    self._client = boto3.client(
                        "s3",
                        endpoint_url=os.environ.get("NXR_S3_ENDPOINT_URL") or None,
                        region_name=os.environ.get("AWS_REGION")
                        or os.environ.get("AWS_DEFAULT_REGION") or None,
                        config=Config(
                            retries={"max_attempts": 3, "mode": "standard"},
                            # MinIO and some gateways only support path style.
                            s3={"addressing_style": os.environ.get(
                                "NXR_S3_ADDRESSING_STYLE", "auto")},
                        ),
                    )
        return self._client

    def _k(self, key: str) -> str:
        return self.prefix + _safe_key(key)

    def put(self, key: str, data: bytes, *, content_type: str = "") -> str:
        self.client.put_object(
            Bucket=self.bucket, Key=self._k(key), Body=data,
            ContentType=content_type or content_type_for(key))
        return key

    def put_file(self, key: str, path: Path, *, content_type: str = "") -> str:
        self.client.upload_file(
            str(path), self.bucket, self._k(key),
            ExtraArgs={"ContentType": content_type or content_type_for(key)})
        return key

    def get(self, key: str) -> bytes:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._k(key))
            return obj["Body"].read()
        except Exception as e:
            if _is_missing(e):
                raise BlobNotFound(key) from e
            raise

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception as e:
            if _is_missing(e):
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._k(key))

    def list(self, prefix: str = "") -> Iterator[str]:
        token = None
        full = self.prefix + (_safe_key(prefix) if prefix else "")
        while True:
            kw = {"Bucket": self.bucket, "Prefix": full}
            if token:
                kw["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kw)
            for item in resp.get("Contents", []):
                yield item["Key"][len(self.prefix):]
            if not resp.get("IsTruncated"):
                return
            token = resp.get("NextContinuationToken")

    def url(self, key: str, *, expires: int = 3600) -> str | None:
        """A presigned GET. Lets a browser pull a 20 MB GLB straight from S3
        instead of streaming it through the API task."""
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": self._k(key)},
                ExpiresIn=expires)
        except Exception:
            return None

    def stats(self) -> dict:
        return {"backend": self.name, "bucket": self.bucket,
                "prefix": self.prefix or None,
                "endpoint": os.environ.get("NXR_S3_ENDPOINT_URL") or "aws"}


def _is_missing(exc: Exception) -> bool:
    """Is this 'no such key/bucket' rather than a real failure?"""
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return str(code) in {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}


# ── Singleton ───────────────────────────────────────────────────────────
_store: ObjectStore | None = None
_store_backend: str = ""
_store_lock = threading.Lock()


def get_store() -> ObjectStore:
    """The process-wide object store, rebuilt if the backend config changes."""
    global _store, _store_backend
    want = backend()
    if _store is not None and _store_backend == want:
        return _store
    with _store_lock:
        if _store is not None and _store_backend == want:
            return _store
        _store = S3ObjectStore(bucket(), key_prefix()) if want == S3 \
            else LocalObjectStore()
        _store_backend = want
        return _store


def reset_store(new: ObjectStore | None = None) -> None:
    """Replace or clear the singleton (tests, or after changing config)."""
    global _store, _store_backend
    with _store_lock:
        _store = new
        _store_backend = backend() if new is not None else ""


# ── Diagnostics ─────────────────────────────────────────────────────────
_PROBE_KEY = "_healthcheck/probe"


def ping() -> tuple[bool, str]:
    """(reachable, detail). Writes and deletes a tiny probe object, because a
    bucket you can list but not write to is not a working blob store — and that
    is the usual IAM mistake. Never raises."""
    try:
        s = get_store()
        s.put(_PROBE_KEY, b"ok", content_type="text/plain")
        ok = s.get(_PROBE_KEY) == b"ok"
        s.delete(_PROBE_KEY)
        return (ok, "connected" if ok else "readback mismatch")
    except Exception as e:
        return False, str(e)


def info() -> dict:
    """Backend summary for /health. No credentials."""
    out = dict(get_store().stats())
    ok, detail = ping()
    out["status"] = "connected" if ok else "unreachable"
    if not ok:
        out["detail"] = detail
    return out


def log_posture() -> None:
    """Startup line, next to [auth]/[db]/[bus]. ASCII only (CloudWatch)."""
    if is_s3():
        s = get_store().stats()
        print(f"[blobs] S3 - bucket={s['bucket']} prefix={s['prefix'] or '/'} "
              f"endpoint={s['endpoint']}", flush=True)
    else:
        print(f"[blobs] local filesystem under {local_root()} - per-task state. "
              "Fine for one task; with more than one task a model generated on "
              "one task 404s on the others. Set NXR_S3_BUCKET before scaling out.",
              flush=True)
