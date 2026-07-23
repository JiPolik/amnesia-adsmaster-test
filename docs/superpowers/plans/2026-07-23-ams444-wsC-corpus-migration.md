# AMS-444 WS-C — amnesia-campaigns corpus migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Migrate the `amnesia-campaigns` corpus so **every** campaign is anchored to one global base through the chain `Campaign → vertical CampaignTemplate → global _base.yaml`, using the WS-A/WS-B keyed-name-override capability — with `verify_byte_equivalence.py` at **0 diffs** throughout.

**Architecture:** A committed, deterministic, idempotent Python migrator (extending the existing `tools/generate_templates.py` machinery) that (1) creates a global unscoped `_base.yaml`, (2) rewires the 13 existing per-vertical templates to `extends: efaq_base` and strips the now-inherited scalar defaults, (3) groups every campaign by structural signature (excluding copy/geo/budget/bid/mechanical refs), synthesizes a template per distinct signature that lacks one (incl. the 6 bare verticals), and rewrites every campaign as `extends: <template>` + a computed keyed-name delta. Correctness is guaranteed field-by-field by `verify_byte_equivalence.py` against a pinned pre-migration ref.

**Tech Stack:** Python 3 (pyyaml, jsonschema), pytest. Repo: `haremedia/amnesia-campaigns`. Requires a sibling `amnesia` checkout at `../amnesia` on `develop` (WS-A #270/#271 + WS-B #272 merged) — `validate.py` + `verify_byte_equivalence.py` import the chain-aware expander from there. Branch: `feature/AMS-444-wsc-migrate`.

**Ticket:** [AMS-444](https://linear.app/haremediagroup/issue/AMS-444/) (child of epic AMS-393).

---

## ⚠️ Preconditions + invariants (read first)

1. **Sibling amnesia @develop with WS-A+WS-B merged.** Confirm: `grep -c ad_set_defaults ../amnesia/git-sync/src/main/resources/schemas/campaign-template.schema.json` → 2, and `grep -c resolve_chain ../amnesia/.github/actions/validate-config/validate.py` → ≥1. If not, `git -C ../amnesia checkout develop && git -C ../amnesia pull`. The migrator/gate reuse THAT validator's expand chain.
2. **Byte-equivalence is the correctness contract.** `tools/verify_byte_equivalence.py <repo> --base <pre-migration-ref>` must report **0 diffs** — every campaign's expanded materialized bodies (Campaign + AdSet + AdTemplate) must be byte-identical to today's. The base ref is the pre-migration point: use `origin/main` (or `git merge-base origin/main HEAD`). Run it after EVERY migrator change.
3. **Uniform design (locked):** every vertical gets ≥1 template; **no campaign extends the base directly.** A structural group of size 1 still becomes a template + one (near-empty) delta — keeps the corpus uniform and matches the "common template for all campaigns" goal.
4. **Idempotent + deterministic:** re-running the migrator on already-migrated output is a no-op (no re-nesting, stable ordering, stable template names). Mirror `generate_templates.py`'s determinism (sorted iteration, canonical hashing).
5. **Byte-shape fidelity:** reuse `generate_templates.py`'s `dump_doc`/`write_multi`/`parse_file` (same `yaml.safe_dump` kwargs + `---\n` join) so unrelated bytes don't churn.
6. **This is the prod config repo (test account until cutover).** No fabrication — the migrator only re-shapes existing content; it never invents copy/geo/budget.

---

## Baseline (record before touching anything)
- `git rev-parse origin/main` (the byte-equivalence base ref).
- Adoption today: 187 Campaign docs, 13 CampaignTemplates, 43 extend, 6 bare verticals (`leadership`, `chat`, `greek_gods`, `license_plate`, `love_style`, `normalcy`).
- Every bare vertical has ONE ad-set-name signature (confirmed); `love_style` = 27 campaigns.

---

## Task 1: Global base `_base.yaml` (standalone, low-risk)

**Files:** create `_base.yaml` (repo root or `google/_base.yaml` — root is cleanest as it's platform-agnostic); test via `tools/validate.sh`.

- [ ] **Step 1: Create `_base.yaml`** at the repo root:

```yaml
apiVersion: amnesia/v1
kind: CampaignTemplate
metadata:
  name: efaq_base
spec:
  objective: TRAFFIC
  status: PAUSED
  ad_set_defaults:
    status: PAUSED
    bid_strategy: TARGET_CPA
  ad_template_defaults:
    status: ENABLED
```

> Values are the corpus universals (objective TRAFFIC 157/157, bid_strategy TARGET_CPA 184/184, campaign/ad-set status PAUSED dominant, ad-template status ENABLED). If the real dominant campaign/ad-set status is NOT PAUSED for some verticals, that's fine — those campaigns/templates will override it; the base only sets the default. The byte-equivalence gate will catch any default that changes an output.

- [ ] **Step 2: Validate it in isolation.** `AMNESIA_DIR=../amnesia tools/validate.sh` (or `python3 ../amnesia/.github/actions/validate-config/validate.py .`).
Expected: exit 0. The base is an unscoped CampaignTemplate: schema-valid (WS-A relaxation), scope-exempt (WS-B `normalize_body`/`process`), non-materializing (not a cross-ref target). If it errors "requires metadata.platform", the sibling amnesia isn't on develop — fix precondition 1.

- [ ] **Step 3: Byte-equivalence still 0** (base alone changes no campaign output).
`AMNESIA_DIR=../amnesia python3 tools/verify_byte_equivalence.py . --base origin/main`
Expected: 0 diffs (nothing materialized yet references the base).

- [ ] **Step 4: Commit.**
```bash
git add _base.yaml docs/superpowers/plans/2026-07-23-ams444-wsC-corpus-migration.md
git commit -m "AMS-444: add global base CampaignTemplate (_base.yaml)"
```

---

## Task 2: The migrator — rewire templates + collapse all campaigns to deltas

**Files:** create `tools/migrate_to_base_templates.py` (reuse `generate_templates.py` helpers — import or copy `dump_doc`/`write_multi`/`parse_file`); create `tools/test_migrate_to_base_templates.py`.

This is the heart. Build it TDD against small fixtures AND the real byte-equivalence gate. Do NOT hand-edit corpus files — everything goes through the committed migrator so it's reproducible + idempotent.

### 2a — Rewire existing templates + strip inherited defaults

- [ ] **Step 1: Migrator pass A** — for each existing `_template*.yaml` (13): add `metadata.extends: efaq_base`; remove from the template spec the fields now inherited from the base defaults **only when they equal the base value** — campaign-level `objective`/`status` (when TRAFFIC/PAUSED), and from each ad-set `status`/`bid_strategy` (when PAUSED/TARGET_CPA), and from each ad-template `status` (when ENABLED). Leave any non-default value in place (it's a legitimate override). Also strip the same now-redundant scalar keys from the **43 existing campaign overlays** where present (they'd be inherited).

> Why "only when equal to base": stripping a field that differs from the base would change the materialized output. The base injects defaults UNDER the item (item wins), so a stripped field must equal the default to be byte-equivalent. Let the byte-equivalence gate be the judge — if stripping X breaks a diff, don't strip X.

- [ ] **Step 2: Gate.** Run the migrator (pass A only), then:
`AMNESIA_DIR=../amnesia python3 tools/verify_byte_equivalence.py . --base origin/main` → **0 diffs**.
`AMNESIA_DIR=../amnesia tools/validate.sh` → 0 errors / 187 campaigns.
If a diff appears, a stripped field wasn't actually inherited-equal — narrow the strip. Do not proceed until 0.

- [ ] **Step 3: Commit.** `git add -A && git commit -m "AMS-444: rewire 13 templates to extends efaq_base, strip inherited defaults"`

### 2b — Group campaigns by structure, synthesize missing templates, emit deltas

- [ ] **Step 4: Structural grouping.** Extend `generate_templates.py`'s `shared_tree_key` into a `structural_signature(campaign_docs)` that canonically hashes the ad-set/ad-template **structure** while EXCLUDING everything that legitimately varies per campaign:
  - campaign `metadata.name`, `spec.budget_daily_usd`, `metadata.geo_preset_ref`, `metadata.geo_exclude_refs`, `spec.objective`, `spec.status`
  - each ad-set `spec.target_cpa_usd`/`spec.target_roas`/`spec.bid_strategy`/`spec.status`
  - each ad-template **copy**: `spec.headlines`, `spec.descriptions`, `spec.destination_url`, `spec.business_name`, `spec.call_to_action`, `spec.status`
  - mechanical: `metadata.campaign_ref`, `spec.ad_set_ref` (re-synthesized on expand)
  The signature is thus: the set of ad-set names, each with its set of ad-template names + each ad-template's `creative_feed_ref`/`logo_asset_ref` + any structural non-copy fields. Two campaigns with the same skeleton but different copy → same signature.

- [ ] **Step 5: Per (platform, vertical, signature) group:** if an existing template in that (platform, vertical) has a matching signature, reuse it; else synthesize a new `_template*.yaml` from a **representative** (deterministic pick — e.g. the alphabetically-first campaign path in the group) via `build_template_doc`, named `<vertical>_<pshort>_tmpl[_N]` (mirror generate_templates.py naming; suffix `_N` for multiple structures per vertical), carrying `extends: efaq_base`. Write the template with the representative's full tree (copy included) minus the base-inherited defaults.

- [ ] **Step 6: Rewrite every campaign as `extends: <template>` + keyed-name delta.** For each campaign, compute the delta vs its template's expanded tree:
  - `metadata.extends: <template-name>`, keep `metadata.{name,platform,vertical,geo_preset_ref,geo_exclude_refs,ad_extension_refs}`.
  - campaign `spec`: include `budget_daily_usd` always (per-campaign); include `objective`/`status` only if they differ from what the chain yields; bid (`target_cpa_usd`/`target_roas`) only if it differs (projects onto every ad-set via the expander).
  - `spec.ad_sets`: emit a keyed-name entry ONLY for ad-sets/ad-templates whose fields differ from the template. For a differing ad-template, emit `{name, <only the differing fields>}` — a differing `headlines`/`descriptions` is emitted **wholesale** (leaf-list-replace), `destination_url` if different, etc. Ad-sets/ad-templates identical to the template are omitted entirely (inherited). If nothing differs below campaign level, omit `spec.ad_sets` (pure scalar delta).

> The representative campaign's delta will be minimal/empty below campaign level (it defines the template's copy). Non-representative members override only their differing copy. This is exactly the keyed-name merge WS-A/WS-B implement.

- [ ] **Step 7: Gate (the big one).** Run the full migrator, then:
`AMNESIA_DIR=../amnesia python3 tools/verify_byte_equivalence.py . --base origin/main` → **0 diffs across all 187 campaigns**.
`AMNESIA_DIR=../amnesia tools/validate.sh` → 0 errors / 187 campaigns.
Iterate the delta computation until both are green. Common failure modes to watch: a leaf-list emitted partially (must be wholesale), a bid/status difference not captured in the delta, an ad-set present in the campaign but not the template (must be added as a new keyed entry, not dropped), ordering differences (canonicalize).

- [ ] **Step 8: Idempotency test.** Run the migrator a SECOND time on the migrated tree → `git diff` shows no changes. Add this as a pytest case + assert it manually.

- [ ] **Step 9: pytest** for the migrator on small synthetic fixtures: signature grouping (same skeleton/diff copy → 1 group; diff skeleton → 2 groups); delta emits only differing fields; leaf-list wholesale; representative gets empty tree delta; new-template synthesis; idempotency. `python3 -m pytest tools/test_migrate_to_base_templates.py -q`.

- [ ] **Step 10: Commit.** `git add -A && git commit -m "AMS-444: migrator collapses all campaigns to base-anchored template + keyed-name deltas"`

---

## Task 3: Final verification + adoption proof + docs

- [ ] **Step 1: Adoption check** — every campaign now extends a template:
`grep -rL "extends:" $(grep -rl "kind: Campaign$" --include="*.yaml" google) | grep -v _template` → **empty** (no standalone campaigns). 187 campaigns, all with `metadata.extends`. Report the new template count (13 + new bare-vertical/structure templates) and the net line delta (`git diff --stat origin/main`).

- [ ] **Step 2: Full gate, clean run from the pinned base:**
`AMNESIA_DIR=../amnesia python3 tools/verify_byte_equivalence.py . --base "$(git merge-base origin/main HEAD)"` → 0 diffs.
`AMNESIA_DIR=../amnesia tools/validate.sh` → 0 errors.

- [ ] **Step 3: Structural pre/post diff sanity** — for a sampling of campaigns across verticals (incl. a `love_style` member, a localized `iq` member like `bg-ww`, and a bare-vertical singleton), show the before/after file and confirm the after is a thin delta and expands to the original (spot-check beyond the automated gate).

- [ ] **Step 4: README refresh** — update `README.md`'s scope/structure section to describe the new `_base.yaml` + per-vertical-template + delta layout (the tree description likely says "43 extend"; make it "all campaigns extend a per-vertical template rooted at `_base.yaml`").

- [ ] **Step 5: Commit.** `git add -A && git commit -m "AMS-444: README + final verification for base-anchored corpus"`

---

## Self-review checklist (plan author)
- **Spec coverage** (design §5 WS-C + AMS-444): `_base.yaml` → Task 1; rewire 13 templates + strip defaults → Task 2a; group + synthesize missing templates (incl. 6 bare verticals) + collapse 143 standalone to deltas → Task 2b; byte-equivalence gate → every task; adoption proof + docs → Task 3.
- **Design decision recorded:** uniform per-vertical templates, no campaign extends base directly (precondition 3).
- **Correctness backstop:** `verify_byte_equivalence.py` at 0 diffs is the gate at Task 1 Step 3, Task 2 Steps 2 & 7, Task 3 Step 2 — the migrator is only "done" when it's green on all 187.
- **No placeholders:** the migrator code isn't fully pre-written because its delta logic depends on per-vertical corpus specifics the implementer analyzes; instead each stage is bounded by an exact, runnable gate command that defines "correct." The base file, grouping-key field list, and delta rules are fully specified.
- **Idempotency + determinism** explicitly gated (Task 2 Step 8).
