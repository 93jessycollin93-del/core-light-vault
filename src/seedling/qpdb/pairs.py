"""Symbol-pair key units and the local (router-scoped) key store.

A Pair is a *relationship token*, not a raw secret: it names two symbols and
the metadata that situates them inside one router's topology. Pairs never
leave the local store; only the composed SeedBinding fingerprint is persisted
inside a seed.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, replace, asdict
from typing import Dict, Iterable, List, Optional

# Supported key modes. n=4 low friction, n=6 balanced, n=8 archival grade.
KEY_MODES = (4, 6, 8)


@dataclass(frozen=True)
class PairMeta:
    """Metadata carried by every symbol pair."""

    router_id: str
    context_hash: str
    direction: str = "forward"          # "forward" | "reverse"
    weight: float = 1.0
    timestamp: float = field(default_factory=time.time)
    version: int = 1
    rotation_index: int = 0
    rotation_signature: Optional[str] = None
    nonce_hash: Optional[str] = None

    def canonical(self) -> bytes:
        """Deterministic byte encoding of the binding-relevant fields.

        rotation_signature and timestamp are deliberately excluded: a pair must
        bind to the same vector on every machine that holds it, and signatures
        are per-rotation-event authorship proof, not binding material.
        """
        payload = {
            "router_id": self.router_id,
            "context_hash": self.context_hash,
            "direction": self.direction,
            "weight": repr(float(self.weight)),
            "version": self.version,
            "rotation_index": self.rotation_index,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Pair:
    """Pair = (S1, S2, M)."""

    s1: str
    s2: str
    m: PairMeta

    def canonical(self) -> bytes:
        return b"|".join([self.s1.encode(), self.s2.encode(), self.m.canonical()])

    def fingerprint(self) -> str:
        """Short, non-reversible label safe to show in a UI or audit log."""
        return hashlib.blake2b(self.canonical(), digest_size=8).hexdigest()

    def with_meta(self, **kw) -> "Pair":
        return Pair(self.s1, self.s2, replace(self.m, **kw))

    def to_dict(self) -> dict:
        return {"s1": self.s1, "s2": self.s2, "m": asdict(self.m)}

    @staticmethod
    def from_dict(d: dict) -> "Pair":
        return Pair(d["s1"], d["s2"], PairMeta(**d["m"]))


def context_hash(*parts: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    for p in parts:
        h.update(p.encode())
        h.update(b"\x1f")
    return h.hexdigest()


def generate_pairs(router_id: str, n: int, *, rng: Optional[object] = None) -> List[Pair]:
    """Generate n fresh pairs for a router. n must be a supported key mode."""
    if n not in KEY_MODES:
        raise ValueError(f"unsupported key mode {n}; expected one of {KEY_MODES}")
    randbytes = rng.randbytes if rng is not None else os.urandom
    pairs: List[Pair] = []
    for i in range(n):
        s1 = "S" + randbytes(8).hex()
        s2 = "S" + randbytes(8).hex()
        pairs.append(
            Pair(s1, s2, PairMeta(router_id=router_id, context_hash=context_hash(router_id, str(i))))
        )
    return pairs


class KeyStore:
    """Router-scoped local pair store.

    SECURITY BOUNDARY. This reference store keeps pairs in process memory only.
    A production deployment MUST back it with a hardware-bound store (TPM,
    Windows CNG / DPAPI, Secure Enclave) or an OS keyring. Raw pairs are never
    written into seed metadata by any code path in this package.
    """

    def __init__(self, router_id: str, back_pocket_depth: int = 8):
        self.router_id = router_id
        self._pairs: List[Pair] = []
        self._sealed = False
        # Tier-1 back pocket. Symbol transforms are one-way, so a rotated
        # keyset cannot be walked backwards -- a snapshot taken *before* each
        # rotation is the only way to open a seed sealed under an older epoch
        # (a dormant nested router, an older Tier-1 snapshot). Bounded ring:
        # past back_pocket_depth epochs the oldest snapshot is dropped and
        # seeds sealed under it are unopenable by design.
        self.epoch = 0
        self.back_pocket_depth = back_pocket_depth
        self._history: Dict[int, List[Pair]] = {}

    # -- lifecycle -----------------------------------------------------
    def load(self, pairs: Iterable[Pair]) -> None:
        pairs = list(pairs)
        if len(pairs) not in KEY_MODES:
            raise ValueError(f"key mode must be one of {KEY_MODES}, got {len(pairs)}")
        self._pairs = pairs

    def get(self, index: int) -> Pair:
        self._assert_open()
        return self._pairs[index]

    def all(self) -> List[Pair]:
        self._assert_open()
        return list(self._pairs)

    def put(self, index: int, pair: Pair) -> None:
        self._assert_open()
        self._pairs[index] = pair

    def reorder(self, order: List[int]) -> None:
        self._assert_open()
        if sorted(order) != list(range(len(self._pairs))):
            raise ValueError("reorder requires a full permutation of pair indices")
        self._pairs = [self._pairs[i] for i in order]

    def mode(self) -> int:
        return len(self._pairs)

    # -- back pocket ---------------------------------------------------
    def checkpoint(self) -> int:
        """Snapshot the current keyset and advance the epoch.

        Called by rotate_seed and swap_pairs before they commit, so the epoch
        recorded in a seed always names the keyset that actually sealed it.
        """
        self._assert_open()
        self._history[self.epoch] = list(self._pairs)
        self.epoch += 1
        if len(self._history) > self.back_pocket_depth:
            del self._history[min(self._history)]
        return self.epoch

    def at(self, epoch: int) -> List[Pair]:
        """The keyset as it stood at `epoch`. Raises once it has aged out."""
        self._assert_open()
        if epoch == self.epoch:
            return list(self._pairs)
        if epoch in self._history:
            return list(self._history[epoch])
        raise KeyError(
            f"epoch {epoch} is outside the back pocket "
            f"(held: {sorted(self._history) + [self.epoch]})"
        )

    def seal(self) -> None:
        """Retire the keyset. Pairs are dropped; the store becomes unusable."""
        self._pairs = []
        self._sealed = True

    def _assert_open(self) -> None:
        if self._sealed:
            raise RuntimeError("key store is sealed (keyset retired)")

    # -- redaction -----------------------------------------------------
    def fingerprints(self) -> List[str]:
        """Display-safe view. Never returns symbol material."""
        return [p.fingerprint() for p in self._pairs]

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"<KeyStore router={self.router_id!r} mode={len(self._pairs)} REDACTED>"
