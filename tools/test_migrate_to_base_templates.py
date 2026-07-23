#!/usr/bin/env python3
"""Unit + end-to-end tests for the AMS-444 corpus migrator.

Pure-function tests build Unit objects directly; the end-to-end test runs the migrator on a tiny
synthetic repo and asserts byte-equivalence of the materialized tree + idempotency. Requires a
sibling amnesia checkout (AMNESIA_DIR or ../../amnesia) for the shared expander."""
import os, copy, pathlib
import pytest
import yaml

import migrate_to_base_templates as M


def _adtpl(name, feed, headlines, desc, dest, extra=None):
    spec = {"status": "ENABLED", "destination_url": dest, "headlines": headlines, "descriptions": desc}
    if extra:
        spec.update(extra)
    return {"name": name, "creative_feed_ref": feed, "spec": spec}


def _adset(name, cpa, ad_templates, extra=None):
    spec = {"status": "PAUSED", "bid_strategy": "TARGET_CPA", "target_cpa_usd": cpa}
    if extra:
        spec.update(extra)
    return {"name": name, "spec": spec, "ad_templates": ad_templates}


def _unit(name, cpa, headlines, dest="https://x/lp", budget=100.0, feed="f", geo="us"):
    at = _adtpl("as1__rsa", feed, headlines, ["d1"], dest)
    return M.Unit(name, "google_search", "iq", {"geo_preset_ref": geo},
                  {"objective": "TRAFFIC", "status": "PAUSED", "budget_daily_usd": budget},
                  [_adset("as1", cpa, [at])])


# ---- signature grouping -----------------------------------------------------------------------

def test_same_skeleton_diff_copy_same_signature():
    a = _unit("a", 10.0, ["EN one", "EN two"])
    b = _unit("b", 99.0, ["DE eins", "DE zwei"], dest="https://y/lp", budget=500.0, geo="de")
    assert M.signature(a) == M.signature(b)


def test_diff_skeleton_diff_signature():
    a = _unit("a", 10.0, ["h"])
    b = copy.deepcopy(a)
    b.ad_sets[0]["ad_templates"][0]["name"] = "as1__rsa2"  # different ad-template name
    assert M.signature(a) != M.signature(b)


def test_diff_feed_diff_signature():
    a = _unit("a", 10.0, ["h"], feed="feed_a")
    b = _unit("b", 10.0, ["h"], feed="feed_b")
    assert M.signature(a) != M.signature(b)


def test_bid_and_status_excluded_from_signature():
    a = _unit("a", 10.0, ["h"])
    b = _unit("b", 77.0, ["h"])  # only bid differs
    assert M.signature(a) == M.signature(b)


# ---- delta computation ------------------------------------------------------------------------

def _tmpl_unit_from(rep):
    """Emulate the pure chain result: strip per-campaign scalars, keep template copy/structure."""
    tu = copy.deepcopy(rep)
    tu.camp_spec = {"objective": "TRAFFIC", "status": "PAUSED"}  # base defaults, no budget/bid
    for a in tu.ad_sets:
        a["spec"] = {"status": "PAUSED", "bid_strategy": "TARGET_CPA"}  # defaults, bid dropped
    return tu


def test_representative_gets_empty_tree_delta():
    rep = _unit("rep", 15.0, ["h1", "h2"])
    delta = M.compute_delta_spec(rep, _tmpl_unit_from(rep))
    assert delta["budget_daily_usd"] == 100.0
    assert delta["target_cpa_usd"] == 15.0
    assert "ad_sets" not in delta            # copy identical to template -> nothing below campaign
    assert "objective" not in delta and "status" not in delta


def test_delta_emits_only_differing_fields_leaf_list_wholesale():
    rep = _unit("rep", 15.0, ["h1", "h2"])
    member = _unit("m", 20.0, ["h1", "CHANGED"])  # same dest, one headline changed
    delta = M.compute_delta_spec(member, _tmpl_unit_from(rep))
    assert delta["target_cpa_usd"] == 20.0
    at = delta["ad_sets"][0]["ad_templates"][0]
    assert at["name"] == "as1__rsa"
    assert at["headlines"] == ["h1", "CHANGED"]   # whole list, not a partial patch
    assert "destination_url" not in at            # unchanged -> omitted
    assert "descriptions" not in at


def test_delta_emits_destination_when_changed():
    rep = _unit("rep", 15.0, ["h1"])
    member = _unit("m", 15.0, ["h1"], dest="https://different/lp")
    delta = M.compute_delta_spec(member, _tmpl_unit_from(rep))
    at = delta["ad_sets"][0]["ad_templates"][0]
    assert at["destination_url"] == "https://different/lp"
    assert "headlines" not in at


def test_delta_raises_on_non_removable_inherited_field():
    rep = _unit("rep", 15.0, ["h1"])
    tu = _tmpl_unit_from(rep)
    tu.ad_sets[0]["ad_templates"][0]["spec"]["business_name"] = "eFAQ"  # template has it, member lacks
    member = _unit("m", 15.0, ["h1"])
    with pytest.raises(RuntimeError, match="non-removable"):
        M.compute_delta_spec(member, tu)


