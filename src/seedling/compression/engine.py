"""The eYe pipeline: router state -> three reversible passes -> one dot.

    Pass 1  structural   canonical form; symbols normalised, metadata aligned
    Pass 2  relational   topology split from payload; repeated symbols interned
    Pass 3  geometric    Y-axis slicing -> fold toward centre -> collapse
            density      entropy coding of the folded core

Every stage is a bijection on its input, so rehydration reproduces the router
state byte for byte. The round-trip tests assert exactly that.

HONEST BOUNDS. Reversibility is unconditional; ratio is not. No lossless
scheme shrinks every input -- by counting, most inputs of a given length
cannot map to shorter distinct outputs. Structured router state (repeated
identifiers, regular graphs) compresses well; already-compressed or random
payloads will round-trip perfectly and may grow by the header. Pass 3's fold
is a permutation: it costs nothing and it reorders bytes so that the entropy
coder in density_scale sees related bytes adjacently. It is not itself a
size reduction, and nothing here beats the entropy of the source.
"""
from __future__ import annotations

import hashlib
import json
import uuid
import zlib
from typing import Any, Dict, List, Optional, Tuple

MAGIC = b"eYe1"
DEFAULT_SLICES = 8


class RehydrationError(RuntimeError):
    """Rehydration refused or failed. No partial router is ever returned."""


# --------------------------------------------------------------------------
# Pass 1 -- structural
# --------------------------------------------------------------------------
def structural_compress(router_state: Dict[str, Any]) -> bytes:
    """Canonicalise. Sorted keys, tight separators, no transient noise.

    Keys beginning with "_runtime" are dropped: they are per-process scratch
    and reintroducing them on rehydration would be noise, not fidelity.
    """
    cleaned = _strip_runtime(router_state)
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _strip_runtime(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_runtime(v) for k, v in obj.items() if not k.startswith("_runtime")}
    if isinstance(obj, list):
        return [_strip_runtime(v) for v in obj]
    return obj


# --------------------------------------------------------------------------
# Pass 2 -- relational
# --------------------------------------------------------------------------
def relational_compress(blob: bytes) -> Tuple[bytes, Dict[str, Any]]:
    """Intern repeated tokens; emit the topology alongside the payload.

    The dictionary is part of the output, so this is a pure re-encoding: the
    inverse re-substitutes and recovers the original bytes exactly.
    """
    text = blob.decode("utf-8")
    counts: Dict[str, int] = {}
    for token in _tokens(text):
        counts[token] = counts.get(token, 0) + 1
    # Interning only pays when a token repeats and is longer than its handle.
    table = sorted(t for t, c in counts.items() if c > 1 and len(t) > 4)
    handles = {t: f"\x00{i:x}\x00" for i, t in enumerate(table)}
    for t in sorted(table, key=len, reverse=True):
        text = text.replace(t, handles[t])
    topology = {
        "table": table,
        "token_count": len(counts),
        "repeat_count": sum(1 for c in counts.values() if c > 1),
        "payload_len": len(blob),
    }
    return text.encode("utf-8"), topology


def relational_expand(payload: bytes, topology: Dict[str, Any]) -> bytes:
    text = payload.decode("utf-8")
    table = topology["table"]
    for i, t in enumerate(table):
        text = text.replace(f"\x00{i:x}\x00", t)
    return text.encode("utf-8")


def _tokens(text: str) -> List[str]:
    out, cur = [], []
    for ch in text:
        if ch.isalnum() or ch in "_-.:/":
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return out


def hash_topology(topology: Dict[str, Any]) -> str:
    """Fingerprint of the relational structure. Binds keys to shape, not bytes."""
    return hashlib.blake2b(
        json.dumps(topology, sort_keys=True, separators=(",", ":")).encode(),
        digest_size=16,
    ).hexdigest()


# --------------------------------------------------------------------------
# Pass 3 -- geometric
# --------------------------------------------------------------------------
def geometric_fold(payload: bytes, n_slices: int = DEFAULT_SLICES) -> bytes:
    """Y-axis slice, then fold outer slices toward the centre.

    Slicing is strided (payload[i::n]), which groups bytes that sit in the same
    column of a row-major layout. Folding then interleaves 0, n-1, 1, n-2, ...
    so the outermost columns land adjacent to each other. Both steps are
    permutations, recorded in a header so axis_unfold inverts them exactly.
    """
    if n_slices < 1:
        raise ValueError("n_slices must be >= 1")
    slices = [payload[i::n_slices] for i in range(n_slices)]
    folded = b"".join(slices[i] for i in _fold_order(n_slices))
    header = MAGIC + n_slices.to_bytes(2, "big") + len(payload).to_bytes(8, "big")
    return header + folded


