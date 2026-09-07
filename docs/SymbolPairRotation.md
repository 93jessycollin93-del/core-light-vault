# SymbolPairRotation.md

**Symbol-Pair Rotation · Router-Scoped Keying · QPDB Binding**
Canonical spec for the Seedling rotation layer. Status: implemented and under test.

| | |
|---|---|
| Reference implementation | `src/seedling/qpdb/` |
| Test harness | `tests/rotation_tests.py` (25 cases, stdlib `unittest`) |
| Runtime | Python 3.9+, user space only — no kernel hooks, no drivers, no OS assumptions |
| Verified on | Linux CI and Windows user space; `os.replace` is atomic on NTFS and POSIX alike |

---

## 0. What this layer does, and what it does not

Rotation lets a router **change what its keys are** without changing **what it is**. The
compressed payload, the topology, and the router's identity are untouched; only the binding
vectors move. That is what makes staged disclosure possible: each rotation supersedes the
previous binding, so a captured old keyset stops working the moment the rotation commits.

Three claims in the surrounding design need narrowing before anyone builds on them, because
building on the wide version produces a system that fails quietly:

**A SeedBinding authorises; it does not conceal.** `compose_non_linear` proves a caller holds
the right pairs in the right order. It does not encrypt the sphere. Anyone with filesystem
access to a seed can read its payload whether or not they hold a single pair. If seeds must be
confidential at rest, encrypt the blob (AES-256-GCM) under a key derived from the pairs through
a memory-hard KDF, and keep the binding as the integrity check it actually is. The reference
implementation does not do this, and says so at the top of `binder.py`.

**Reversibility is unconditional; ratio is not.** The three passes are bijections, so
rehydration reproduces router state byte for byte — `test_randomised_round_trips_are_byte_exact`
asserts it over 200 randomised states. No pipeline is lossless *and* smaller on every input;
by counting, most inputs of a given length have nowhere shorter to go. Structured router state
compresses well (measured: **16,737 B → 2,067 B, ratio 0.124**). High-entropy payloads round-trip
perfectly and may grow by the header — `test_incompressible_input_still_round_trips` pins that
case so nobody discovers it in production.

**Ash is not a backup.** Disintegration destroys the payload and keeps residue: a byte-class
census, lengths, fingerprints. Enough to prove what existed and reconcile an audit; never enough
to reconstitute a router. Recovery comes from the backup tiers. `AshRecord(archive=True)` retains
a sealed copy where policy demands it, and that is the only path back.

Everything below is written to hold under those three corrections.

---

## 1. Pair format

A Pair is a **relationship token**, not a raw secret. It names two symbols and the metadata that
situates them inside one router's topology.

```
Pair = (S1, S2, M)
```

| Field | Type | Binding? | Notes |
|---|---|---|---|
| `s1`, `s2` | `str` | **yes** | Symbol tokens. Glyphs, semantic tokens, or encoded vectors |
| `m.router_id` | `str` | **yes** | Owning router. Establishes provenance |
| `m.context_hash` | `str` | **yes** | Binds the pair to one region of the router's context |
| `m.direction` | `"forward" \| "reverse"` | **yes** | Edge orientation |
| `m.weight` | `float` | **yes** | Encoded via `repr()` — no float-formatting drift across platforms |
| `m.version` | `int` | **yes** | Pair format version |
| `m.rotation_index` | `int` | **yes** | Monotonic per pair; increments on every rotation |
| `m.timestamp` | `float` | no | Wall clock at creation or last rotation |
| `m.rotation_signature` | `str \| None` | no | Authorship proof for the rotation that produced this pair |
| `m.nonce_hash` | `str \| None` | no | Which nonce produced it. Feeds replay detection |

**Why timestamp and signature are excluded from binding.** A pair must bind to the same vector on
every machine that holds it, forever. Wall clock differs across machines; signatures are
per-event authorship proof, not binding material. Including either would make a valid keyset
fail to open its own seed on a second machine. `PairMeta.canonical()` enforces the split.

Encoding is canonical JSON — sorted keys, `(",", ":")` separators — so byte-identical input
produces byte-identical output on any platform.

### Display safety

`Pair.fingerprint()` returns an 8-byte BLAKE2b digest. That is the **only** representation that
may reach a UI, a log line, or an audit entry. `KeyStore.__repr__` prints `REDACTED`;
`KeyStore.fingerprints()` is the display accessor. `test_no_raw_pairs_reach_storage` serialises a
whole seed record and asserts no `s1` or `s2` appears anywhere in it.

---

## 2. Binding: `bind_pair` and `compose_non_linear`

