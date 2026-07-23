#!/usr/bin/env python3
"""Deterministic CampaignTemplate + extends migration (AMS-426, Task 8).

Collapses locale-variant campaign files that share an identical creative tree onto a single
non-materializing `kind: CampaignTemplate`, rewriting each member to a `kind: Campaign` overlay
(metadata.extends + a flat spec delta). Files whose shared tree is unique in their (platform,
vertical) group stay standalone (untouched).

Shared-tree hash = canonical hash of ALL doc content EXCEPT the per-locale varying fields:
  - campaign metadata.name, spec.budget_daily_usd, spec.geo_preset_ref
  - each ad-set/ad-template spec.target_cpa_usd / spec.target_roas
  - the mechanical metadata.campaign_ref back-refs (they live in metadata, never in the tree)
  - ad-template spec.ad_set_ref (mechanical — the ExtendsExpander re-synthesizes it from nesting)

Projection mirrors the backend ExtendsExpander exactly (see git-sync/.../v2/ExtendsExpander.java).
Byte-shape fidelity: reuses convert.py's safe_dump kwargs + "---\\n" join.

Usage: generate_templates.py <repo_root>   (rewrites the corpus in place)
"""
import sys, os, glob, json, copy, collections
import yaml

PSHORT = {
    "google_search": "search", "google_demand_gen": "dg",
    "meta": "meta", "snapchat": "snap", "tiktok": "tiktok", "openai": "openai",
}

def dump_doc(doc):
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=4096)

def write_multi(path, docs):
    with open(path, "w") as fh:
        fh.write("---\n".join(dump_doc(d) for d in docs))

def parse_file(path):
    """Parse a campaign file into a structured view: campaign spec + ordered ad_sets[ad_templates].
    Eligible for collapsing only when it holds exactly one Campaign doc and a single uniform bid."""
    docs = [d for d in yaml.safe_load_all(open(path)) if d]
    camps = [d for d in docs if d.get("kind") == "Campaign"]
    ad_sets, cur = [], None
    for d in docs:
        if d.get("kind") == "AdSet":
            cur = {"name": d["metadata"]["name"], "spec": d["spec"], "ad_templates": []}
            ad_sets.append(cur)
        elif d.get("kind") == "AdTemplate":
            if cur is None:
                return {"eligible": False}
            cur["ad_templates"].append({"name": d["metadata"]["name"], "spec": d["spec"]})
    if len(camps) != 1:
        return {"eligible": False}
    c = camps[0]
    seen = set()
    for a in ad_sets:
        sp = a["spec"]
        if "target_cpa_usd" in sp:
            seen.add(("cpa", sp["target_cpa_usd"]))
        elif "target_roas" in sp:
            seen.add(("roas", sp["target_roas"]))
        else:
            seen.add(("none", None))
    if len(seen) > 1:
        return {"eligible": False}
    bid = next(iter(seen)) if seen else ("none", None)
    return {
        "eligible": True,
        "path": path,
        "campaign_name": c["metadata"]["name"],
        "platform": c["metadata"].get("platform"),
        "vertical": c["metadata"].get("vertical"),
        "campaign_spec": c["spec"],
        "ad_sets": ad_sets,
        "bid": bid,  # (field, value): field in {cpa, roas, none}
    }

def shared_tree_key(pf):
    """Canonical JSON of the shared tree — identical across locale siblings that collapse together."""
    c = {k: v for k, v in pf["campaign_spec"].items()
         if k not in ("budget_daily_usd", "geo_preset_ref")}
    c["__platform"] = pf["platform"]
    c["__vertical"] = pf["vertical"]
    ad_sets = []
    for a in pf["ad_sets"]:
        asp = {k: v for k, v in a["spec"].items() if k not in ("target_cpa_usd", "target_roas")}
        ats = [{"name": t["name"],
                "spec": {k: v for k, v in t["spec"].items()
                         if k not in ("target_cpa_usd", "target_roas", "ad_set_ref")}}
               for t in a["ad_templates"]]
        ad_sets.append({"name": a["name"], "spec": asp, "ad_templates": ats})
    return json.dumps({"c": c, "a": ad_sets}, sort_keys=True, ensure_ascii=False)

