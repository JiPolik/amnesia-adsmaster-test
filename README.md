# amnesia-campaigns

Git-as-source-of-truth campaign configuration for the **HareMedia** org in Amnesia
(`schema_version: 2`, kind-based YAML). Amnesia clones this repo, validates the docs,
and reconciles them to Google Ads. Dashboard budget/bid edits flow back here as commits.

## Layout

```
_base.yaml                                   # global CampaignTemplate `efaq_base` (universal defaults)
.amnesia/config.yml                          # v2 RepoConfig: Google account + credentials
geo/<file>.yaml                              # GeoPreset + GeoBlocklist docs
feeds/<file>.yaml                            # CreativeFeed docs
google/search/<vertical>/<locale>.yaml       # Search: Campaign (thin extends + delta)
google/search/<vertical>/_template*.yaml     # Search: per-vertical CampaignTemplate (extends efaq_base)
google/demandgen/<vertical>/<locale>.yaml    # Demand Gen: Campaign (thin extends + delta)
google/demandgen/<vertical>/_template*.yaml  # Demand Gen: per-vertical CampaignTemplate (extends efaq_base)
```

Each doc carries its `(platform, vertical)` scope in `metadata.platform` / `metadata.vertical`
(AMS-425) — the directory tree is now **advisory** for humans, not the source of scope, and
filenames carry no semantic meaning. All cross-references (`campaign_ref`, `geo_preset_ref`,
`creative_feed_ref`, `ad_set_ref`, `geo_exclude_refs`, `ad_extension_refs`, `extends`) live in
`metadata`, never `spec` (AMS-427/439). Files outside this layout (this README, `.idea/`) are
ignored by the sync.

## Campaign templates (`extends`, AMS-426 / AMS-444)

The corpus is fully **base-anchored**. A single global, unscoped `_base.yaml`
(`kind: CampaignTemplate`, name `efaq_base`) holds the universal defaults every campaign shares —
`objective`, `status`, and the `ad_set_defaults` / `ad_template_defaults` blocks. One
per-(platform, vertical) `_template*.yaml` sits above it (`metadata.extends: efaq_base`) and authors
that vertical's shared `ad_sets` / `ad_templates` tree (headlines, descriptions, `creative_feed_ref`,
`destination_url`) once. Every concrete `Campaign` then `extends` its vertical template and shrinks
to a thin delta: `metadata.geo_preset_ref` plus a flat `spec` (`budget_daily_usd`, `target_cpa_usd`),
overriding locale-specific copy only where it diverges from the template (keyed by ad-set /
ad-template `name`).

The extends chain is therefore **`Campaign → <vertical> _template → _base.yaml`**. git-sync expands
each `Campaign` back into its full `Campaign + AdSet(s) + AdTemplate(s)` tree before reconciliation,
so the **materialized output is byte-identical** to the pre-template corpus (machine-verified by
`tools/verify_byte_equivalence.py`). All 187 campaigns now extend a template — no standalone
campaigns remain — collapsing the corpus onto 23 templates (22 per-vertical + the shared base) and
eliminating every byte-duplicated ad tree.

## Provenance

Generated from the legacy `haremedia/ads-archive` eFAQ corpus (read-only source) by the AMS-401
deterministic converter. **Zero fabrication:** every headline, description, URL, and asset ref is
verbatim from the source JSON (machine-verified against a provenance map). Account is the test
account `4616809274` (creds `hmg_test`) — staging-testable; the real-prod account swap is a
one-line change at the cutover (AMS-84).

## Documented lossiness (legacy fields with no amnesia v2 slot — dropped)

The converter dropped the following legacy field classes (they are not representable in v2 today;
re-adding any of them to prod is a separate schema-extension ticket):

- `keywords` (Search match data — no v2 keyword kind yet). Ad extensions (`sitelinks`, `callouts`,
  `structured_snippets`) now have a v2 git surface via `kind: AdExtension` (AMS-428), but this
  corpus does not yet author any.
- `network_settings`, `campaign_group`, `campaign_images`, `multi_size_image_set`
- Demand Gen ads with a 5th+ description: truncated to the schema cap of 4 (source order preserved)
- **Geo targeting on non-ISO region codes**: resolved by AMS-414 (see below) for all codes
  except `PRESET_DXG_CARIBBEAN_ENGLISH_MARKETS` (3 campaigns — country list exists nowhere
  in code; pending an authoritative list, AMS-415).

## Scaffolding files excluded

