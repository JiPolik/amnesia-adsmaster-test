#!/usr/bin/env python3
"""One-shot migration: bring amnesia-adsmaster-test to the current config model (AMS-440).

Four transforms on scoped kinds (Campaign/AdSet/AdTemplate); scope-free kinds untouched:
  T1  inject metadata.platform + metadata.vertical, derived from the file path
  T2  move ad_set_ref + creative_feed_ref from spec -> metadata (AdTemplate)
  T3  restore geo_preset_ref into Campaign metadata (dropped in AMS-400, commit e5e2682)
  T4  drop spec.demand_gen.lifecycle_goal on AdSet, retired by AMS-429

Idempotent + deterministic. Byte-shape via the same PyYAML kwargs + "---\\n" join as the
corpus migrate_refs_to_metadata.py (AMS-439).

Usage: migrate_to_metadata_model.py <repo_root>
"""
import sys
import os
import glob
import yaml

SCOPED = {"Campaign", "AdSet", "AdTemplate"}
MOVE_REFS = ("ad_set_ref", "creative_feed_ref")  # AdTemplate: spec -> metadata


def derive_scope(rel_path):
    """(platform, vertical) from the repo-relative path. Fail loud on an unknown layout."""
    parts = rel_path.split("/")
    if len(parts) >= 3 and parts[0] == "google" and parts[1] == "search":
        return "google_search", parts[2]
    if len(parts) >= 3 and parts[0] == "google" and parts[1] == "demandgen":
        return "google_demand_gen", parts[2]
    if len(parts) >= 2 and parts[0] == "openai":
        return "openai", parts[1]
    raise ValueError(f"cannot derive platform/vertical from path: {rel_path}")


def inject_scope(doc, platform, vertical):
    """Set metadata.platform/vertical on scoped kinds. Idempotent. Returns True if changed."""
    if doc.get("kind") not in SCOPED:
        return False
    md = doc.get("metadata") or {}
    doc["metadata"] = md
    changed = False
    if md.get("platform") != platform:
        md["platform"] = platform
        changed = True
    if md.get("vertical") != vertical:
        md["vertical"] = vertical
        changed = True
    return changed


def move_refs(doc):
    """Move AdTemplate ad_set_ref + creative_feed_ref from spec to metadata. Returns True if changed."""
    if doc.get("kind") != "AdTemplate":
        return False
    spec = doc.get("spec") or {}
    md = doc.get("metadata") or {}
    doc["metadata"] = md
    changed = False
    for k in MOVE_REFS:
        if k in spec:
            md[k] = spec.pop(k)
            changed = True
    return changed


# geo_preset_ref per campaign file — the values AMS-400 (commit e5e2682) removed,
# recovered from its pre-image e5e2682^. Every value resolves to an existing GeoPreset doc.
GEO_MAP = {
    "google/demandgen/iq/en-gb.yaml": "gb",
    "google/demandgen/iq/en-us.yaml": "us",
    "google/demandgen/iq/pl-ww.yaml": "main_markets",
    "google/demandgen/leadership/en-us.yaml": "us",
    "google/demandgen/spiritanimal/de-ww.yaml": "main_markets",
    "google/demandgen/spiritanimal/en-ca.yaml": "ca",
    "google/demandgen/spiritanimal/en-gb.yaml": "gb",
    "google/demandgen/spiritanimal/en-us.yaml": "us",
    "google/search/autism/de-ww.yaml": "main_markets",
    "google/search/autism/en-gb.yaml": "gb",
    "google/search/autism/en-us.yaml": "us",
    "google/search/career/de-ww.yaml": "main_markets",
    "google/search/career/en-gb.yaml": "gb",
    "google/search/career/pl-ww.yaml": "main_markets",
    "google/search/chat/en-us.yaml": "us",
    "google/search/exback/de-ww.yaml": "main_markets",
    "google/search/exback/en-gb.yaml": "gb",
    "google/search/exback/en-us.yaml": "us",
    "google/search/exback/pl-ww.yaml": "main_markets",
    "google/search/iq/de-ww.yaml": "main_markets",
    "google/search/iq/en-ca.yaml": "ca",
    "google/search/iq/en-gb.yaml": "gb",
    "google/search/iq/en-ie.yaml": "ie",
    "google/search/iq/en-oc.yaml": "oceania",
    "google/search/iq/en-us.yaml": "us",
    "google/search/leadership/de-ww.yaml": "main_markets",
    "google/search/leadership/en-us.yaml": "us",
    "google/search/leadership/pl-ww.yaml": "main_markets",
    "google/search/licenseplate/en-us.yaml": "us",
    "google/search/licenseplate/es-us.yaml": "us",
    "google/search/lovestyle/de-ww.yaml": "main_markets",
    "google/search/lovestyle/en-gb.yaml": "gb",
    "google/search/lovestyle/pl-ww.yaml": "main_markets",
    "google/search/personality/de-ww.yaml": "main_markets",
    "google/search/personality/en-gb.yaml": "gb",
    "google/search/psychopath/en-gb.yaml": "gb",
    "google/search/psychopath/en-ie.yaml": "ie",
    "google/search/psychopath/en-us.yaml": "us",
    "google/search/spiritanimal/de-ww.yaml": "main_markets",
    "google/search/spiritanimal/en-gb.yaml": "gb",
    "google/search/spiritanimal/en-oc.yaml": "oceania",
    "openai/iq/en-us.yaml": "us",
}


def restore_geo(doc, rel_path):
    """Restore geo_preset_ref into Campaign metadata from GEO_MAP. Idempotent. Returns True if changed."""
    if doc.get("kind") != "Campaign":
        return False
    ref = GEO_MAP.get(rel_path)
    if ref is None:
        return False
    md = doc.get("metadata") or {}
    doc["metadata"] = md
    if md.get("geo_preset_ref") == ref:
        return False
    md["geo_preset_ref"] = ref
    return True


def strip_retired_fields(doc):
    """Drop AMS-429-retired ad-set field spec.demand_gen.lifecycle_goal. Returns True if changed."""
    if doc.get("kind") != "AdSet":
        return False
    spec = doc.get("spec") or {}
    dg = spec.get("demand_gen")
    if isinstance(dg, dict) and "lifecycle_goal" in dg:
        del dg["lifecycle_goal"]
        return True
    return False


def dump_doc(doc):
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=4096)


def migrate_file(path, root):
    """Apply T1–T4 to every doc in a file. Rewrite only if something changed. Returns True if changed."""
    rel = os.path.relpath(path, root)
    platform, vertical = derive_scope(rel)  # fail loud on an unknown layout
    with open(path) as fh:
        docs = [d for d in yaml.safe_load_all(fh) if d is not None]
    results = []
    for d in docs:
        c1 = inject_scope(d, platform, vertical)
        c2 = move_refs(d)
        c3 = restore_geo(d, rel)
        c4 = strip_retired_fields(d)
        results.append(c1 or c2 or c3 or c4)
    changed = any(results)
    if changed:
        with open(path, "w") as fh:
            fh.write("---\n".join(dump_doc(d) for d in docs))
    return changed


def main():
    root = os.path.abspath(sys.argv[1])
    files = sorted(
        glob.glob(os.path.join(root, "google", "**", "*.yaml"), recursive=True)
        + glob.glob(os.path.join(root, "openai", "**", "*.yaml"), recursive=True)
    )
    changed = sum(1 for f in files if migrate_file(f, root))
    print(f"migrated {changed}/{len(files)} files")


if __name__ == "__main__":
    main()
