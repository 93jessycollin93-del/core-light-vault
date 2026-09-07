"""Symbol-pair rotation and router-scoped keying.

Rotation changes a router's binding vectors without touching the compressed
payload or its topology. It exists so a router can stage what it reveals:
each rotation supersedes the previous binding, so a captured old keyset stops
working the moment the rotation commits.

Atomicity rule. rotate_seed computes every new vector and the new SeedBinding
BEFORE mutating anything. If any step raises, neither the key store nor the
seed record is touched -- the caller is left exactly where they started.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .binder import bind_pair, compose_non_linear, constant_time_equal
from .pairs import KeyStore, Pair

_TAG_SYMBOL = b"seedling/qpdb/v1/rotate-symbol"
_TAG_SIG = b"seedling/qpdb/v1/rotate-sig"
_TAG_NONCE = b"seedling/qpdb/v1/nonce"


class RotationError(RuntimeError):
    """Rotation refused. Nothing was mutated."""


class ReplayError(RotationError):
    """A nonce already spent on this seed was presented again."""


@dataclass
class RotationPlan:
    """Which pairs to rotate, under which nonce."""

    indices: Sequence[int]
    nonce: bytes
    reason: str = "scheduled"
    requested_at: float = field(default_factory=time.time)

    def nonce_hash(self) -> str:
        return hashlib.blake2b(_TAG_NONCE + self.nonce, digest_size=16).hexdigest()


def transform_symbols(s1: str, s2: str, nonce: bytes) -> tuple[str, str]:
    """Deterministic symbol advance.

    Given the same (s1, s2, nonce) this always yields the same successor pair,
    so a rotation replays identically on any machine -- required for the
    rotation-delta restore path in the backup tiers.
    """
    def step(sym: str, salt: bytes) -> str:
        d = hashlib.blake2b(digest_size=8, person=_TAG_SYMBOL[:16])
        d.update(sym.encode())
        d.update(b"\x1f")
        d.update(nonce)
        d.update(salt)
        return "S" + d.hexdigest()

    return step(s1, b"\x01"), step(s2, b"\x02")


def sign_rotation(pair: Pair, nonce: bytes, router_private_key: bytes) -> str:
    """Authorship proof for one rotation step.

    HMAC stands in for the router's signing key here. Swap in Ed25519 when the
    router holds an asymmetric identity; the audit format is unchanged.
    """
    msg = b"|".join(
        [
            _TAG_SIG,
            nonce,
            pair.s1.encode(),
            pair.s2.encode(),
            repr(pair.m.timestamp).encode(),
            str(pair.m.rotation_index).encode(),
        ]
    )
    return hmac.new(router_private_key, msg, hashlib.blake2b).hexdigest()


def rotate_pair(pair: Pair, nonce: bytes, router_private_key: bytes) -> Pair:
    """Pair -> Pair'. Pure; commits nothing."""
    if not nonce:
        raise RotationError("rotation nonce must be non-empty")
    signature = sign_rotation(pair, nonce, router_private_key)
    new_s1, new_s2 = transform_symbols(pair.s1, pair.s2, nonce)
    nonce_hash = hashlib.blake2b(_TAG_NONCE + nonce, digest_size=16).hexdigest()
    return Pair(
        new_s1,
        new_s2,
        pair.m.__class__(
            router_id=pair.m.router_id,
            context_hash=pair.m.context_hash,
            direction=pair.m.direction,
            weight=pair.m.weight,
            timestamp=time.time(),
            version=pair.m.version,
            rotation_index=pair.m.rotation_index + 1,
            rotation_signature=signature,
            nonce_hash=nonce_hash,
        ),
    )


def validate_binding(seed_binding: bytes, provided_pairs: Sequence[Pair], topology_hash: str) -> bool:
    """Recompose from the presented pairs and compare in constant time."""
    try:
        recomposed = compose_non_linear(bind_pair(p, topology_hash) for p in provided_pairs)
    except ValueError:
        return False
    return constant_time_equal(recomposed, seed_binding)


def rotate_seed(
    store,
    seed_id: str,
    plan: RotationPlan,
    keystore: KeyStore,
    router_private_key: bytes,
    *,
    dna_validator=None,
) -> bytes:
    """Rotate the named pairs and atomically replace the seed's binding.

    Returns the new SeedBinding. Raises without mutating on any failure.
    """
    seed = store.get(seed_id)
    if seed is None:
        raise RotationError(f"unknown seed {seed_id!r}")
    if seed["state"] != "active":
        raise RotationError(f"seed {seed_id!r} is {seed['state']}, not rotatable")
    if dna_validator is not None and not dna_validator(seed):
        raise RotationError("Seedling DNA validation failed; refusing to rotate")

    nonce_hash = plan.nonce_hash()
    if nonce_hash in seed["metadata"].get("spent_nonces", []):
        raise ReplayError(f"nonce {nonce_hash[:12]} already spent on {seed_id}")

    current = keystore.all()
    for i in plan.indices:
        if not 0 <= i < len(current):
            raise RotationError(f"pair index {i} out of range for mode {len(current)}")

    # --- compute everything first; commit nothing yet ---------------------
    staged: List[Pair] = list(current)
    for i in plan.indices:
        staged[i] = rotate_pair(current[i], plan.nonce, router_private_key)
    new_binding = compose_non_linear(bind_pair(p, seed["topology_hash"]) for p in staged)

    # --- commit (single writer, single point of failure) ------------------
    # Snapshot first: seeds still sealed under the outgoing keyset (a dormant
    # nested router, a Tier-1 backup) must stay openable from the back pocket.
    epoch = keystore.checkpoint()
    for i, p in enumerate(staged):
        keystore.put(i, p)
    store.replace_binding(
        seed_id,
        new_binding,
        {
            "op": "rotate",
            "indices": list(plan.indices),
            "nonce_hash": nonce_hash,
            "reason": plan.reason,
            "rotation_index": max(p.m.rotation_index for p in staged),
            "key_epoch": epoch,
            "signatures": [staged[i].m.rotation_signature for i in plan.indices],
        },
    )
    return new_binding


def swap_pairs(
    store,
    seed_id: str,
    keystore: KeyStore,
    order: List[int],
    *,
    dna_validator=None,
) -> bytes:
    """Reorder the keyset and rebind.

    Because composition is order-sensitive, the old order stops validating the
    moment this commits -- which is the point: presentation order is itself a
    factor of the key.
    """
    seed = store.get(seed_id)
    if seed is None:
        raise RotationError(f"unknown seed {seed_id!r}")
    if seed["state"] != "active":
        raise RotationError(f"seed {seed_id!r} is {seed['state']}, not swappable")
    if dna_validator is not None and not dna_validator(seed):
        raise RotationError("Seedling DNA validation failed; refusing to swap")

    current = keystore.all()
    if sorted(order) != list(range(len(current))):
        raise RotationError("swap order must be a full permutation of pair indices")

    staged = [current[i] for i in order]
    new_binding = compose_non_linear(bind_pair(p, seed["topology_hash"]) for p in staged)

    epoch = keystore.checkpoint()
    keystore.reorder(order)
    store.replace_binding(
        seed_id, new_binding, {"op": "swap", "order": list(order), "key_epoch": epoch}
    )
    return new_binding
