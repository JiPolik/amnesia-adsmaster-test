# amnesia-adsmaster-test

**v2 (`schema_version: 2`) fixture repo** for the Amnesia git-sync → materialize → reconcile flow.
Contents are a curated subset of the real eFAQ ad configs from `haremedia/ads`, re-expressed in
the Amnesia v2 config format (AMS-400).

## Layout

`.amnesia/config.yml` activates v2 mode for the whole repo. Every `*.yaml` file is a multi-document
YAML stream of `kind:` envelopes. Platform objects (Campaign/AdSet/AdTemplate) are scoped by their
directory path `<platform>/<vertical>/…`:

- `google/search/<vertical>/`   → Google Search
- `google/demandgen/<vertical>/`→ Google Demand Gen
- `openai/<vertical>/`          → OpenAI Ads
- `feeds/`                      → CreativeFeed docs (unscoped)
- `geo/`                        → GeoPreset docs (unscoped)

## Provenance & documented differences from the real `ads` repo

The Amnesia v2 model is narrower than the legacy eFAQ config format. These legacy fields have **no
Amnesia v2 slot and are intentionally dropped**: `keywords`, `sitelinks`, `callouts`,
`structured_snippets`, `network_settings`, `campaign_group`, multiple RSAs per ad-group (collapsed to
one headline/description set), and multi-size image variants (images come from the generation feed,
not this repo).

Other conventions: campaigns use `objective: TRAFFIC` and `status: PAUSED` (no real spend on staging);
legacy `maximize_conversions` + `cpa_target` maps to `bid_strategy: TARGET_CPA` + `target_cpa_usd`;
named legacy geos (`WW`/`MAIN_MARKETS`) are approximated by an explicit-country `main_markets` preset.
`creative_feed_ref` / `logo_asset_ref` are real names but resolve to live image bytes only once
AMS-409 lands.
