"""Tests for the one-shot spec→metadata cross-ref migration (AMS-439)."""
import subprocess, sys, pathlib, textwrap
import yaml

SCRIPT = pathlib.Path(__file__).resolve().parent / "migrate_refs_to_metadata.py"


def run(root):
    subprocess.run([sys.executable, str(SCRIPT), str(root)], check=True)


def test_moves_campaign_and_adtemplate_refs_and_is_idempotent(tmp_path):
    f = tmp_path / "google" / "search" / "chat" / "en-us.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(textwrap.dedent("""
        apiVersion: amnesia/v1
        kind: Campaign
        metadata:
          name: c1
          platform: google_search
          vertical: chat
        spec:
          objective: TRAFFIC
          status: PAUSED
          geo_preset_ref: us
          geo_exclude_refs: [xx, yy]
          ad_extension_refs: [ext1]
        ---
        apiVersion: amnesia/v1
        kind: AdTemplate
        metadata:
          name: t1
          platform: google_search
          vertical: chat
          campaign_ref: c1
        spec:
          status: ENABLED
          ad_set_ref: as1
          creative_feed_ref: f1
          headlines: [a, b, c]
          descriptions: [d, e]
          destination_url: https://x.com
    """).lstrip())
    run(tmp_path)
    docs = [d for d in yaml.safe_load_all(f.read_text()) if d]
    camp, at = docs[0], docs[1]
    assert camp["metadata"]["geo_preset_ref"] == "us"
    assert camp["metadata"]["geo_exclude_refs"] == ["xx", "yy"]
    assert camp["metadata"]["ad_extension_refs"] == ["ext1"]
    assert "geo_preset_ref" not in camp["spec"]
    assert "geo_exclude_refs" not in camp["spec"] and "ad_extension_refs" not in camp["spec"]
    assert at["metadata"]["creative_feed_ref"] == "f1"
    assert at["metadata"]["ad_set_ref"] == "as1"
    assert "creative_feed_ref" not in at["spec"] and "ad_set_ref" not in at["spec"]
    # idempotent: a second run leaves the file byte-identical
    before = f.read_text()
    run(tmp_path)
    assert f.read_text() == before


def test_campaign_template_untouched(tmp_path):
    f = tmp_path / "google" / "search" / "autism" / "_template.yaml"
    f.parent.mkdir(parents=True)
    original = textwrap.dedent("""
        apiVersion: amnesia/v1
        kind: CampaignTemplate
        metadata:
          name: tmpl
          platform: google_search
          vertical: autism
        spec:
          objective: TRAFFIC
          status: PAUSED
          ad_sets:
          - name: a1
            ad_templates:
            - name: t1
              creative_feed_ref: feed1
              status: ENABLED
    """).lstrip()
    f.write_text(original)
    run(tmp_path)
    assert f.read_text() == original  # skipped → byte-identical
