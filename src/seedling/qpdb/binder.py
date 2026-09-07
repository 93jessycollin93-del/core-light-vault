"""QPDB binding: deterministic, non-invertible, order-sensitive.

Two primitives:

  bind_pair(pair, topology_hash) -> 32-byte binding vector
      Deterministic. Same pair + same topology => same vector, on every
      machine, forever. Non-invertible: recovering (S1, S2) from a vector
      requires inverting BLAKE2b, so the vector is safe to compare but never
      safe to treat as a secret carrier.

  compose_non_linear(vectors) -> 32-byte SeedBinding
      A hash chain. Order-sensitive by construction: compose([A,B]) and
      compose([B,A]) differ with overwhelming probability, because each step
      folds the running state, the incoming vector, and the step index
      together. This is what makes swap detection work.

WHAT THIS IS NOT. A SeedBinding authorizes a rehydration; it does not encrypt
the sphere blob. Anyone with filesystem access to a seed can read its payload
regardless of whether they hold the pairs. If a seed must be confidential at
rest, encrypt the blob (AES-256-GCM) with a key derived from the pairs via a
memory-hard KDF, and keep the binding as the integrity check it is.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Iterable, List

from .pairs import Pair

# Domain separation tags. Changing any of these is a breaking format change.
_TAG_TOKEN = b"seedling/qpdb/v1/token"
_TAG_VECTOR = b"seedling/qpdb/v1/vector"
_TAG_COMPOSE = b"seedling/qpdb/v1/compose"
_TAG_INIT = b"seedling/qpdb/v1/init"

VECTOR_SIZE = 32
INITIAL_STATE = hashlib.blake2b(_TAG_INIT, digest_size=VECTOR_SIZE).digest()


def _h(tag: bytes, *parts: bytes) -> bytes:
    d = hashlib.blake2b(digest_size=VECTOR_SIZE, person=tag[:16])
    for p in parts:
        d.update(len(p).to_bytes(4, "big"))  # length-prefixed: no concat ambiguity
        d.update(p)
    return d.digest()


def pattern_transform(token: bytes) -> bytes:
    """Non-invertible mapping token -> binding vector.

    Two rounds with distinct domain tags. Deterministic, no randomness, no
    machine-specific state, so a vector computed on Windows equals the vector
    computed in CI.
    """
    a = _h(_TAG_VECTOR, token)
    return _h(_TAG_VECTOR, a, token)


def bind_pair(pair: Pair, topology_hash: str) -> bytes:
    """V = bind_pair(Pair, topology_hash)."""
    token = _h(
        _TAG_TOKEN,
        pair.s1.encode(),
        pair.s2.encode(),
        pair.m.canonical(),
        topology_hash.encode(),
    )
    return pattern_transform(token)


def non_commutative_combine(state: bytes, vector: bytes, index: int) -> bytes:
    """One fold of the composition chain.

    The step index is mixed in, so a vector contributes differently at
    position 0 than at position 3. That is the property swap detection rests
    on -- reordering pairs changes every downstream fold.
    """
    return _h(_TAG_COMPOSE, state, vector, index.to_bytes(4, "big"))


def compose_non_linear(vectors: Iterable[bytes]) -> bytes:
    """SeedBinding = compose_non_linear(V1..Vn), order sensitive."""
    state = INITIAL_STATE
    count = 0
    for i, v in enumerate(vectors):
        if len(v) != VECTOR_SIZE:
            raise ValueError(f"binding vector {i} has length {len(v)}, expected {VECTOR_SIZE}")
        state = non_commutative_combine(state, v, i)
        count += 1
    if count == 0:
        raise ValueError("compose_non_linear requires at least one vector")
    # Bind the arity too, so an n=4 binding can never collide with an n=8 one.
    return _h(_TAG_COMPOSE, state, count.to_bytes(4, "big"))


def bind_all(pairs: List[Pair], topology_hash: str) -> bytes:
    """Convenience: bind an ordered pair list straight to a SeedBinding."""
    return compose_non_linear(bind_pair(p, topology_hash) for p in pairs)


def constant_time_equal(a: bytes, b: bytes) -> bool:
    """Compare bindings without leaking a timing oracle on the prefix."""
    return hmac.compare_digest(a, b)
