#!/usr/bin/env python3
"""Deterministic, idempotent corpus migrator (AMS-444 WS-C).

Anchors EVERY campaign to the global `efaq_base` CampaignTemplate through the chain
`Campaign -> vertical CampaignTemplate -> _base.yaml`, using the WS-A/WS-B keyed-name
override capability. Two logical passes (select with --stage):

  templates : rewire each existing `_template*.yaml` to `extends: efaq_base` and strip the
              scalar fields the base now supplies (campaign objective/status; ad-set
              status/bid_strategy; ad-template status) ONLY when they equal the base value.
  full      : the above, plus group every campaign by STRUCTURAL signature (ad-set/ad-template
              skeleton excluding copy/bid/status), reuse an existing matching template (all
              existing templates are kept — copy-variant siblings are retained so their members
              stay byte-equivalent) or synthesize one from a deterministic representative, and
              rewrite every campaign as `extends: <template>` + a keyed-name delta. Templates are
              never deleted; any that end up unreferenced are WARNed.

Correctness is guaranteed field-by-field by tools/verify_byte_equivalence.py against the pinned
pre-migration ref. The migrator only re-shapes existing content — it never invents copy/geo/budget.

Everything is derived from each campaign's MATERIALIZED tree (expanded through the current chain),
so re-running on already-migrated output reproduces byte-identical files.

Usage: AMNESIA_DIR=../amnesia migrate_to_base_templates.py <repo_root> [--stage templates|full]
"""
import sys, os, glob, json, copy, collections, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_templates import dump_doc, write_multi, PSHORT  # byte-shape fidelity + naming

_AMNESIA = os.environ.get(
    "AMNESIA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "amnesia"),
)
_VALIDATOR_DIR = os.path.join(_AMNESIA, ".github", "actions", "validate-config")
if not os.path.isfile(os.path.join(_VALIDATOR_DIR, "validate.py")):
    sys.exit(f"error: amnesia validator not found under {_VALIDATOR_DIR}; set AMNESIA_DIR")
sys.path.insert(0, _VALIDATOR_DIR)
import validate as V

BASE_NAME = "efaq_base"
BASE_CAMPAIGN_DEFAULTS = {"objective": "TRAFFIC", "status": "PAUSED"}
BASE_ADSET_DEFAULTS = {"status": "PAUSED", "bid_strategy": "TARGET_CPA"}
BASE_ADTPL_DEFAULTS = {"status": "ENABLED"}

# Body keys that are structural framing, not authored spec content.
_CAMP_BODY_DROP = {"name", "schema_version", "platform", "vertical",
                   "geo_preset_ref", "geo_exclude_refs", "ad_extension_refs"}
_CAMP_META_REFS = ("geo_preset_ref", "geo_exclude_refs", "ad_extension_refs")
_ADSET_BODY_DROP = {"name", "schema_version", "platform", "vertical", "campaign_ref"}
_ADTPL_BODY_DROP = {"name", "schema_version", "platform", "vertical", "campaign_ref",
                    "creative_feed_ref", "ad_set_ref"}
# Fields excluded from the structural signature (vary per campaign / supplied by base).
_ADSET_VARY = {"status", "bid_strategy", "target_cpa_usd", "target_roas"}
_ADTPL_VARY = {"status", "destination_url", "business_name", "headlines",
               "descriptions", "call_to_action"}
_BID_FIELDS = ("target_cpa_usd", "target_roas")


class Unit:
    """One campaign's materialized truth: campaign scalars + ordered ad-set/ad-template tree."""
    __slots__ = ("name", "platform", "vertical", "refs", "camp_spec", "ad_sets")

    def __init__(self, name, platform, vertical, refs, camp_spec, ad_sets):
        self.name, self.platform, self.vertical = name, platform, vertical
        self.refs, self.camp_spec, self.ad_sets = refs, camp_spec, ad_sets


