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
- `geo/`                        → GeoPreset docs (unscoped; authored but not yet referenced — see below)

## Provenance & documented differences from the real `ads` repo

The Amnesia v2 model is narrower than the legacy eFAQ config format. These legacy fields have **no
Amnesia v2 slot and are intentionally dropped**: `keywords`, `sitelinks`, `callouts`,
`structured_snippets`, `network_settings`, `campaign_group`, multiple RSAs per ad-group (collapsed to
one headline/description set), and multi-size image variants (images come from the generation feed,
not this repo).

Other conventions: campaigns use `objective: TRAFFIC` and `status: PAUSED` (no real spend on staging);
legacy `maximize_conversions` + `cpa_target` maps to `bid_strategy: TARGET_CPA` + `target_cpa_usd`.

**Geo targeting is deferred.** The `geo/` directory carries real `GeoPreset` docs (`us`, `gb`, `ca`,
`ie`, `oceania`, and a `main_markets` explicit-country expansion of the legacy `WW`/`MAIN_MARKETS`
geos), but campaigns do **not** yet set `geo_preset_ref`: git-sync has no `GeoPreset` materializer
(the sibling of the AdTemplate materializer from AMS-385 and the CreativeFeed materializer from
AMS-387), so a git-authored geo preset never lands in the org and `geo_preset_ref` can't resolve —
which would skip every ad-set/ad-template on materialization. Once a git-native `GeoPreset`
materializer ships, campaigns can reference these presets. The presets are kept here so that change
is a one-line-per-campaign edit.

`creative_feed_ref` / `logo_asset_ref` are real names but resolve to live image bytes only once
AMS-409 lands.