The converter intentionally skips legacy scaffolding files that are not real campaigns:
`shared.json` (shared asset pools, no campaign objects), `template.json` (English placeholder
duplicating a real localized campaign), and `temporary.json` (empty-locale drafts). Reconciliation:
190 source campaign objects − 1 duplicate placeholder − 2 empty-locale drafts = 187 emitted.

## Bidding mapping

All ad-sets use `TARGET_CPA`. This is intentional: the legacy corpus uses
`maximize_conversions` (with a `cpa_target`, 171 campaigns) and `target_cpa` (16) — Google
merged "Maximize Conversions with a target CPA" into Target CPA, so both map to `TARGET_CPA`
with the source `cpa_target` carried verbatim as `target_cpa_usd`. The corpus contains no
tROAS (`maximize_conversion_value`) campaigns.

## Inventory

Counts below are **materialized** entities (after `extends` expansion). The repo authors 187
`Campaign` (all thin `extends` deltas) + 23 `CampaignTemplate` (22 per-vertical + the shared
`efaq_base`); `AdSet` / `AdTemplate` trees are no longer standalone docs — they live inline in the
templates (and in the few campaigns with locale-specific copy overrides) and materialize on
expansion. Plus 16 `CreativeFeed`, 13 `GeoPreset`, and 1 `GeoBlocklist`.

**google/search** — 171 campaigns / 207 ad-sets / 207 ad-templates

| Vertical | Campaigns | Ad-sets | Ad-templates |
|---|---:|---:|---:|
| autism | 9 | 9 | 9 |
| career | 30 | 30 | 30 |
| chat | 1 | 2 | 2 |
| ex_back | 9 | 9 | 9 |
| greek_gods | 1 | 1 | 1 |
| iq | 35 | 70 | 70 |
| leadership | 10 | 10 | 10 |
| license_plate | 2 | 2 | 2 |
| love_style | 27 | 27 | 27 |
| normalcy | 1 | 1 | 1 |
| personality | 28 | 28 | 28 |
| psychopath | 12 | 12 | 12 |
| spirit_animal | 6 | 6 | 6 |

**google/demandgen** — 16 campaigns / 16 ad-sets / 42 ad-templates

| Vertical | Campaigns | Ad-sets | Ad-templates |
|---|---:|---:|---:|
| iq | 4 | 4 | 10 |
| leadership | 1 | 1 | 3 |
| spirit_animal | 11 | 11 | 29 |

## Validation

`./tools/validate.sh` validates every doc against the canonical amnesia v2 schemas — it runs the
validator from a sibling `haremedia/amnesia` checkout (no schemas vendored here), checks
cross-references, and enforces Google's real RSA/Demand-Gen character limits (Search headline ≤30,
DG ≤40, description ≤90) — stricter than the git schema's lenient `maxLength`. CI runs the same
validator via the `validate-config` GitHub Action from `haremedia/amnesia`
(`.github/workflows/validate.yml`) on every PR. `tools/convert.py` + `tools/_provenance.json` are the
converter and the zero-fabrication audit trail (every field → source location).

## Geo presets (AMS-414)

Region presets in `geo/geo-presets.yaml`, pointed at by 144 formerly geo-less campaigns:

| preset | source | countries |
|---|---|---|
| `worldwide_ex_sanctioned` | `worldwide: true` (Google "All countries and territories") with an explicit `exclude` of `UA RU BY VE IR KP CU` (AMS-418 — replaced the earlier 215-country enumerate-include) | all − 7 |
| `europe` / `anglosphere` / `oceania` / `main_markets` | verbatim from prod `platform-google` `Geocode.java` (enum order; `GB` dedup in main_markets) | 41 / 6 / 2 / 49 |

Provenance tooling: `tools/ams414_geo_apply.py` (deterministic generator/applier) +
`tools/geotargets-2026-07-06-countries.json` (pinned country set). YAML note: country code
`NO` (Norway) must stay quoted (`'NO'`) — bare `NO` parses as boolean false.

The earlier enumerate-include worldwide preset has been replaced by `worldwide: true` +
subtractive composition (AMS-418) layered atop the global blocklist below (AMS-417); the geo epic
AMS-416 is code-complete.

## Global geo blocklist (AMS-417)

`geo/blocklist.yaml` (`kind: GeoBlocklist`, singleton) is the org-wide block list every campaign on
every platform excludes on each sync — editing it re-reconciles the whole fleet (no per-campaign
edits). Seeded with `UA RU BY VE IR KP CU` (sanctioned + business-risk), mirroring the hardcoded
`platform-google` `Geocode.BLOCKED` floor beneath it. Add e.g. `AT` to stop all campaigns targeting
Austria. `tools/validate.sh` enforces the singleton + flags any GeoPreset include that overlaps it.