def build_template_doc(rep, tmpl_name, common_bid):
    """CampaignTemplate doc from the cluster representative. common_bid carries the group's shared
    bid value when it does NOT vary (kept on every template ad-set); None when it varies (overlay)."""
    camp_shared = copy.deepcopy({k: v for k, v in rep["campaign_spec"].items()
                                 if k not in ("budget_daily_usd", "geo_preset_ref")})
    tmpl_ad_sets = []
    for a in rep["ad_sets"]:
        asp = copy.deepcopy(a["spec"])
        if common_bid is None:  # bid varies -> lives in the overlay, stripped from the template
            asp.pop("target_cpa_usd", None)
            asp.pop("target_roas", None)
        ats = []
        for t in a["ad_templates"]:
            tsp = copy.deepcopy(t["spec"])
            tsp.pop("ad_set_ref", None)  # ExtendsExpander re-synthesizes from the parent ad-set name
            ats.append({"name": t["name"], **tsp})
        tmpl_ad_sets.append({"name": a["name"], **asp, "ad_templates": ats})
    spec = {**camp_shared, "ad_sets": tmpl_ad_sets}
    return {
        "apiVersion": "amnesia/v1", "kind": "CampaignTemplate",
        "metadata": {"name": tmpl_name, "platform": rep["platform"], "vertical": rep["vertical"]},
        "spec": spec,
    }

def build_overlay_doc(pf, tmpl_name, bid_varies):
    """Concrete Campaign overlay: metadata.extends + flat spec delta (budget/geo + per-file bid)."""
    spec = {}
    cs = pf["campaign_spec"]
    if "budget_daily_usd" in cs:
        spec["budget_daily_usd"] = copy.deepcopy(cs["budget_daily_usd"])
    if "geo_preset_ref" in cs:
        spec["geo_preset_ref"] = copy.deepcopy(cs["geo_preset_ref"])
    if bid_varies:
        field, value = pf["bid"]
        if field == "cpa":
            spec["target_cpa_usd"] = value
        elif field == "roas":
            spec["target_roas"] = value
    return {
        "apiVersion": "amnesia/v1", "kind": "Campaign",
        "metadata": {"name": pf["campaign_name"], "platform": pf["platform"],
                     "vertical": pf["vertical"], "extends": tmpl_name},
        "spec": spec,
    }

def main():
    root = os.path.abspath(sys.argv[1])
    files = sorted(glob.glob(os.path.join(root, "google", "**", "*.yaml"), recursive=True))
    files = [f for f in files if not os.path.basename(f).startswith("_template")]

    # dir -> shared_tree_key -> [parsed_file]; ineligible files get a unique singleton key.
    by_dir = collections.defaultdict(lambda: collections.defaultdict(list))
    for f in files:
        d = os.path.dirname(f)
        pf = parse_file(f)
        if pf["eligible"]:
            by_dir[d][shared_tree_key(pf)].append(pf)
        else:
            by_dir[d][f"__solo__{f}"].append({"eligible": False, "path": f})

    stats = collections.OrderedDict()
    templates_created = 0
    files_collapsed = 0
    standalone = 0

    for d in sorted(by_dir):
        rel_dir = os.path.relpath(d, root)
        # Deterministic collapse-cluster ordering: by the sorted member basenames.
        collapse = []
        for key, members in by_dir[d].items():
            eligible_members = [m for m in members if m.get("eligible")]
            if len(eligible_members) >= 2:
                collapse.append(sorted(eligible_members, key=lambda m: os.path.basename(m["path"])))
        collapse.sort(key=lambda members: [os.path.basename(m["path"]) for m in members])

        dir_standalone = sum(len(v) for v in by_dir[d].values()) - sum(len(c) for c in collapse)
        standalone += dir_standalone

        multi = len(collapse) > 1
        for idx, members in enumerate(collapse, start=1):
            rep = members[0]
            pshort = PSHORT.get(rep["platform"], rep["platform"])
            vert = rep["vertical"]
            tmpl_name = f"{vert}_{pshort}_tmpl" + (f"_{idx}" if multi else "")
            tmpl_file = os.path.join(d, "_template.yaml" if not multi else f"_template_{idx}.yaml")

            field = rep["bid"][0]
            values = [m["bid"][1] for m in members]
            common = all(v == values[0] for v in values)
            common_bid = (field, values[0]) if common else None  # sentinel: None => varies

            write_multi(tmpl_file, [build_template_doc(rep, tmpl_name, common_bid)])
            templates_created += 1
            for m in members:
                overlay = build_overlay_doc(m, tmpl_name, bid_varies=not common)
                write_multi(m["path"], [overlay])
                files_collapsed += 1

        stats[rel_dir] = {"templates": len(collapse), "collapsed": sum(len(c) for c in collapse),
                          "standalone": dir_standalone}

    print("=== per-(platform,vertical) migration ===")
    for rel_dir, s in stats.items():
        print(f"  {rel_dir}: templates={s['templates']} collapsed={s['collapsed']} standalone={s['standalone']}")
    print(f"\nTOTAL: templates_created={templates_created} files_collapsed={files_collapsed} "
          f"standalone={standalone} campaign_files={len(files)}")

if __name__ == "__main__":
    main()
