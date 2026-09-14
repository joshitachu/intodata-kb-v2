---
name: content-hash-cache
description: Cache expensive per-file work by SHA-256 of the file contents so results survive renames and self-invalidate on edit. Use when adding or debugging caching for scans, parsing, extraction, or ingestion.
---

# Content-Hash File Cache

Key the cache on what the file *is*, not where it lives. A move or rename is then a
cache hit, and an edit is an automatic miss. No index file to keep in sync.

Relevant here to the intodoc scan cache (`knowledge_base/intodoc-output/`) and to RAG
ingestion dedup, where the same client file is seen across repeated runs.

## The hash

```python
import hashlib
from pathlib import Path

_CHUNK = 65536  # hash in chunks; client repos contain files too large to slurp

def file_hash(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            sha.update(chunk)
    return sha.hexdigest()
```

## The part that bites: the key is not just the content

A cache keyed on file content alone is **wrong** whenever the output also depends on
configuration. In this repo it always does -- the same Talend job scanned under a
different pack, platform, or scanner version produces different facts. Key on both:

```python
def cache_key(path: Path, *, scanner_version: str, pack_id: str, platform: str) -> str:
    h = hashlib.sha256()
    h.update(file_hash(path).encode())
    h.update(f"{scanner_version}|{pack_id}|{platform}".encode())
    return h.hexdigest()
```

Omit this and a pack change silently serves stale facts, which reads as "the scanner
ignored my change" -- an expensive bug to chase because nothing errors.

## Storage

One file per entry, named by key. O(1) lookup, no index, no lock.

```python
def read_cache(cache_dir: Path, key: str) -> dict | None:
    f = cache_dir / f"{key}.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, KeyError):
        return None  # corruption is a miss, never a crash


def write_cache(cache_dir: Path, key: str, payload: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache_dir / f".{key}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache_dir / f"{key}.json")  # atomic; a killed run leaves no half file
```

## Keep the cache out of the worker

The function that does the work must not know a cache exists. Wrap it.

```python
def extract_cached(path: Path, *, use_cache: bool = True,
                   cache_dir: Path = Path(".cache"), **cfg) -> Document:
    if not use_cache:
        return extract(path)                      # pure, cache-unaware
    key = cache_key(path, **cfg)
    if (hit := read_cache(cache_dir, key)) is not None:
        logger.info("cache hit %s (%s)", path.name, key[:12])
        return Document.model_validate(hit)
    logger.info("cache miss %s (%s)", path.name, key[:12])
    doc = extract(path)
    write_cache(cache_dir, key, doc.model_dump())
    return doc
```

## Rules

- Hash content, never paths.
- Fold every input that changes the output into the key -- config, version, pack.
- Write through a temp file and `replace()` so a crash cannot leave a partial entry.
- Treat unreadable or corrupt entries as misses.
- Log hit/miss with a truncated key; a cache you cannot observe is a cache you cannot debug.

## Do not use it when

Results must be fresh every run, entries would be very large (stream instead), or the
output depends on something you cannot enumerate into the key.
