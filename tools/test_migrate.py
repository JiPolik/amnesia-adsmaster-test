# tools/test_migrate.py
import pytest
from migrate_to_metadata_model import derive_scope


def test_derive_scope_google_search():
    assert derive_scope("google/search/career/pl-ww.yaml") == ("google_search", "career")


def test_derive_scope_google_demandgen():
    assert derive_scope("google/demandgen/iq/en-us.yaml") == ("google_demand_gen", "iq")


def test_derive_scope_openai():
    assert derive_scope("openai/iq/en-us.yaml") == ("openai", "iq")


def test_derive_scope_unknown_layout_raises():
    with pytest.raises(ValueError):
        derive_scope("meta/whatever/x.yaml")


from migrate_to_metadata_model import inject_scope


def test_inject_scope_sets_platform_vertical_on_scoped():
    doc = {"kind": "Campaign", "metadata": {"name": "x"}, "spec": {}}
    changed = inject_scope(doc, "google_search", "career")
    assert changed is True
    assert doc["metadata"] == {"name": "x", "platform": "google_search", "vertical": "career"}


def test_inject_scope_skips_scope_free_kinds():
    doc = {"kind": "CreativeFeed", "metadata": {"name": "f"}, "spec": {}}
    assert inject_scope(doc, "google_search", "career") is False
    assert "platform" not in doc["metadata"]


def test_inject_scope_idempotent():
    doc = {"kind": "AdSet", "metadata": {"name": "a", "platform": "openai", "vertical": "iq"}}
    assert inject_scope(doc, "openai", "iq") is False


from migrate_to_metadata_model import move_refs


def test_move_refs_moves_both_from_spec_to_metadata():
    doc = {"kind": "AdTemplate",
           "metadata": {"name": "t", "campaign_ref": "c"},
           "spec": {"ad_set_ref": "main", "creative_feed_ref": "feed", "status": "ENABLED"}}
    changed = move_refs(doc)
    assert changed is True
    assert doc["metadata"]["ad_set_ref"] == "main"
    assert doc["metadata"]["creative_feed_ref"] == "feed"
    assert "ad_set_ref" not in doc["spec"]
    assert "creative_feed_ref" not in doc["spec"]
    assert doc["spec"] == {"status": "ENABLED"}  # other spec keys untouched


def test_move_refs_ignores_non_adtemplate():
    doc = {"kind": "AdSet", "metadata": {"name": "a"}, "spec": {"ad_set_ref": "x"}}
    assert move_refs(doc) is False


def test_move_refs_idempotent_when_already_in_metadata():
    doc = {"kind": "AdTemplate",
           "metadata": {"name": "t", "ad_set_ref": "main", "creative_feed_ref": "feed"},
           "spec": {"status": "ENABLED"}}
    assert move_refs(doc) is False


from migrate_to_metadata_model import restore_geo, GEO_MAP


def test_geo_map_is_42_entries_all_known_presets():
    assert len(GEO_MAP) == 42
    assert set(GEO_MAP.values()) <= {"ca", "gb", "ie", "main_markets", "oceania", "us"}


def test_restore_geo_sets_ref_from_map():
    doc = {"kind": "Campaign", "metadata": {"name": "x"}, "spec": {}}
    changed = restore_geo(doc, "google/search/career/pl-ww.yaml")
    assert changed is True
    assert doc["metadata"]["geo_preset_ref"] == "main_markets"


def test_restore_geo_ignores_non_campaign():
    doc = {"kind": "AdSet", "metadata": {"name": "a"}}
    assert restore_geo(doc, "google/search/career/pl-ww.yaml") is False


def test_restore_geo_idempotent():
    doc = {"kind": "Campaign", "metadata": {"name": "x", "geo_preset_ref": "us"}, "spec": {}}
    assert restore_geo(doc, "openai/iq/en-us.yaml") is False


import pathlib


def test_geo_map_keys_are_real_campaign_files():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    missing = [k for k in GEO_MAP if not (repo_root / k).is_file()]
    assert missing == [], f"GEO_MAP keys with no matching file: {missing}"


from migrate_to_metadata_model import strip_retired_fields


def test_strip_retired_fields_removes_lifecycle_goal_keeps_audiences():
    doc = {"kind": "AdSet", "metadata": {"name": "a"},
           "spec": {"demand_gen": {"lifecycle_goal": "NEW_CUSTOMER_ACQUISITION", "audience_user_lists": ["x"]}}}
    assert strip_retired_fields(doc) is True
    assert "lifecycle_goal" not in doc["spec"]["demand_gen"]
    assert doc["spec"]["demand_gen"]["audience_user_lists"] == ["x"]