def _materialize(docs, index):
    out = []
    for d in docs:
        if d["kind"] == "Campaign" and d["metadata"].get("extends"):
            synth, errs = V.expand_extends(d, index)
            if errs:
                raise RuntimeError("; ".join(errs))
            for s in synth:
                out.append((s["kind"], s["metadata"]["name"], V.normalize_body(s)))
        else:
            out.append((d["kind"], d["metadata"]["name"], V.normalize_body(d)))
    return out


def _units_from_bodies(bodies):
    """Segment ordered (kind,name,body) triples into one Unit per Campaign doc, in file order."""
    units, cur, cur_as = [], None, None
    for kind, name, body in bodies:
        if kind == "Campaign":
            refs = {k: copy.deepcopy(body[k]) for k in _CAMP_META_REFS if k in body}
            camp_spec = {k: copy.deepcopy(v) for k, v in body.items() if k not in _CAMP_BODY_DROP}
            cur = Unit(name, body.get("platform"), body.get("vertical"), refs, camp_spec, [])
            units.append(cur)
            cur_as = None
        elif kind == "AdSet":
            spec = {k: copy.deepcopy(v) for k, v in body.items() if k not in _ADSET_BODY_DROP}
            cur_as = {"name": name, "spec": spec, "ad_templates": []}
            cur.ad_sets.append(cur_as)
        elif kind == "AdTemplate":
            spec = {k: copy.deepcopy(v) for k, v in body.items() if k not in _ADTPL_BODY_DROP}
            cur_as["ad_templates"].append(
                {"name": name, "creative_feed_ref": body.get("creative_feed_ref"), "spec": spec})
    return units


def signature(unit):
    """Canonical hash of the ad-set/ad-template skeleton, excluding per-campaign copy/bid/status."""
    ad_sets = []
    for a in unit.ad_sets:
        struct = {k: v for k, v in a["spec"].items() if k not in _ADSET_VARY}
        ats = [{"name": t["name"], "feed": t["creative_feed_ref"],
                "struct": {k: v for k, v in t["spec"].items() if k not in _ADTPL_VARY}}
               for t in a["ad_templates"]]
        ad_sets.append({"name": a["name"], "struct": struct, "ats": ats})
    return json.dumps({"ad_sets": ad_sets}, sort_keys=True, ensure_ascii=False)


def _uniform_bid(unit):
    """The campaign's single bid (all ad-sets share one), or None."""
    bids = set()
    for a in unit.ad_sets:
        sp = a["spec"]
        if "target_cpa_usd" in sp:
            bids.add(("cpa", sp["target_cpa_usd"]))
        elif "target_roas" in sp:
            bids.add(("roas", sp["target_roas"]))
        else:
            bids.add(("none", None))
    if len(bids) != 1:
        shown = sorted(bids, key=lambda b: (b[0], "" if b[1] is None else b[1]))
        raise RuntimeError(f"{unit.name}: non-uniform bid {shown} — campaign-level override unsafe")
    field, value = next(iter(bids))
    return None if field == "none" else (field, value)


# ---- template rewiring / synthesis -----------------------------------------------------------

def rewire_template_doc(doc):
    """Add extends: efaq_base and strip base-default scalars (only when equal to the base value)."""
    doc = copy.deepcopy(doc)
    doc["metadata"]["extends"] = BASE_NAME
    spec = doc.get("spec") or {}
    for k, v in BASE_CAMPAIGN_DEFAULTS.items():
        if spec.get(k) == v:
            spec.pop(k, None)
    for a in spec.get("ad_sets") or []:
        for k, v in BASE_ADSET_DEFAULTS.items():
            if a.get(k) == v:
                a.pop(k, None)
        for t in a.get("ad_templates") or []:
            for k, v in BASE_ADTPL_DEFAULTS.items():
                if t.get(k) == v:
                    t.pop(k, None)
    doc["spec"] = spec
    return doc


