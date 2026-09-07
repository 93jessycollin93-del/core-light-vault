"""Rotation, nesting, replay, and Ash safety tests for the Seedling QPDB layer.

Stdlib only (unittest). No pytest, no network, no fixtures on disk.
Runs identically on Windows user space, Linux, and CI.

    python tests/rotation_tests.py            # human-readable
    python tests/rotation_tests.py --json     # machine report
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from seedling.compression import RehydrationError, create_seed, rehydrate_seed  # noqa: E402
from seedling.qpdb import (  # noqa: E402
    KeyStore,
    ReplayError,
    RotationError,
    RotationPlan,
    bind_all,
    generate_pairs,
    rotate_pair,
    rotate_seed,
    swap_pairs,
    validate_binding,
)
from seedling.seed_store import SeedStore, seedling_dna  # noqa: E402

ROUTER_KEY = b"router-private-key-for-tests-only"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def router_state(name: str, size: int = 12, child: dict | None = None) -> dict:
    """A router with a small inference graph, optionally carrying a child seed."""
    state = {
        "router_id": name,
        "truth_cycle": {"iteration": 0, "equilibrium": False},
        "graph": [
            {"node": f"{name}_n{i}", "edges": [f"{name}_n{(i + k) % size}" for k in (1, 2)], "w": i * 0.5}
            for i in range(size)
        ],
        "_runtime_scratch": {"pid": os.getpid()},  # dropped by Pass 1
    }
    if child is not None:
        state["child_seed"] = child
    return state


def embed(store: SeedStore, seed_id: str) -> dict:
    return store.export_seed(seed_id)


def keys_for(ks: KeyStore, rec: dict):
    """The keyset that actually sealed this record -- current, or from the
    back pocket if the router has rotated since the record was embedded."""
    return ks.at(SeedStore.sealing_epoch(rec))


def unembed(store: SeedStore, rec: dict) -> str:
    return store.import_seed(rec)


def new_store_with_router(name: str, mode: int = 6, child: dict | None = None):
    store = SeedStore()
    pairs = generate_pairs(name, mode)
    ks = KeyStore(name)
    ks.load(pairs)
    sid = create_seed(store, router_state(name, child=child), ks.all(),
                      router_id=name, key_epoch=ks.epoch)
    return store, ks, sid


# --------------------------------------------------------------------------
# 1. Rotation round-trip
# --------------------------------------------------------------------------
class RotationRoundTrip(unittest.TestCase):
    def test_rotate_all_pairs_then_rehydrate(self):
        """After rotating every pair, the rotated keyset rehydrates the router
        unchanged and the pre-rotation keyset stops working."""
        store, ks, sid = new_store_with_router("router_a")
        before = rehydrate_seed(store, sid, ks.all())
        stale = ks.all()

        rotate_seed(store, sid, RotationPlan(indices=range(ks.mode()), nonce=os.urandom(16)),
                    ks, ROUTER_KEY, dna_validator=seedling_dna)

        after = rehydrate_seed(store, sid, ks.all())
        self.assertEqual(before, after, "rotation must not alter router state")
        self.assertEqual(before["router_id"], "router_a")

        with self.assertRaises(RehydrationError):
            rehydrate_seed(store, sid, stale)
        self.assertEqual(store.get(sid)["state"], "quarantined")

    def test_partial_rotation(self):
        """Rotating a subset still rebinds the whole composition."""
        store, ks, sid = new_store_with_router("router_p")
        old = store.get(sid)["seed_binding"]
        rotate_seed(store, sid, RotationPlan(indices=[0, 2], nonce=os.urandom(16)), ks, ROUTER_KEY)
        self.assertNotEqual(old, store.get(sid)["seed_binding"])
        self.assertEqual(rehydrate_seed(store, sid, ks.all())["router_id"], "router_p")

    def test_rotation_is_deterministic(self):
        """Same pair + same nonce => same successor, on any machine."""
        p = generate_pairs("router_d", 4)[0]
        n = os.urandom(16)
        a, b = rotate_pair(p, n, ROUTER_KEY), rotate_pair(p, n, ROUTER_KEY)
        self.assertEqual((a.s1, a.s2), (b.s1, b.s2))
        self.assertEqual(a.m.rotation_index, p.m.rotation_index + 1)

    def test_all_key_modes(self):
        for mode in (4, 6, 8):
            with self.subTest(mode=mode):
                store, ks, sid = new_store_with_router(f"router_m{mode}", mode=mode)
                rotate_seed(store, sid, RotationPlan(indices=[0], nonce=os.urandom(16)), ks, ROUTER_KEY)
                self.assertEqual(rehydrate_seed(store, sid, ks.all())["router_id"], f"router_m{mode}")

    def test_arity_is_bound(self):
        """A 4-key binding can never be satisfied by a truncated 8-key set."""
        pairs = generate_pairs("router_ar", 8)
        th = "topology-fixed"
        self.assertNotEqual(bind_all(pairs, th), bind_all(pairs[:4], th))


# --------------------------------------------------------------------------
# 2. Swap robustness
# --------------------------------------------------------------------------
class SwapRobustness(unittest.TestCase):
    def test_order_matters(self):
        """Presentation order is part of the key: the same pairs in the wrong
        order must not validate."""
        store, ks, sid = new_store_with_router("router_s")
        binding = bytes.fromhex(store.get(sid)["seed_binding"])
        th = store.get(sid)["topology_hash"]
        correct = ks.all()
        shuffled = [correct[1], correct[0]] + correct[2:]
        self.assertTrue(validate_binding(binding, correct, th))
        self.assertFalse(validate_binding(binding, shuffled, th))

    def test_swap_rebinds_and_old_order_fails(self):
        store, ks, sid = new_store_with_router("router_s2")
        order = [1, 0] + list(range(2, ks.mode()))
        pre_swap_order = ks.all()
        swap_pairs(store, sid, ks, order)
        self.assertEqual(rehydrate_seed(store, sid, ks.all())["router_id"], "router_s2")
        with self.assertRaises(RehydrationError):
            rehydrate_seed(store, sid, pre_swap_order)

    def test_partial_permutation_rejected(self):
        store, ks, sid = new_store_with_router("router_s3")
        with self.assertRaises(RotationError):
            swap_pairs(store, sid, ks, [0, 0, 1, 2, 3, 4])


# --------------------------------------------------------------------------
# 3. Nested rotation (Russian doll)
# --------------------------------------------------------------------------
class NestedRotation(unittest.TestCase):
    def test_three_level_nest_rotate_and_rehydrate_outer_to_inner(self):
        """C contains A contains B. Rotate every level, then unwind outer->inner
        and assert each router comes back byte-identical."""
        store = SeedStore()

        ks_b = KeyStore("router_b"); ks_b.load(generate_pairs("router_b", 4))
        sid_b = create_seed(store, router_state("router_b"), ks_b.all(),
                            router_id="router_b", key_epoch=ks_b.epoch)

        ks_a = KeyStore("router_a"); ks_a.load(generate_pairs("router_a", 6))
        sid_a = create_seed(store, router_state("router_a", child=embed(store, sid_b)),
                            ks_a.all(), router_id="router_a",
                            child_seed_ids=[sid_b], key_epoch=ks_a.epoch)

        ks_c = KeyStore("router_c"); ks_c.load(generate_pairs("router_c", 8))
        sid_c = create_seed(store, router_state("router_c", child=embed(store, sid_a)),
                            ks_c.all(), router_id="router_c",
                            child_seed_ids=[sid_a], key_epoch=ks_c.epoch)

        # Each layer rotates on its own schedule, inner layers while dormant.
        for sid, ks in ((sid_c, ks_c), (sid_a, ks_a), (sid_b, ks_b)):
            rotate_seed(store, sid, RotationPlan(indices=[0, 1], nonce=os.urandom(16)), ks, ROUTER_KEY)

        # Unwind: outer validates before inner is ever touched. The embedded
        # copies were sealed before their routers rotated, so each inner layer
        # opens with its back-pocket epoch, not the current keyset.
        outer = rehydrate_seed(store, sid_c, ks_c.all())
        self.assertEqual(outer["router_id"], "router_c")

        inner_store = SeedStore()
        mid_rec = outer["child_seed"]
        mid_id = unembed(inner_store, mid_rec)
        mid = rehydrate_seed(inner_store, mid_id, keys_for(ks_a, mid_rec))
        self.assertEqual(mid["router_id"], "router_a")

        leaf_rec = mid["child_seed"]
        leaf_id = unembed(inner_store, leaf_rec)
        leaf = rehydrate_seed(inner_store, leaf_id, keys_for(ks_b, leaf_rec))
        self.assertEqual(leaf["router_id"], "router_b")
        self.assertEqual(leaf["graph"], router_state("router_b")["graph"])

    def test_inner_stays_sealed_without_its_own_keys(self):
        """Holding the outer keyset does not grant the inner router. Sovereignty
        is per-router, not inherited."""
        store = SeedStore()
        ks_b = KeyStore("router_b"); ks_b.load(generate_pairs("router_b", 4))
        sid_b = create_seed(store, router_state("router_b"), ks_b.all(),
                            router_id="router_b", key_epoch=ks_b.epoch)
        ks_a = KeyStore("router_a"); ks_a.load(generate_pairs("router_a", 4))
        sid_a = create_seed(store, router_state("router_a", child=embed(store, sid_b)),
                            ks_a.all(), router_id="router_a",
                            child_seed_ids=[sid_b], key_epoch=ks_a.epoch)

        outer = rehydrate_seed(store, sid_a, ks_a.all())
        inner_store = SeedStore()
        inner_id = unembed(inner_store, outer["child_seed"])
        with self.assertRaises(RehydrationError):
            rehydrate_seed(inner_store, inner_id, ks_a.all())  # outer keys, inner seed

    def test_rotated_router_still_opens_its_dormant_snapshot(self):
        """A router that rotates while a snapshot of it sits inside a parent
        must still be able to open that snapshot -- from the back pocket."""
        store, ks, sid = new_store_with_router("router_dormant")
        snapshot = embed(store, sid)
        rotate_seed(store, sid, RotationPlan(indices=[0], nonce=os.urandom(16)), ks, ROUTER_KEY)

        cold = SeedStore()
        cold_id = unembed(cold, snapshot)
        with self.assertRaises(RehydrationError):
            rehydrate_seed(cold, cold_id, ks.all())          # current keyset: too new
        cold2 = SeedStore()
        cold_id2 = unembed(cold2, snapshot)
        self.assertEqual(
            rehydrate_seed(cold2, cold_id2, keys_for(ks, snapshot))["router_id"],
            "router_dormant",
        )

    def test_back_pocket_is_bounded(self):
        """Past the retention depth an old epoch is gone for good -- the
        snapshot sealed under it is unopenable, by design rather than by bug."""
        store = SeedStore()
        ks = KeyStore("router_bounded", back_pocket_depth=2)
        ks.load(generate_pairs("router_bounded", 4))
        sid = create_seed(store, router_state("router_bounded"), ks.all(),
                          router_id="router_bounded", key_epoch=ks.epoch)
        snapshot = embed(store, sid)
        for _ in range(4):
            rotate_seed(store, sid, RotationPlan(indices=[0], nonce=os.urandom(16)), ks, ROUTER_KEY)
        with self.assertRaises(KeyError):
            keys_for(ks, snapshot)


# --------------------------------------------------------------------------
# 4. Replay protection and atomicity
# --------------------------------------------------------------------------
class ReplayAndAtomicity(unittest.TestCase):
    def test_nonce_cannot_be_reused(self):
        store, ks, sid = new_store_with_router("router_r")
        nonce = os.urandom(16)
        rotate_seed(store, sid, RotationPlan(indices=[0], nonce=nonce), ks, ROUTER_KEY)
        with self.assertRaises(ReplayError):
            rotate_seed(store, sid, RotationPlan(indices=[1], nonce=nonce), ks, ROUTER_KEY)

    def test_replayed_nonce_leaves_state_untouched(self):
        store, ks, sid = new_store_with_router("router_r2")
        nonce = os.urandom(16)
        rotate_seed(store, sid, RotationPlan(indices=[0], nonce=nonce), ks, ROUTER_KEY)
        binding, fps = store.get(sid)["seed_binding"], ks.fingerprints()
        with self.assertRaises(ReplayError):
            rotate_seed(store, sid, RotationPlan(indices=[0], nonce=nonce), ks, ROUTER_KEY)
        self.assertEqual(binding, store.get(sid)["seed_binding"])
        self.assertEqual(fps, ks.fingerprints())

    def test_bad_index_rotates_nothing(self):
        """A plan naming an out-of-range pair must abort before any mutation."""
        store, ks, sid = new_store_with_router("router_r3")
        binding, fps = store.get(sid)["seed_binding"], ks.fingerprints()
        with self.assertRaises(RotationError):
            rotate_seed(store, sid, RotationPlan(indices=[0, 99], nonce=os.urandom(16)), ks, ROUTER_KEY)
        self.assertEqual(binding, store.get(sid)["seed_binding"])
        self.assertEqual(fps, ks.fingerprints())
        self.assertEqual(rehydrate_seed(store, sid, ks.all())["router_id"], "router_r3")

    def test_audit_chain_records_every_op_and_detects_tampering(self):
        store, ks, sid = new_store_with_router("router_au")
        rotate_seed(store, sid, RotationPlan(indices=[0], nonce=os.urandom(16)), ks, ROUTER_KEY)
        rehydrate_seed(store, sid, ks.all())
        ops = [e["op"] for e in store.audit.entries(sid)]
        self.assertEqual(ops, ["create", "rotate", "rehydrate"])
        self.assertTrue(store.audit.verify())
        store.audit.entries(sid)  # read-only view
        store.audit._entries[1]["detail"]["reason"] = "tampered"
        self.assertFalse(store.audit.verify())

    def test_no_raw_pairs_reach_storage(self):
        """The storage invariant: a seed record never carries symbol material."""
        store, ks, sid = new_store_with_router("router_np")
        blob = json.dumps(store.get(sid), default=lambda o: o.hex() if isinstance(o, bytes) else str(o))
        for p in ks.all():
            self.assertNotIn(p.s1, blob)
            self.assertNotIn(p.s2, blob)


# --------------------------------------------------------------------------
# 5. Ash safety
# --------------------------------------------------------------------------
class AshSafety(unittest.TestCase):
    def test_dna_failure_disintegrates_to_ash(self):
        """Corrupt the sphere behind the store's back; DNA catches the drift on
        the next rehydration and the seed becomes Ash."""
        store, ks, sid = new_store_with_router("router_ash")
        store.get(sid)["sphere"] = b"\x00" * 64  # drift: digest no longer matches
        with self.assertRaises(RehydrationError):
            rehydrate_seed(store, sid, ks.all())
        self.assertEqual(store.get(sid)["state"], "ash")

    def test_ash_is_residue_only(self):
        """Ash keeps a census and fingerprints -- never the payload."""
        store, ks, sid = new_store_with_router("router_ash2")
        sphere = store.get(sid)["sphere"]
        ash_id = store.disintegrate_to_ash(sid, "operator_command", k_threshold=3)
        ash = store.get_ash(ash_id)
        self.assertEqual(store.get(sid)["sphere"], b"")
        self.assertFalse(ash.is_recoverable())
        self.assertNotIn("sphere", ash.residue)
        self.assertEqual(ash.residue["byte_length"], len(sphere))
        self.assertIn("census", ash.residue)

    def test_ash_unlock_requires_k_of_n(self):
        store, ks, sid = new_store_with_router("router_ash3")
        th = store.get(sid)["topology_hash"]
        ash_id = store.disintegrate_to_ash(sid, "compromise", k_threshold=3)
        with self.assertRaises(PermissionError):
            store.rehydrate_ash(ash_id, ks.all()[:2], th)
        out = store.rehydrate_ash(ash_id, ks.all()[:3], th)
        self.assertIn("census", out["residue"])
        self.assertIsNone(out["sphere"], "non-archived ash must not yield a payload")

    def test_archived_ash_can_be_recovered(self):
        store, ks, sid = new_store_with_router("router_ash4")
        th, sphere = store.get(sid)["topology_hash"], store.get(sid)["sphere"]
        ash_id = store.disintegrate_to_ash(sid, "planned_retire", k_threshold=1, archive=True)
        out = store.rehydrate_ash(ash_id, ks.all()[:1], th)
        self.assertEqual(out["sphere"], sphere)

    def test_ash_seed_cannot_be_rotated_or_rehydrated(self):
        store, ks, sid = new_store_with_router("router_ash5")
        store.disintegrate_to_ash(sid, "operator_command")
        with self.assertRaises(RotationError):
            rotate_seed(store, sid, RotationPlan(indices=[0], nonce=os.urandom(16)), ks, ROUTER_KEY)
        with self.assertRaises(RehydrationError):
            rehydrate_seed(store, sid, ks.all())


# --------------------------------------------------------------------------
# 6. Round-trip fidelity and throughput
# --------------------------------------------------------------------------
class RoundTripFidelity(unittest.TestCase):
    def test_randomised_round_trips_are_byte_exact(self):
        """200 randomised router states, seeded for reproducibility."""
        rng = random.Random(20260907)
        store = SeedStore()
        for i in range(200):
            pairs = generate_pairs(f"r{i}", rng.choice((4, 6, 8)))
            state = {
                "router_id": f"r{i}",
                "depth": rng.randint(0, 9),
                "vals": [rng.random() for _ in range(rng.randint(1, 30))],
                "tags": [f"tag_{rng.randint(0, 40)}" for _ in range(rng.randint(1, 25))],
                "nested": {"a": {"b": {"c": rng.randint(0, 10**9)}}},
            }
            sid = create_seed(store, state, pairs, router_id=f"r{i}")
            self.assertEqual(rehydrate_seed(store, sid, pairs), state)

    def test_incompressible_input_still_round_trips(self):
        """Ratio is not guaranteed; reversibility is. High-entropy payloads must
        survive intact even when the sphere is no smaller than the source."""
        store = SeedStore()
        pairs = generate_pairs("router_hi", 4)
        state = {"router_id": "router_hi", "blob": os.urandom(4096).hex()}
        sid = create_seed(store, state, pairs, router_id="router_hi")
        self.assertEqual(rehydrate_seed(store, sid, pairs), state)


class Throughput(unittest.TestCase):
    """Not a pass/fail gate -- a printed baseline to compare across machines."""

    def test_report_latency(self):
        store = SeedStore()
        pairs = generate_pairs("router_perf", 6)
        ks = KeyStore("router_perf"); ks.load(pairs)
        state = router_state("router_perf", size=200)

        t0 = time.perf_counter()
        sid = create_seed(store, state, ks.all(), router_id="router_perf")
        t_compress = time.perf_counter() - t0

        t0 = time.perf_counter()
        rotate_seed(store, sid, RotationPlan(indices=range(6), nonce=os.urandom(16)), ks, ROUTER_KEY)
        t_rotate = time.perf_counter() - t0

        t0 = time.perf_counter()
        rehydrate_seed(store, sid, ks.all())
        t_rehydrate = time.perf_counter() - t0

        md = store.get(sid)["metadata"]
        Throughput.report = {
            "raw_bytes": md["raw_bytes"],
            "sphere_bytes": md["sphere_bytes"],
            "ratio": round(md["sphere_bytes"] / md["raw_bytes"], 4),
            "compress_ms": round(t_compress * 1000, 3),
            "rotate_6_ms": round(t_rotate * 1000, 3),
            "rehydrate_ms": round(t_rehydrate * 1000, 3),
        }
        self.assertLess(t_rehydrate, 2.0, "rehydration budget exceeded")


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------
def main() -> int:
    as_json = "--json" in sys.argv
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    stream = open(os.devnull, "w") if as_json else sys.stderr
    result = unittest.TextTestRunner(stream=stream, verbosity=0 if as_json else 2).run(suite)
    if as_json:
        stream.close()

    failures = [
        {"test": str(t), "error": e.strip().splitlines()[-1]}
        for t, e in (result.failures + result.errors)
    ]
    report = {
        "artifact": "SymbolPairRotation.md",
        "tests_run": result.testsRun,
        "tests_passed": result.wasSuccessful(),
        "failures": failures,
        "benchmark": getattr(Throughput, "report", None),
    }
    print(json.dumps(report, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