```python
def bind_pair(pair, topology_hash) -> bytes:            # 32 bytes
    token = H(TAG_TOKEN, s1, s2, m.canonical(), topology_hash)
    return pattern_transform(token)                      # two rounds, distinct tags

def compose_non_linear(vectors) -> bytes:                # 32 bytes
    state = INITIAL_STATE
    for i, v in enumerate(vectors):
        state = H(TAG_COMPOSE, state, v, i)               # index mixed in => order-sensitive
    return H(TAG_COMPOSE, state, len(vectors))            # arity bound => n=4 ≠ truncated n=8
```

Four properties the tests hold to:

- **Deterministic.** No randomness, no machine state. A vector computed on a Windows workstation
  equals the vector computed in CI.
- **Non-invertible.** Recovering `(S1, S2)` from a vector means inverting BLAKE2b. Vectors are
  safe to compare, never safe to treat as secret carriers.
- **Order-sensitive.** The step index is folded into every round, so a vector contributes
  differently at position 0 than at position 3. Reordering changes every downstream fold.
  This is what swap detection rests on — `test_order_matters`.
- **Arity-bound.** The final fold commits to the vector count, so a 4-key binding can never be
  satisfied by a truncated 8-key set — `test_arity_is_bound`.

Every hash input is **length-prefixed**, so `("ab","c")` and `("a","bc")` cannot collide.
All comparisons go through `hmac.compare_digest` — a prefix-timing oracle on a binding would leak
it a byte at a time.

Domain-separation tags (`seedling/qpdb/v1/*`) are part of the wire format. Changing one is a
breaking change that invalidates every existing seed.

### Key modes

| Mode | Friction | Use |
|---|---|---|
| **4** | low, fast | Router vaults, dev workflows, frequent unlocks |
| **6** | moderate | Personal context vaults — the default |
| **8** | high, deliberate | Bibliothèque Eru, long-term archives |

`KEY_MODES = (4, 6, 8)`. `KeyStore.load` rejects anything else rather than silently accepting a
weaker keyset. All three are exercised by `test_all_key_modes`.

---

## 3. Rotation, swap, and the atomicity rule

### The rule

> Compute every new vector and the new SeedBinding **before** mutating anything. If any step
> raises, neither the key store nor the seed record is touched.

A half-rotated router is unopenable by anybody — the keyset moved but the binding did not, or
the reverse. There is no repair path from that state, so the only safe design is one that cannot
enter it. `test_bad_index_rotates_nothing` names an out-of-range pair mid-plan and asserts the
binding, the fingerprints, and rehydration are all exactly as before.

### `rotate_pair(pair, nonce, router_private_key) -> Pair'`

Pure. Commits nothing. Deterministic: the same `(pair, nonce)` always yields the same successor,
which is what lets a rotation replay identically during a backup restore.
`rotation_index` increments; `rotation_signature` and `nonce_hash` are recorded.

HMAC-BLAKE2b stands in for the signing key. Swap in Ed25519 when the router holds an asymmetric
identity — the audit format does not change.

### `rotate_seed(store, seed_id, plan, keystore, key, *, dna_validator=None) -> bytes`

Ordered gates, each of which aborts before any mutation:

1. Seed exists and is `active` — never rotate a quarantined or Ash seed.
2. Seedling DNA validates, when a validator is supplied.
3. The plan's nonce is not in `metadata.spent_nonces` → else `ReplayError`.
4. Every index in the plan is in range for the current key mode.
5. **Stage** all successors and compute the new binding.
6. **Checkpoint** the outgoing keyset into the back pocket (§4).
7. **Commit**: write staged pairs, atomically replace the binding, append the audit entry.

### `swap_pairs(store, seed_id, keystore, order) -> bytes`

Reorder and rebind. Because composition is order-sensitive, the old order stops validating the
instant this commits — presentation order is itself a factor of the key. `order` must be a full
permutation; a partial one is refused (`test_partial_permutation_rejected`).

### Replay protection

Each seed carries `metadata.spent_nonces`. A nonce is spent per-seed at commit, so the same nonce
cannot be reused against that seed even for a different pair index
(`test_nonce_cannot_be_reused`), and a refused replay leaves binding and fingerprints untouched
(`test_replayed_nonce_leaves_state_untouched`).

### Rotation policy

| Vault class | Cadence | Mode |
|---|---|---|
| Router vaults | monthly, or on truth milestone | 4 |
| Personal context | quarterly | 6 |
| Bibliothèque Eru | yearly | 8 |
| Any class | **immediately** on suspected compromise | unchanged |

---

## 4. Nested rotation and conflict reconciliation

Routers nest: `Router C` carries `Router A`, which carries `Router B`. Each layer holds its own
keyset, and holding the outer keyset grants **nothing** about the inner one —
`test_inner_stays_sealed_without_its_own_keys`.

