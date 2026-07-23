#!/usr/bin/env python3
"""One-shot spec→metadata cross-ref migration (AMS-439).

Moves the cross-refs AMS-427 relocated to metadata but the corpus still authored in spec:
  - Campaign:   geo_preset_ref, geo_exclude_refs, ad_extension_refs
  - AdTemplate: creative_feed_ref, ad_set_ref
CampaignTemplate docs are left untouched (their nested ad_templates[].creative_feed_ref stays
inline; the backend ExtendsExpander hoists it). Idempotent: a ref already only in metadata is
left alone. Byte-shape fidelity uses the same safe_dump kwargs + "---\\n" join as convert.py.

Usage: migrate_refs_to_metadata.py <repo_root>   (rewrites the corpus in place)
"""
import sys, os, glob
import yaml

MOVE = {
    "Campaign":   ("geo_preset_ref", "geo_exclude_refs", "ad_extension_refs"),
    "AdTemplate": ("creative_feed_ref", "ad_set_ref"),
}


def dump_doc(doc):
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=4096)


def move_refs(doc):
    """Move this doc's spec-authored cross-refs into metadata. Returns True if it changed."""
    keys = MOVE.get(doc.get("kind"))
    if not keys:
        return False
    spec = doc.get("spec") or {}
    md = doc.setdefault("metadata", {})
    changed = False
    for k in keys:
        if k in spec:
            md[k] = spec.pop(k)
            changed = True
    return changed


def migrate_file(path):
    docs = [d for d in yaml.safe_load_all(open(path)) if d]
    # list-comp (not a generator) so every doc is evaluated; a generator would let any() short-circuit.
    changed = any([move_refs(d) for d in docs])
    if changed:
        with open(path, "w") as fh:
            fh.write("---\n".join(dump_doc(d) for d in docs))
    return changed


def main():
    root = os.path.abspath(sys.argv[1])
    files = sorted(glob.glob(os.path.join(root, "google", "**", "*.yaml"), recursive=True))
    changed = sum(1 for f in files if migrate_file(f))
    print(f"migrated {changed}/{len(files)} files (spec→metadata cross-refs)")


if __name__ == "__main__":
    main()