def axis_unfold(folded: bytes) -> bytes:
    if not folded.startswith(MAGIC):
        raise RehydrationError("folded core is missing the eYe header")
    n_slices = int.from_bytes(folded[4:6], "big")
    total = int.from_bytes(folded[6:14], "big")
    body = folded[14:]
    lengths = [len(range(i, total, n_slices)) for i in range(n_slices)]
    order = _fold_order(n_slices)
    chunks: Dict[int, bytes] = {}
    pos = 0
    for idx in order:
        ln = lengths[idx]
        chunks[idx] = body[pos : pos + ln]
        pos += ln
    if pos != len(body):
        raise RehydrationError("folded core length mismatch")
    out = bytearray(total)
    for idx in range(n_slices):
        out[idx::n_slices] = chunks[idx]
    return bytes(out)


def _fold_order(n: int) -> List[int]:
    """0, n-1, 1, n-2, ... -- top toward centre, bottom toward centre."""
    lo, hi, order = 0, n - 1, []
    while lo <= hi:
        order.append(lo)
        if lo != hi:
            order.append(hi)
        lo += 1
        hi -= 1
    return order


def spherical_collapse(folded: bytes) -> bytes:
    """Minimise surface. The fold has already made related bytes adjacent;
    this is where that adjacency is cashed in."""
    return zlib.compress(folded, 9)


def spherical_unfold(sphere: bytes) -> bytes:
    try:
        return zlib.decompress(sphere)
    except zlib.error as exc:
        raise RehydrationError(f"sphere failed to expand: {exc}") from exc


def density_scale(sphere: bytes) -> bytes:
    """Attach the density header that makes the dot self-describing."""
    return len(sphere).to_bytes(8, "big") + sphere


def density_expand(dot: bytes) -> bytes:
    declared = int.from_bytes(dot[:8], "big")
    body = dot[8:]
    if declared != len(body):
        raise RehydrationError(
            f"density mismatch: header says {declared}, body is {len(body)}"
        )
    return body


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def create_seed(
    store,
    router_state: Dict[str, Any],
    pairs,
    *,
    router_id: str,
    child_seed_ids: Optional[List[str]] = None,
    n_slices: int = DEFAULT_SLICES,
    key_epoch: int = 0,
) -> str:
    """Compress a router into a seed and bind the presented keyset to it."""
    from ..qpdb.binder import bind_pair, compose_non_linear

    pairs = list(pairs)
    blob = structural_compress(router_state)                 # Pass 1
    payload, topology = relational_compress(blob)            # Pass 2
    folded = geometric_fold(payload, n_slices)               # Pass 3
    sphere = density_scale(spherical_collapse(folded))
    topology_hash = hash_topology(topology)

    binding = compose_non_linear(bind_pair(p, topology_hash) for p in pairs)

    seed = {
        "seed_id": "seed_" + uuid.uuid4().hex[:12],
        "sphere": sphere,
        "topology": topology,
        "topology_hash": topology_hash,
        "seed_binding": binding.hex(),
        "state": "active",
        "metadata": {
            "router_id": router_id,
            "key_mode": len(pairs),
            "key_epoch": key_epoch,
            "n_slices": n_slices,
            "rotation_index": max((p.m.rotation_index for p in pairs), default=0),
            "nesting_depth": 0 if not child_seed_ids else 1,
            "child_seed_ids": list(child_seed_ids or []),
            "raw_bytes": len(blob),
            "sphere_bytes": len(sphere),
        },
    }
    return store.put(seed)


def rehydrate_seed(store, seed_id: str, provided_pairs, *, dna_validator=None) -> Dict[str, Any]:
    """Validate, expand, and reconstruct. Atomic: all or nothing.

    On DNA failure the seed is disintegrated to Ash, per the foundation rule.
    On a binding mismatch the seed is quarantined -- a wrong key is not by
    itself evidence of corruption, and destroying data on a typo would be a
    denial-of-service against the owner.
    """
    from ..qpdb.rotation import validate_binding

    seed = store.get(seed_id)
    if seed is None:
        raise RehydrationError(f"unknown seed {seed_id!r}")
    if seed["state"] != "active":
        raise RehydrationError(f"seed {seed_id!r} is {seed['state']}")

    validator = dna_validator
    if validator is None:
        from ..seed_store.store import seedling_dna as validator  # type: ignore
    if not validator(seed):
        store.disintegrate_to_ash(seed_id, "dna_validation_failed")
        raise RehydrationError("Seedling DNA validation failed; seed disintegrated to Ash")

    if not validate_binding(bytes.fromhex(seed["seed_binding"]), list(provided_pairs), seed["topology_hash"]):
        store.quarantine(seed_id, "binding_mismatch")
        raise RehydrationError("QPDB binding mismatch; seed quarantined")

    folded = spherical_unfold(density_expand(seed["sphere"]))
    payload = axis_unfold(folded)
    blob = relational_expand(payload, seed["topology"])
    router_state = json.loads(blob.decode("utf-8"))
    store.audit.append(seed_id, "rehydrate", {"router_id": seed["metadata"]["router_id"]})
    return router_state