### The problem nesting creates

When a parent seals a child, it carries a **snapshot** of that child, bound under the keyset the
child held *at that moment*. If the child later rotates while dormant, the parent's copy still
carries the old binding. Symbol transforms are one-way, so the rotated keyset cannot be walked
backwards to reopen it.

This is not hypothetical. It is the first thing the harness caught: the three-level nested test
failed with `QPDB binding mismatch` until the mechanism below existed.

### The resolution: the back pocket

`KeyStore` snapshots itself **before** every rotation and swap, and stamps a monotonic `epoch`.
Each seed records the `key_epoch` that sealed it. To open a carried record you ask for the keyset
that actually sealed it:

```python
rec  = store.export_seed(child_id)         # detached, deep-copied
keys = keystore.at(SeedStore.sealing_epoch(rec))
router = rehydrate_seed(store, store.import_seed(rec), keys)
```

This is the Tier-1 back pocket doing exactly the job it was designed for, and it is **bounded**:
`back_pocket_depth` (default 8) epochs are retained, then the oldest is dropped and snapshots
sealed under it become unopenable — by design, not by bug. Retention is a policy dial; set it
from your deepest expected nesting plus your slowest restore window.
Covered by `test_rotated_router_still_opens_its_dormant_snapshot` and
`test_back_pocket_is_bounded`.

> **`export_seed` deep-copies, and must.** An aliased export keeps tracking the live record, so a
> later rotation silently rewrites the snapshot's `key_epoch` and leaves it claiming a keyset that
> never sealed it. That aliasing bug was live in the first draft of this harness; the deep copy in
> `SeedStore.export_seed` is what fixes it. Do not "optimise" it away.

### Rehydration order

Strictly **outer → inner**. Each layer validates DNA and binding before the next is touched, so a
compromised outer layer never gets the chance to hand a forged child to the inner rehydrator.

Rehydration is atomic per layer: on failure the layer returns nothing. A partially reconstructed
router is never handed back to a caller.

### Failure disposition

| Condition | Action | Why |
|---|---|---|
| DNA validation fails | disintegrate to Ash | Drift or tampering. The foundation rule |
| Binding mismatch | **quarantine**, do not destroy | A wrong key is a typo, not proof of corruption |
| Inner layer fails | outer rolls back; inner quarantined | Never return a half-built router |

That distinction is deliberate. Destroying a seed on a mistyped key is a denial-of-service
against the router's own owner, and an attacker who can present wrong keys could use it to erase
data they were never able to read.

---

## 5. Audit trail

Append-only and **hash-chained**: every entry commits to its predecessor, so a deleted or edited
entry breaks `verify()` at exactly the tampered position
(`test_audit_chain_records_every_op_and_detects_tampering`).

```json
{
  "ts": 1789432187.114,
  "seed_id": "seed_9f2c1ab77e04",
  "op": "rotate",
  "detail": {
    "indices": [0, 2], "nonce_hash": "b41d…", "reason": "scheduled",
    "rotation_index": 3, "key_epoch": 2,
    "signatures": ["7c5e…", "01ba…"],
    "from": "a19f4c22e0b13d55", "to": "6d02e7f1aa39b480"
  },
  "prev": "3f9a11c7e0d24b68",
  "entry_hash": "c8b70e14da55f902"
}
```

Operations: `create` · `rotate` · `swap` · `rehydrate` · `quarantine` · `disintegrate` ·
`ash_unlock` · `ash_denied`.

Only fingerprints and prefixes are recorded — never symbol material, never a private key.
Bindings appear as 16-hex-character prefixes, enough to correlate, not enough to substitute.

---

## 6. Storage and secure-enclave guidance

The invariant, enforced by `_assert_no_raw_material` on **every** write:

> A seed record may hold the sphere blob, the topology hash, the binding fingerprint and
> metadata. Never a raw symbol pair. Never a private key.

Keys `{s1, s2, pairs, local_pairs, private_key, router_private_key}` are refused at any depth of
the record, so an accidental nesting cannot smuggle material past the check.

`KeyStore` in this implementation is **process memory only** — adequate for tests, not for
deployment. Production must back it with:

| Platform | Backing store |
|---|---|
| Windows | TPM via CNG, or DPAPI with a user-bound entropy blob |
| macOS | Secure Enclave / Keychain |
| Linux | TPM2 via `tpm2-pkcs11`, or kernel keyring |
| Any | OS keyring through `keyring`, as a floor, not a target |

`KeyStore.seal()` retires a keyset: pairs are dropped and the store refuses all further use.
Seal **after** the seeds under that keyset are re-sealed or archived — sealing first strands them.

