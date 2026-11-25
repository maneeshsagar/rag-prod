"""
Near-duplicate detection with MinHash + LSH (locality-sensitive hashing).

The idea in three steps:
1. Shingles: every run of 5 consecutive words in a document ("the leave policy applies to").
   Two documents are similar if they share many shingles (Jaccard similarity).
2. MinHash: 128 numbers per document. The chance that two documents have the same
   number at a position equals their Jaccard similarity, so comparing 128 numbers
   estimates similarity without comparing the full texts.
3. LSH bands: the 128 numbers are cut into 32 bands of 4. Documents that share any
   whole band become candidates (stored in Postgres, looked up with an index), so a
   new document is compared with a handful of others, not all 50k.
"""

import hashlib
import re
import zlib

import numpy as np

NUM_PERM = 128
BANDS, ROWS = 32, 4  # 32 * 4 = 128; candidates from ~40% similarity, then checked exactly
SHINGLE_WORDS = 5
MIN_SHINGLES = 20  # shorter texts are skipped: too little text to compare reliably

# Hash functions (a*x + b) mod p. With p = 2^31 - 1 and a, x < 2^31 the product wraps
# around p many times, which is what makes the 128 functions behave independently
# (and a*x < 2^62 never overflows 64-bit integers).
_PRIME = (1 << 31) - 1
_rng = np.random.default_rng(seed=1)  # fixed seed: fingerprints must be comparable forever
_A = _rng.integers(1, _PRIME, NUM_PERM, dtype=np.uint64)
_B = _rng.integers(0, _PRIME, NUM_PERM, dtype=np.uint64)

WORD = re.compile(r"\w+")


def shingles(text: str) -> set[str]:
    words = WORD.findall(text.lower())
    return {" ".join(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def minhash(text: str) -> list[int] | None:
    """128-number signature of the text, or None if the text is too short."""
    items = shingles(text)
    if len(items) < MIN_SHINGLES:
        return None
    # crc32 is stable across runs (Python's hash() is not).
    hashes = np.fromiter((zlib.crc32(s.encode()) % _PRIME for s in items), dtype=np.uint64)
    # One "random permutation" per row: (a*x + b) mod prime. Keep the minimum per row.
    # Done in blocks so a 300-page document never needs more than ~10 MB.
    signature = np.full(NUM_PERM, _PRIME, dtype=np.uint64)
    for start in range(0, len(hashes), 10_000):
        block = (np.outer(hashes[start:start + 10_000], _A) + _B) % _PRIME
        signature = np.minimum(signature, block.min(axis=0))
    return signature.tolist()


def band_keys(signature: list[int]) -> list[tuple[int, int]]:
    """(band number, bucket) pairs. Documents sharing any pair are candidates."""
    keys = []
    for band in range(BANDS):
        rows = signature[band * ROWS:(band + 1) * ROWS]
        digest = hashlib.blake2b(np.array(rows, dtype=np.uint64).tobytes(), digest_size=8).digest()
        keys.append((band, int.from_bytes(digest, "big") >> 1))  # >> 1: fits a signed BIGINT
    return keys


def similarity(a: list[int], b: list[int]) -> float:
    """Estimated Jaccard similarity (0..1) of two signatures."""
    return float(np.mean(np.array(a) == np.array(b)))
