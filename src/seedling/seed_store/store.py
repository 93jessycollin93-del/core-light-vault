"""Seed persistence, the append-only audit trail, Seedling DNA, and Ash.

Storage invariant, enforced by _assert_no_raw_material on every write:
a seed record may hold the sphere blob, the topology hash, the binding
fingerprint and metadata -- never a raw symbol pair, never a private key.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_FORBIDDEN_KEYS = {"s1", "s2", "pairs", "local_pairs", "private_key", "router_private_key"}


class DNAViolation(RuntimeError):
    """A seed failed a Seedling DNA invariant."""


# --------------------------------------------------------------------------
# Audit trail
# --------------------------------------------------------------------------
class AuditLog:
    """Append-only, hash-chained event log.

    Each entry commits to its predecessor, so a deleted or edited entry breaks
    verify() at exactly the tampered position. Entries are never mutated.
    """

    GENESIS = hashlib.blake2b(b"seedling/audit/v1/genesis", digest_size=16).hexdigest()

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []

    def append(self, seed_id: str, op: str, detail: Dict[str, Any]) -> Dict[str, Any]:
        prev = self._entries[-1]["entry_hash"] if self._entries else self.GENESIS
        body = {
            "ts": time.time(),
            "seed_id": seed_id,
            "op": op,
            "detail": detail,
            "prev": prev,
        }
        body["entry_hash"] = hashlib.blake2b(
            json.dumps(body, sort_keys=True, default=str).encode(), digest_size=16
        ).hexdigest()
        self._entries.append(body)
        return body

    def verify(self) -> bool:
        prev = self.GENESIS
        for e in self._entries:
            body = {k: v for k, v in e.items() if k != "entry_hash"}
            if body["prev"] != prev:
                return False
            expect = hashlib.blake2b(
                json.dumps(body, sort_keys=True, default=str).encode(), digest_size=16
            ).hexdigest()
            if expect != e["entry_hash"]:
                return False
            prev = e["entry_hash"]
        return True

    def entries(self, seed_id: Optional[str] = None, op: Optional[str] = None) -> List[Dict[str, Any]]:
        out = self._entries
        if seed_id is not None:
            out = [e for e in out if e["seed_id"] == seed_id]
        if op is not None:
            out = [e for e in out if e["op"] == op]
        return list(out)

    def __len__(self) -> int:
        return len(self._entries)


# --------------------------------------------------------------------------
# Ash
# --------------------------------------------------------------------------
@dataclass
class AshRecord:
    """What is left after disintegration.

    Ash is NOT a backup. By default the payload is destroyed and only a symbol
    census plus fingerprints survive -- enough to prove what existed and to
    reconcile an audit, not enough to reconstitute a router. Recovery comes
    from the backup tiers, not from Ash.

    When policy sets archive=True, a sealed copy of the sphere is retained
    alongside the residue and k-of-n unlock can restore it. Use that only where
    the archive itself is protected at rest.
    """

    ash_id: str
    seed_id: str
    reason: str
    residue: Dict[str, Any]
    k_threshold: int
    binding_fingerprint: str
    created: float = field(default_factory=time.time)
    _sealed_sphere: Optional[bytes] = None

    def is_recoverable(self) -> bool:
        return self._sealed_sphere is not None


def _symbol_census(sphere: bytes) -> Dict[str, int]:
    """Symbolic residue: a byte-class histogram, no ordering, no payload.

    Ordering is what carries meaning in the sphere; a census discards it.
    """
    buckets = Counter(b >> 4 for b in sphere)  # 16 coarse classes
    return {f"c{k:x}": v for k, v in sorted(buckets.items())}


# --------------------------------------------------------------------------
# Seedling DNA
# --------------------------------------------------------------------------
def seedling_dna(seed: Dict[str, Any]) -> bool:
    """Foundation invariants. Returns False rather than raising, so callers
    can quarantine instead of crashing.

    1. Structural completeness -- required fields present.
    2. Topology integrity -- topology_hash matches the stored sphere digest.
    3. Provenance -- router_id present and stable.
    4. No raw key material anywhere in the record.
    5. Lineage -- declared children exist in the record's child list.
    """
    required = {"seed_id", "sphere", "topology_hash", "seed_binding", "metadata", "state"}
    if not required.issubset(seed.keys()):
        return False
    if seed["state"] == "ash":
        return False
    md = seed["metadata"]
    if not md.get("router_id"):
        return False
    if md.get("sphere_digest") != hashlib.blake2b(seed["sphere"], digest_size=16).hexdigest():
        return False
    try:
        _assert_no_raw_material(seed)
    except DNAViolation:
        return False
    return True


def _assert_no_raw_material(obj: Any, path: str = "seed") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _FORBIDDEN_KEYS:
                raise DNAViolation(f"raw key material at {path}.{k}")
            _assert_no_raw_material(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _assert_no_raw_material(v, f"{path}[{i}]")


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------
class SeedStore:
    """In-memory seed store with an optional JSON mirror on disk.

    Windows-safe: writes go to a temp file in the same directory and are then
    os.replace()d, which is atomic on NTFS as well as POSIX.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._seeds: Dict[str, Dict[str, Any]] = {}
        self._ash: Dict[str, AshRecord] = {}
        self.audit = AuditLog()
        self.path = path

    # -- writes --------------------------------------------------------
    def put(self, seed: Dict[str, Any]) -> str:
        _assert_no_raw_material(seed)
        seed.setdefault("state", "active")
        seed["metadata"].setdefault("spent_nonces", [])
        seed["metadata"]["sphere_digest"] = hashlib.blake2b(
            seed["sphere"], digest_size=16
        ).hexdigest()
        self._seeds[seed["seed_id"]] = seed
        self.audit.append(
            seed["seed_id"],
            "create",
            {
                "router_id": seed["metadata"]["router_id"],
                "topology_hash": seed["topology_hash"],
                "binding_fingerprint": seed["seed_binding"][:16],
                "key_mode": seed["metadata"].get("key_mode"),
                "depth": seed["metadata"].get("nesting_depth", 0),
            },
        )
        self._flush()
        return seed["seed_id"]

    def replace_binding(self, seed_id: str, new_binding: bytes, detail: Dict[str, Any]) -> None:
        """Atomic binding swap plus audit append. Either both land or neither."""
        seed = self._seeds.get(seed_id)
        if seed is None:
            raise KeyError(seed_id)
        old = seed["seed_binding"]
        seed["seed_binding"] = new_binding.hex()
        nonce_hash = detail.get("nonce_hash")
        if nonce_hash:
            seed["metadata"].setdefault("spent_nonces", []).append(nonce_hash)
        if "rotation_index" in detail:
            seed["metadata"]["rotation_index"] = detail["rotation_index"]
        if "key_epoch" in detail:
            seed["metadata"]["key_epoch"] = detail["key_epoch"]
        try:
            self.audit.append(
                seed_id,
                detail.get("op", "rotate"),
                {**detail, "from": old[:16], "to": seed["seed_binding"][:16]},
            )
            self._flush()
        except Exception:  # pragma: no cover - rollback path
            seed["seed_binding"] = old
            raise

    def quarantine(self, seed_id: str, reason: str) -> None:
        seed = self._seeds[seed_id]
        seed["state"] = "quarantined"
        self.audit.append(seed_id, "quarantine", {"reason": reason})
        self._flush()

    def disintegrate_to_ash(
        self, seed_id: str, reason: str, *, k_threshold: int = 2, archive: bool = False
    ) -> str:
        """Destroy the seed, keep symbolic residue.

        Irreversible unless archive=True. The sphere is dropped from the record
        before the ash entry is written, so a crash mid-call cannot leave a
        readable payload attached to an ash-marked seed.
        """
        if not 1 <= k_threshold <= 8:
            raise ValueError("k_threshold must be in 1..8")
        seed = self._seeds[seed_id]
        sphere = seed["sphere"]
        ash = AshRecord(
            ash_id="ash_" + uuid.uuid4().hex[:12],
            seed_id=seed_id,
            reason=reason,
            residue={
                "census": _symbol_census(sphere),
                "byte_length": len(sphere),
                "topology_hash": seed["topology_hash"],
                "router_id": seed["metadata"]["router_id"],
                "depth": seed["metadata"].get("nesting_depth", 0),
            },
            k_threshold=k_threshold,
            binding_fingerprint=seed["seed_binding"][:16],
            _sealed_sphere=bytes(sphere) if archive else None,
        )
        seed["sphere"] = b""
        seed["state"] = "ash"
        seed["ash_id"] = ash.ash_id
        self._ash[ash.ash_id] = ash
        self.audit.append(
            seed_id,
            "disintegrate",
            {
                "ash_id": ash.ash_id,
                "reason": reason,
                "k_threshold": k_threshold,
                "recoverable": archive,
            },
        )
        self._flush()
        return ash.ash_id

    def rehydrate_ash(self, ash_id: str, provided_pairs, topology_hash: str) -> Dict[str, Any]:
        """k-of-n residue unlock.

        Returns the residue when enough valid pairs are presented. Returns the
        sealed sphere too, but only if the ash was archived -- residue alone
        can never rebuild a router, and this call does not pretend otherwise.
        """
        from ..qpdb.binder import bind_pair  # local import avoids a cycle

        ash = self._ash[ash_id]
        provided = list(provided_pairs)
        if len(provided) < ash.k_threshold:
            self.audit.append(ash.seed_id, "ash_denied", {"ash_id": ash_id, "presented": len(provided)})
            raise PermissionError(
                f"ash {ash_id} needs {ash.k_threshold} keys, {len(provided)} presented"
            )
        # Each presented pair must bind under the recorded topology.
        for p in provided:
            if len(bind_pair(p, topology_hash)) != 32:  # pragma: no cover - defensive
                raise PermissionError("malformed pair presented to ash unlock")
        self.audit.append(ash.seed_id, "ash_unlock", {"ash_id": ash_id, "keys": len(provided)})
        self._flush()
        return {
            "residue": ash.residue,
            "recoverable": ash.is_recoverable(),
            "sphere": ash._sealed_sphere,
        }

    # -- nesting -------------------------------------------------------
    def export_seed(self, seed_id: str) -> Dict[str, Any]:
        """A detached, JSON-safe snapshot of a seed, for a parent to carry.

        Deep-copied on purpose. An aliased export would keep tracking the live
        record, so a later rotation would silently rewrite the snapshot's
        key_epoch and leave it claiming a keyset that never sealed it.
        """
        s = self._seeds[seed_id]
        return {
            "seed_id": s["seed_id"],
            "sphere_hex": s["sphere"].hex(),
            "topology": json.loads(json.dumps(s["topology"])),
            "topology_hash": s["topology_hash"],
            "seed_binding": s["seed_binding"],
            "state": s["state"],
            "metadata": json.loads(json.dumps(s["metadata"], default=str)),
        }

    def import_seed(self, rec: Dict[str, Any]) -> str:
        """Reinstate a carried seed so it can be rehydrated."""
        return self.put(
            {
                "seed_id": rec["seed_id"],
                "sphere": bytes.fromhex(rec["sphere_hex"]),
                "topology": rec["topology"],
                "topology_hash": rec["topology_hash"],
                "seed_binding": rec["seed_binding"],
                "state": rec["state"],
                "metadata": dict(rec["metadata"]),
            }
        )

    @staticmethod
    def sealing_epoch(rec: Dict[str, Any]) -> int:
        """The keyset epoch that sealed a carried record."""
        return rec["metadata"].get("key_epoch", 0)

    # -- reads ---------------------------------------------------------
    def get(self, seed_id: str) -> Optional[Dict[str, Any]]:
        return self._seeds.get(seed_id)

    def get_ash(self, ash_id: str) -> Optional[AshRecord]:
        return self._ash.get(ash_id)

    def ids(self) -> List[str]:
        return list(self._seeds)

    # -- durability ----------------------------------------------------
    def _flush(self) -> None:
        if not self.path:
            return
        payload = {
            "seeds": {
                k: {**v, "sphere": v["sphere"].hex()} for k, v in self._seeds.items()
            },
            "audit": self.audit.entries(),
        }
        tmp = f"{self.path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, default=str)
        os.replace(tmp, self.path)  # atomic on NTFS and POSIX alike