def test_delta_raises_on_non_removable_inherited_adset_field():
    rep = _unit("rep", 15.0, ["h1"])
    tu = _tmpl_unit_from(rep)
    tu.ad_sets[0]["spec"]["audience"] = "aud1"  # template ad-set has it, member lacks
    member = _unit("m", 15.0, ["h1"])
    with pytest.raises(RuntimeError, match="non-removable"):
        M.compute_delta_spec(member, tu)


def test_non_uniform_bid_raises():
    u = _unit("u", 15.0, ["h1"])
    u.ad_sets.append(_adset("as2", 99.0, [_adtpl("as2__rsa", "f", ["h"], ["d"], "https://x/lp")]))
    with pytest.raises(RuntimeError, match="non-uniform bid"):
        M.compute_delta_spec(u, u)


# ---- template synthesis / rewiring ------------------------------------------------------------

def test_synthesize_strips_defaults_keeps_copy_and_feed():
    rep = _unit("rep", 15.0, ["h1"])
    doc = M.synthesize_template_doc(rep, "iq_search_tmpl")
    assert doc["metadata"]["extends"] == "efaq_base"
    a = doc["spec"]["ad_sets"][0]
    assert "status" not in a and "bid_strategy" not in a and "target_cpa_usd" not in a
    at = a["ad_templates"][0]
    assert at["creative_feed_ref"] == "f"
    assert "status" not in at                      # ENABLED default stripped
    assert at["headlines"] == ["h1"]


def test_rewire_strips_base_defaults_and_adds_extends():
    doc = {"apiVersion": "amnesia/v1", "kind": "CampaignTemplate",
           "metadata": {"name": "t", "platform": "google_search", "vertical": "iq"},
           "spec": {"objective": "TRAFFIC", "status": "PAUSED",
                    "ad_sets": [{"name": "as1", "status": "PAUSED", "bid_strategy": "TARGET_CPA",
                                 "ad_templates": [{"name": "x", "status": "ENABLED",
                                                   "destination_url": "u"}]}]}}
    out = M.rewire_template_doc(doc)
    assert out["metadata"]["extends"] == "efaq_base"
    assert "objective" not in out["spec"] and "status" not in out["spec"]
    a = out["spec"]["ad_sets"][0]
    assert "status" not in a and "bid_strategy" not in a
    assert "status" not in a["ad_templates"][0]


def test_rewire_keeps_non_default_values():
    doc = {"apiVersion": "amnesia/v1", "kind": "CampaignTemplate",
           "metadata": {"name": "t", "platform": "google_search", "vertical": "iq"},
           "spec": {"objective": "TRAFFIC", "status": "ENABLED",  # ENABLED != base PAUSED
                    "ad_sets": [{"name": "as1", "status": "PAUSED", "bid_strategy": "TARGET_ROAS",
                                 "ad_templates": [{"name": "x", "status": "ENABLED"}]}]}}
    out = M.rewire_template_doc(doc)
    assert out["spec"]["status"] == "ENABLED"                    # non-default kept
    assert out["spec"]["ad_sets"][0]["bid_strategy"] == "TARGET_ROAS"  # non-default kept


# ---- end-to-end on a synthetic repo -----------------------------------------------------------

CAMPAIGN_TMPL = """\
apiVersion: amnesia/v1
kind: Campaign
metadata:
  name: {name}
  platform: google_search
  vertical: iq
  geo_preset_ref: {geo}
spec:
  objective: TRAFFIC
  status: PAUSED
  budget_daily_usd: {budget}
---
apiVersion: amnesia/v1
kind: AdSet
metadata:
  name: iq_test
  platform: google_search
  vertical: iq
  campaign_ref: {name}
spec:
  status: PAUSED
  bid_strategy: TARGET_CPA
  target_cpa_usd: {cpa}
---
apiVersion: amnesia/v1
kind: AdTemplate
metadata:
  name: iq_test__rsa
  platform: google_search
  vertical: iq
  campaign_ref: {name}
  creative_feed_ref: iq_feed
  ad_set_ref: iq_test
spec:
  status: ENABLED
  destination_url: {dest}
  headlines:
{headlines}
  descriptions:
  - {desc}
"""

BASE_YAML = """\
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
"""


def _write_campaign(d, name, geo, budget, cpa, dest, headlines):
    hl = "\n".join(f"  - {h}" for h in headlines)
    (d / f"{name}.yaml").write_text(
        CAMPAIGN_TMPL.format(name=name, geo=geo, budget=budget, cpa=cpa,
                             dest=dest, headlines=hl, desc="a description"))