### k-of-n recovery

`k_threshold` ∈ 1..8, chosen per vault class. Recovery presents k valid pairs plus out-of-band
confirmation (device signature, or a second custodian for archival classes). Presenting fewer
than k raises `PermissionError` and writes an `ash_denied` entry — a failed attempt is itself a
signal worth keeping.

---

## 7. Test harness

`tests/rotation_tests.py` — stdlib `unittest`, no pytest, no network, no on-disk fixtures.

```bash
python tests/rotation_tests.py           # human-readable
python tests/rotation_tests.py --json    # machine report, exit 0 / 1
```

| Class | Cases | Asserts |
|---|---|---|
| `RotationRoundTrip` | 5 | Rotate-all preserves state; the stale keyset is refused and the seed quarantines; partial rotation still rebinds the whole composition; rotation is deterministic; modes 4/6/8; arity binding |
| `SwapRobustness` | 3 | Correct order validates and shuffled does not; post-swap the old order fails; a partial permutation is refused |
| `NestedRotation` | 4 | Three-level C→A→B rotated at every layer, unwound outer→inner byte-exact; outer keys never open the inner seed; a rotated router still opens its dormant snapshot from the back pocket; the back pocket is bounded |
| `ReplayAndAtomicity` | 5 | Nonce reuse refused; a refused replay mutates nothing; a bad index rotates nothing; the audit chain records every op and detects tampering; no raw pairs reach storage |
| `AshSafety` | 5 | DNA failure disintegrates; Ash is residue-only; k-of-n enforced both ways; archived Ash recovers; an Ash seed is neither rotatable nor rehydratable |
| `RoundTripFidelity` | 2 | 200 seeded randomised states byte-exact; high-entropy input round-trips |
| `Throughput` | 1 | Prints a baseline; gates only on a 2 s rehydration budget |

**25 cases, all passing.** Determinism comes from `random.Random(20260907)`, so a failure
reproduces exactly.

Latest run, this container:

```json
{"artifact":"SymbolPairRotation.md","tests_run":25,"tests_passed":true,"failures":[],
 "benchmark":{"raw_bytes":16737,"sphere_bytes":2067,"ratio":0.1235,
              "compress_ms":2.907,"rotate_6_ms":0.165,"rehydrate_ms":1.698}}
```

Throughput is a **baseline, not a gate** — it is there to compare machines (this container vs.
the RTX 3090 rig), and CI runners are noisy enough that a hard threshold would flake.

### Not yet covered

Named so nobody mistakes the green run for full coverage:

- Concurrent rotation of one seed from two processes. The store is single-writer; a file lock
  is needed before that is safe.
- Signature verification. Signatures are produced and audited, never checked on the read path.
  Add `verify_rotation_signature` when routers hold asymmetric identities.
- Fuzzing of malformed pairs and truncated spheres.
- Persistence round-trip through `SeedStore(path=…)`.

---

## 8. Operational playbook

| Action | Call |
|---|---|
| Seal a router | `create_seed(store, state, ks.all(), router_id=…, key_epoch=ks.epoch)` |
| Rotate | `rotate_seed(store, sid, RotationPlan(indices, nonce), ks, key, dna_validator=seedling_dna)` |
| Reorder | `swap_pairs(store, sid, ks, order)` |
| Open | `rehydrate_seed(store, sid, ks.all())` |
| Open a carried snapshot | `rehydrate_seed(store, store.import_seed(rec), ks.at(SeedStore.sealing_epoch(rec)))` |
| Nest | `create_seed(store, {…, "child": store.export_seed(inner)}, …, child_seed_ids=[inner])` |
| Disintegrate | `store.disintegrate_to_ash(sid, reason, k_threshold=…, archive=…)` |
| Unlock Ash | `store.rehydrate_ash(ash_id, pairs[:k], topology_hash)` |
| Audit | `store.audit.entries(seed_id)` · `store.audit.verify()` |

**Nonces** must come from `os.urandom(16)` or better. A predictable nonce makes
`transform_symbols` predictable, and the whole rotation with it.

---

## 9. Next artifacts

1. `QPDB_KeySystem.md` — binder math, composition algebra, k-of-n recovery design
2. `eYeCompression.spec` — the three passes with reversibility proofs per stage
3. `NestedRouterCompression.md` — deeper nesting, epoch-retention policy, edge cases
4. `RouterRehydrationProtocol.md` — formal state machine and truth-cycle termination
5. `SeedlingDNA_FoundationRules.md` — the full invariant set and drift heuristics
6. Confidentiality layer — AES-256-GCM over the sphere, keyed by KDF from the pairs (§0)
7. File locking on `SeedStore` — prerequisite for concurrent rotation