def test_strip_retired_fields_noop_without_lifecycle_goal():
    doc = {"kind": "AdSet", "metadata": {"name": "a"}, "spec": {"demand_gen": {"audience_user_lists": ["x"]}}}
    assert strip_retired_fields(doc) is False


def test_strip_retired_fields_ignores_non_adset():
    doc = {"kind": "Campaign", "metadata": {"name": "c"}, "spec": {"demand_gen": {"lifecycle_goal": "X"}}}
    assert strip_retired_fields(doc) is False


import textwrap
from migrate_to_metadata_model import migrate_file


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip("\n"))
    return p


def test_migrate_file_applies_all_three_transforms(tmp_path):
    rel = "google/search/career/pl-ww.yaml"
    _write(tmp_path, rel, """
        apiVersion: amnesia/v1
        kind: Campaign
        metadata: {name: pl_ww_goog_s_efaq_career}
        spec:
          objective: TRAFFIC
          status: PAUSED
          budget_daily_usd: 900
        ---
        apiVersion: amnesia/v1
        kind: AdTemplate
        metadata: {name: career_rsa, campaign_ref: pl_ww_goog_s_efaq_career}
        spec:
          status: ENABLED
          ad_set_ref: career
          creative_feed_ref: career_search_feed
          business_name: "eFAQ.com"
    """)
    changed = migrate_file(str(tmp_path / rel), str(tmp_path))
    assert changed is True

    import yaml
    docs = list(yaml.safe_load_all((tmp_path / rel).read_text()))
    camp, tmpl = docs[0], docs[1]
    # T1
    assert camp["metadata"]["platform"] == "google_search"
    assert camp["metadata"]["vertical"] == "career"
    assert tmpl["metadata"]["platform"] == "google_search"
    # T3
    assert camp["metadata"]["geo_preset_ref"] == "main_markets"
    # T2
    assert tmpl["metadata"]["ad_set_ref"] == "career"
    assert tmpl["metadata"]["creative_feed_ref"] == "career_search_feed"
    assert "ad_set_ref" not in tmpl["spec"]
    assert "creative_feed_ref" not in tmpl["spec"]
    # spec values preserved
    assert camp["spec"] == {"objective": "TRAFFIC", "status": "PAUSED", "budget_daily_usd": 900}
    assert tmpl["spec"] == {"status": "ENABLED", "business_name": "eFAQ.com"}


def test_migrate_file_is_idempotent(tmp_path):
    rel = "openai/iq/en-us.yaml"
    _write(tmp_path, rel, """
        apiVersion: amnesia/v1
        kind: Campaign
        metadata: {name: en_us_openai_efaq_iq}
        spec: {objective: TRAFFIC, status: PAUSED, budget_daily_usd: 500}
    """)
    assert migrate_file(str(tmp_path / rel), str(tmp_path)) is True
    first = (tmp_path / rel).read_text()
    assert migrate_file(str(tmp_path / rel), str(tmp_path)) is False   # second run: no change
    assert (tmp_path / rel).read_text() == first


def test_migrate_file_strips_lifecycle_goal_keeps_audiences(tmp_path):
    rel = "google/demandgen/spiritanimal/en-ca.yaml"
    _write(tmp_path, rel, """
        apiVersion: amnesia/v1
        kind: AdSet
        metadata: {name: multiasset, campaign_ref: en_ca_goog_dg_efaq_spiritanimal}
        spec:
          status: PAUSED
          bid_strategy: TARGET_CPA
          target_cpa_usd: 22
          demand_gen:
            lifecycle_goal: NEW_CUSTOMER_ACQUISITION
            audience_user_lists:
            - spiritanimal-visitors
    """)
    assert migrate_file(str(tmp_path / rel), str(tmp_path)) is True
    import yaml
    d = list(yaml.safe_load_all((tmp_path / rel).read_text()))[0]
    assert d["metadata"]["platform"] == "google_demand_gen"
    assert d["metadata"]["vertical"] == "spiritanimal"
    assert "lifecycle_goal" not in d["spec"]["demand_gen"]
    assert d["spec"]["demand_gen"]["audience_user_lists"] == ["spiritanimal-visitors"]