def _materialize_repo(root):
    all_docs = M.V.load_all_docs(pathlib.Path(root))
    index = M.V.build_template_index(all_docs)
    out = {}
    for p in M._campaign_files(str(root)):
        rel = os.path.relpath(p, root)
        out[rel] = [(k, n, b) for k, n, b in M._materialize(M._load_docs(p), index)]
    return out


@pytest.fixture
def mini_repo(tmp_path):
    (tmp_path / "_base.yaml").write_text(BASE_YAML)
    d = tmp_path / "google" / "search" / "iq"
    d.mkdir(parents=True)
    # two share a skeleton (diff copy), one has a different skeleton handled below
    _write_campaign(d, "a_en", "us", 100.0, 10.0, "https://x/en", ["EN 1", "EN 2"])
    _write_campaign(d, "b_de", "de", 500.0, 20.0, "https://x/de", ["DE 1", "DE 2"])
    return tmp_path


def test_end_to_end_byte_equivalent_and_collapses_to_one_template(mini_repo):
    golden = _materialize_repo(mini_repo)
    M.run(str(mini_repo), "full")
    after = _materialize_repo(mini_repo)
    assert golden == after, "materialized tree changed — not byte-equivalent"

    # both campaigns now extend one synthesized template
    tmpls = list((mini_repo / "google" / "search" / "iq").glob("_template*.yaml"))
    assert len(tmpls) == 1
    a = list(yaml.safe_load_all((mini_repo / "google/search/iq/a_en.yaml").read_text()))
    b = list(yaml.safe_load_all((mini_repo / "google/search/iq/b_de.yaml").read_text()))
    assert a[0]["metadata"]["extends"] == b[0]["metadata"]["extends"]
    # representative (alphabetically-first path: a_en) has no tree delta
    assert "ad_sets" not in a[0]["spec"]
    # member overrides copy wholesale
    assert b[0]["spec"]["ad_sets"][0]["ad_templates"][0]["headlines"] == ["DE 1", "DE 2"]


def test_end_to_end_idempotent(mini_repo):
    M.run(str(mini_repo), "full")
    snapshot = {p: p.read_text() for p in (mini_repo / "google").rglob("*.yaml")}
    M.run(str(mini_repo), "full")
    after = {p: p.read_text() for p in (mini_repo / "google").rglob("*.yaml")}
    assert snapshot == after, "second run changed files — not idempotent"


def test_end_to_end_distinct_skeleton_makes_second_template(tmp_path):
    (tmp_path / "_base.yaml").write_text(BASE_YAML)
    d = tmp_path / "google" / "search" / "iq"
    d.mkdir(parents=True)
    _write_campaign(d, "a_en", "us", 100.0, 10.0, "https://x/en", ["EN 1"])
    # a genuinely different skeleton: rename the ad-set/ad-template
    txt = CAMPAIGN_TMPL.format(name="c_x", geo="us", budget=100.0, cpa=10.0,
                               dest="https://x/c", headlines="  - H", desc="d")
    txt = txt.replace("iq_test", "other_set")  # rename ad-set + ad-template -> distinct skeleton
    (d / "c_x.yaml").write_text(txt)
    golden = _materialize_repo(tmp_path)
    M.run(str(tmp_path), "full")
    assert golden == _materialize_repo(tmp_path)
    tmpls = sorted((d).glob("_template*.yaml"))
    assert len(tmpls) == 2  # two distinct structures -> two templates


EXISTING_TEMPLATE = """\
apiVersion: amnesia/v1
kind: CampaignTemplate
metadata:
  name: iq_search_tmpl
  platform: google_search
  vertical: iq
spec:
  objective: TRAFFIC
  status: PAUSED
  ad_sets:
  - name: iq_test
    status: PAUSED
    bid_strategy: TARGET_CPA
    ad_templates:
    - name: iq_test__rsa
      status: ENABLED
      creative_feed_ref: iq_feed
      destination_url: https://x/lp
      headlines:
      - H1
      descriptions:
      - d1
"""


def test_stage_templates_rewires_only_templates(tmp_path):
    (tmp_path / "_base.yaml").write_text(BASE_YAML)
    d = tmp_path / "google" / "search" / "iq"
    d.mkdir(parents=True)
    (d / "_template.yaml").write_text(EXISTING_TEMPLATE)
    _write_campaign(d, "a_en", "us", 100.0, 10.0, "https://x/en", ["EN 1"])
    campaign_before = (d / "a_en.yaml").read_text()

    M.run(str(tmp_path), "templates")

    tdoc = list(yaml.safe_load_all((d / "_template.yaml").read_text()))[0]
    assert tdoc["metadata"]["extends"] == "efaq_base"     # extends added
    assert "objective" not in tdoc["spec"]                 # base-default scalars stripped
    assert "status" not in tdoc["spec"]
    a = tdoc["spec"]["ad_sets"][0]
    assert "status" not in a and "bid_strategy" not in a
    assert "status" not in a["ad_templates"][0]
    assert (d / "a_en.yaml").read_text() == campaign_before  # campaign file untouched