def synthesize_template_doc(rep, tmpl_name):
    """CampaignTemplate from a representative Unit: full tree minus base-inherited defaults + bid."""
    ad_sets = []
    for a in rep.ad_sets:
        entry = {"name": a["name"]}
        for k, v in a["spec"].items():
            if k in _BID_FIELDS:
                continue  # per-campaign — supplied by the overlay's campaign-level bid
            if BASE_ADSET_DEFAULTS.get(k) == v:
                continue
            entry[k] = copy.deepcopy(v)
        ats = []
        for t in a["ad_templates"]:
            te = {"name": t["name"]}
            if t["creative_feed_ref"] is not None:
                te["creative_feed_ref"] = copy.deepcopy(t["creative_feed_ref"])
            for k, v in t["spec"].items():
                if BASE_ADTPL_DEFAULTS.get(k) == v:
                    continue
                te[k] = copy.deepcopy(v)
            ats.append(te)
        entry["ad_templates"] = ats
        ad_sets.append(entry)
    return {
        "apiVersion": "amnesia/v1", "kind": "CampaignTemplate",
        "metadata": {"name": tmpl_name, "platform": rep.platform,
                     "vertical": rep.vertical, "extends": BASE_NAME},
        "spec": {"ad_sets": ad_sets},
    }


# ---- delta computation ------------------------------------------------------------------------

def compute_delta_spec(unit, tmpl_unit):
    """The concrete Campaign spec that, overlaid on tmpl_unit's chain, reproduces unit's tree."""
    spec = collections.OrderedDict()
    spec["budget_daily_usd"] = copy.deepcopy(unit.camp_spec["budget_daily_usd"])
    for k in ("objective", "status", "labels"):
        if k in unit.camp_spec and unit.camp_spec[k] != tmpl_unit.camp_spec.get(k):
            spec[k] = copy.deepcopy(unit.camp_spec[k])

    bid, tbid = _uniform_bid(unit), _uniform_bid(tmpl_unit)
    if bid is not None and bid != tbid:
        spec["target_cpa_usd" if bid[0] == "cpa" else "target_roas"] = bid[1]

    tmpl_as = {a["name"]: a for a in tmpl_unit.ad_sets}
    as_entries = []
    for a in unit.ad_sets:
        ta = tmpl_as[a["name"]]
        as_missing = [k for k in ta["spec"] if k not in a["spec"] and k not in _BID_FIELDS
                      and k != "bid_strategy"]
        if as_missing:
            raise RuntimeError(f"{unit.name}/{a['name']}: template ad-set has non-removable "
                               f"inherited field(s) {as_missing} — signature too coarse")
        entry = collections.OrderedDict([("name", a["name"])])
        for k, v in a["spec"].items():
            if k in _BID_FIELDS or k == "bid_strategy":
                continue  # driven by the campaign-level bid override
            if v != ta["spec"].get(k):
                entry[k] = copy.deepcopy(v)
        tmpl_at = {t["name"]: t for t in ta["ad_templates"]}
        at_entries = []
        for t in a["ad_templates"]:
            tt = tmpl_at[t["name"]]
            te = collections.OrderedDict([("name", t["name"])])
            if t["creative_feed_ref"] != tt["creative_feed_ref"]:
                te["creative_feed_ref"] = copy.deepcopy(t["creative_feed_ref"])
            for k, v in t["spec"].items():
                if v != tt["spec"].get(k):
                    te[k] = copy.deepcopy(v)  # leaf lists replaced wholesale
            missing = [k for k in tt["spec"] if k not in t["spec"]]
            if missing:
                raise RuntimeError(f"{unit.name}/{t['name']}: template has non-removable "
                                   f"inherited field(s) {missing} — signature too coarse")
            if len(te) > 1:
                at_entries.append(dict(te))
        if at_entries:
            entry["ad_templates"] = at_entries
        if len(entry) > 1:
            as_entries.append(dict(entry))
    if as_entries:
        spec["ad_sets"] = as_entries
    return dict(spec)


def build_overlay_doc(unit, tmpl_name, delta_spec):
    md = collections.OrderedDict([
        ("name", unit.name), ("platform", unit.platform),
        ("vertical", unit.vertical), ("extends", tmpl_name)])
    for k in _CAMP_META_REFS:
        if k in unit.refs:
            md[k] = copy.deepcopy(unit.refs[k])
    return {"apiVersion": "amnesia/v1", "kind": "Campaign",
            "metadata": dict(md), "spec": delta_spec}


# ---- orchestration ----------------------------------------------------------------------------

def _campaign_files(root):
    files = sorted(glob.glob(os.path.join(root, "google", "**", "*.yaml"), recursive=True))
    return [f for f in files if not os.path.basename(f).startswith("_template")]


def _template_files(root):
    return sorted(glob.glob(os.path.join(root, "google", "**", "_template*.yaml"), recursive=True))


def _load_docs(path):
    import yaml
    return [d for d in yaml.safe_load_all(open(path)) if d]


def _index_from_docs(template_docs):
    return V.build_template_index([("", i, d) for i, d in enumerate(template_docs)])


def _expand_template_unit(tmpl_name, platform, vertical, index):
    """The pure chain result for `tmpl_name` (no concrete overlay): a probe with empty spec."""
    probe = {"apiVersion": "amnesia/v1", "kind": "Campaign",
             "metadata": {"name": "__probe__", "platform": platform,
                          "vertical": vertical, "extends": tmpl_name}, "spec": {}}
    return _units_from_bodies(_materialize([probe], index))[0]


def run(root, stage="full"):
    import yaml
    root = os.path.abspath(root)

    # Base index from the current working tree (pre-migration or already-migrated — both fine).
    all_docs = V.load_all_docs(__import__("pathlib").Path(root))
    cur_index = V.build_template_index(all_docs)

    # 1. Rewire every existing template in memory.
    tmpl_paths = _template_files(root)
    rewired = {}   # path -> rewired doc
    tmpl_by_pv = collections.defaultdict(list)  # (platform,vertical) -> [(name, path)]
    base_doc = None
    for p in tmpl_paths:
        docs = _load_docs(p)
        doc = docs[0]
        md = doc["metadata"]
        if md.get("name") == BASE_NAME:
            base_doc = doc
            continue
        rd = rewire_template_doc(doc)
        rewired[p] = rd
        tmpl_by_pv[(md.get("platform"), md.get("vertical"))].append((md["name"], p))
    if base_doc is None:
        base_path = os.path.join(root, "_base.yaml")
        base_doc = _load_docs(base_path)[0] if os.path.isfile(base_path) else None
    if base_doc is None:
        sys.exit("error: efaq_base (_base.yaml) not found — run Task 1 first")

    if stage == "templates":
        for p, rd in rewired.items():
            write_multi(p, [rd])
        print(f"stage=templates: rewired {len(rewired)} templates to extends {BASE_NAME}")
        return

    # 2. Materialize every campaign (truth) using the CURRENT chain; capture each campaign's
    #    currently-authored extends target (so pre-existing overlays keep their template).
    camp_paths = _campaign_files(root)
    file_units = collections.OrderedDict()  # path -> [Unit]
    current_extends = {}                     # (path, unit_index) -> extends name or None
    for p in camp_paths:
        raw = _load_docs(p)
        camp_docs = [d for d in raw if d["kind"] == "Campaign"]
        for ui, cd in enumerate(camp_docs):
            current_extends[(p, ui)] = cd["metadata"].get("extends")
        file_units[p] = _units_from_bodies(_materialize(raw, cur_index))

    # Signature of each existing template (structural; unaffected by rewiring).
    tmpl_sig = {}  # name -> signature
    for (pf, vt), lst in tmpl_by_pv.items():
        idx = _index_from_docs([base_doc] + [rewired[p] for _n, p in lst])
        for name, _p in lst:
            tmpl_sig[name] = signature(_expand_template_unit(name, pf, vt, idx))

    def existing_matches(pf, vt, sig):
        return sorted(n for n, _p in tmpl_by_pv.get((pf, vt), []) if tmpl_sig.get(n) == sig)

    # 3. Assign a template to every campaign. Reuse the currently-authored extends when its
    #    signature still matches (keeps pre-existing overlays stable + every template referenced);
    #    else the smallest-named existing match; else group for synthesis.
    assign = {}                                  # (path, unit_index) -> template name
    need_synth = collections.defaultdict(list)   # (pf,vt,sig) -> [(path, unit_index, Unit)]
    for p in camp_paths:
        for ui, u in enumerate(file_units[p]):
            sig = signature(u)
            matches = existing_matches(u.platform, u.vertical, sig)
            cur = current_extends.get((p, ui))
            if cur is not None and cur in matches:
                assign[(p, ui)] = cur
            elif matches:
                assign[(p, ui)] = matches[0]
            else:
                need_synth[(u.platform, u.vertical, sig)].append((p, ui, u))

    # 4. Synthesize one template per unmatched (platform, vertical, signature) from the
    #    alphabetically-first campaign. Deterministic file/name numbering per directory.
    synth_docs = {}  # template name -> (doc, file_path)
    for key in sorted(need_synth, key=lambda k: (k[0] or "", k[1] or "", k[2])):
        pf, vt, _sig = key
        members = sorted(need_synth[key], key=lambda m: (m[0], m[1]))
        rep_path, _rep_ui, rep = members[0]
        rep_dir = os.path.dirname(rep_path)
        pshort = PSHORT.get(pf, pf)
        used_names = {n for n, _p in tmpl_by_pv.get((pf, vt), [])} | set(synth_docs)
        used_files = ({p for _n, p in tmpl_by_pv.get((pf, vt), [])}
                      | {fp for _d, fp in synth_docs.values()})
        idx = 0
        while True:
            suffix = "" if idx == 0 else f"_{idx}"
            name = f"{vt}_{pshort}_tmpl{suffix}"
            fpath = os.path.join(rep_dir, f"_template{suffix}.yaml")
            if name not in used_names and fpath not in used_files:
                break
            idx += 1
        synth_docs[name] = (synthesize_template_doc(rep, name), fpath)
        for p, ui, _u in members:
            assign[(p, ui)] = name

    # 5. Final index: base + ALL rewired existing templates + synthesized (nothing deleted).
    final_template_docs = [base_doc] + [rewired[p] for p in rewired] \
        + [doc for _n, (doc, _fp) in synth_docs.items()]
    final_index = _index_from_docs(final_template_docs)

    tmpl_unit_cache = {}

    def tmpl_unit_for(name, pf, vt):
        if name not in tmpl_unit_cache:
            tmpl_unit_cache[name] = _expand_template_unit(name, pf, vt, final_index)
        return tmpl_unit_cache[name]

    # 6. Build overlays per file (preserving multi-campaign files as multiple docs, in order).
    overlays = {}  # path -> [overlay doc]
    for p in camp_paths:
        docs_out = []
        for ui, u in enumerate(file_units[p]):
            tname = assign[(p, ui)]
            delta = compute_delta_spec(u, tmpl_unit_for(tname, u.platform, u.vertical))
            docs_out.append(build_overlay_doc(u, tname, delta))
        overlays[p] = docs_out

    # 7. Write rewired templates, synthesized templates, then overlays.
    for p, rd in rewired.items():
        write_multi(p, [rd])
    for _name, (doc, fpath) in synth_docs.items():
        write_multi(fpath, [doc])
    for p, docs_out in overlays.items():
        write_multi(p, docs_out)

    referenced = collections.Counter(assign.values())
    print("=== migration summary ===")
    print(f"campaigns:             {sum(len(v) for v in file_units.values())} "
          f"in {len(camp_paths)} files")
    print(f"templates existing:    {len(rewired)}")
    print(f"templates synthesized: {len(synth_docs)}")
    for name in sorted(synth_docs):
        print(f"    + {os.path.relpath(synth_docs[name][1], root)}  ({name})")
    print(f"templates after:       {len(rewired) + len(synth_docs)} (+ {BASE_NAME})")
    unref = sorted(set(tmpl_sig) - set(referenced))
    if unref:
        print(f"WARNING unreferenced existing templates: {unref}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root")
    ap.add_argument("--stage", choices=["templates", "full"], default="full")
    args = ap.parse_args()
    run(args.root, args.stage)


if __name__ == "__main__":
    main()
