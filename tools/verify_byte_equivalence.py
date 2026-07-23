#!/usr/bin/env python3
"""Byte-equivalence gate for the AMS-426 CampaignTemplate migration (Task 8).

For every ORIGINAL campaign file (read from a fixed PRE-migration base ref), expand the NEW
template+overlay in the working tree and diff the materialized bodies field-by-field against the
original's materialized bodies. ANY divergence -> non-zero exit + a per-campaign report.

The base ref must predate the migration commit — NOT HEAD, which is the migration itself (that would
compare migrated-vs-migrated and pass vacuously). Defaults to origin/main (the branch's pre-migration
point); override with --base for CI (e.g. --base "$(git merge-base origin/main HEAD)").

Normalization + extends expansion are imported from validate.py so the gate uses the exact same
parse/normalize/expand chain the validator (and, upstream, the backend ExtendsExpander) uses.

Usage: verify_byte_equivalence.py <repo_root> [--base <ref>]   (run AFTER generate_templates.py)
"""
import sys, os, argparse, subprocess, pathlib
import yaml

# validate.py now lives in amnesia (AMS-430). Resolve it from a sibling checkout.
_AMNESIA = os.environ.get(
    "AMNESIA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "amnesia"),
)
_VALIDATOR_DIR = os.path.join(_AMNESIA, ".github", "actions", "validate-config")
if not os.path.isfile(os.path.join(_VALIDATOR_DIR, "validate.py")):
    sys.exit(
        f"error: amnesia validator not found under {_VALIDATOR_DIR}\n"
        "       clone haremedia/amnesia as a sibling of this repo, or set AMNESIA_DIR=/path/to/amnesia"
    )
sys.path.insert(0, _VALIDATOR_DIR)
import validate as V


def git_show(root, ref, path):
    return subprocess.check_output(["git", "-C", root, "show", f"{ref}:{path}"], text=True)


def base_campaign_files(root, ref):
    """Enumerate the ORIGINAL campaign file set from the pre-migration base ref, so new _template.yaml
    files aren't mistaken for originals and any deleted file is still checked."""
    out = subprocess.check_output(["git", "-C", root, "ls-tree", "-r", "--name-only", ref], text=True)
    files = []
    for line in out.splitlines():
        if line.startswith("google/") and line.endswith(".yaml") and not os.path.basename(line).startswith("_template"):
            files.append(line)
    return sorted(files)


def docs_from_text(text):
    return [d for d in yaml.safe_load_all(text) if d]


def materialize(docs, index):
    """Expand extends-campaigns; normalize every doc to its flat body. Returns list of (kind, name, body)."""
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


def diff_bodies(orig, new):
    """orig/new are lists of (kind, name, body). Returns a list of human-readable diff strings."""
    diffs = []
    orig_seq = [(k, n) for k, n, _ in orig]
    new_seq = [(k, n) for k, n, _ in new]
    if orig_seq != new_seq:
        diffs.append(f"doc sequence differs:\n      original: {orig_seq}\n      expanded: {new_seq}")
        return diffs
    for (k, n, ob), (_, _, nb) in zip(orig, new):
        if ob == nb:
            continue
        keys = sorted(set(ob) | set(nb))
        for key in keys:
            ov, nv = ob.get(key, "<absent>"), nb.get(key, "<absent>")
            if ov != nv:
                diffs.append(f"{k}/{n} field '{key}': original={ov!r} expanded={nv!r}")
    return diffs


def main():
    ap = argparse.ArgumentParser(description="Byte-equivalence gate for the CampaignTemplate migration.")
    ap.add_argument("root", help="repo root")
    ap.add_argument("--base", default="origin/main",
                    help="pre-migration ref to read original bodies from (default: origin/main)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    base = args.base
    all_docs = V.load_all_docs(pathlib.Path(root))
    index = V.build_template_index(all_docs)
    templates = sum(1 for _r, _i, d in all_docs if d and d.get("kind") == "CampaignTemplate")

    files = base_campaign_files(root, base)
    print(f"comparing working tree against pre-migration base ref: {base}\n")
    total_diffs = 0
    orig_campaign_docs = 0
    new_campaign_docs = 0
    for rel in files:
        orig_docs = docs_from_text(git_show(root, base, rel))
        wt_path = pathlib.Path(root, rel)
        if not wt_path.exists():
            print(f"❌ {rel}: present in base '{base}' but missing from the working tree")
            total_diffs += 1
            continue
        new_docs = docs_from_text(wt_path.read_text())
        orig_campaign_docs += sum(1 for d in orig_docs if d["kind"] == "Campaign")
        new_campaign_docs += sum(1 for d in new_docs if d["kind"] == "Campaign")
        try:
            orig_bodies = materialize(orig_docs, index)
            new_bodies = materialize(new_docs, index)
        except RuntimeError as e:
            print(f"❌ {rel}: expansion failed: {e}")
            total_diffs += 1
            continue
        file_diffs = diff_bodies(orig_bodies, new_bodies)
        if file_diffs:
            total_diffs += len(file_diffs)
            print(f"❌ {rel}:")
            for d in file_diffs:
                print(f"      {d}")

    print()
    print(f"campaign files verified: {len(files)}")
    print(f"templates created:       {templates}")
    print(f"Campaign docs before:    {orig_campaign_docs}")
    print(f"Campaign docs after:     {new_campaign_docs}")
    if orig_campaign_docs != new_campaign_docs:
        print(f"❌ Campaign-count mismatch ({orig_campaign_docs} -> {new_campaign_docs})")
        total_diffs += 1
    if total_diffs:
        print(f"\n❌ {total_diffs} divergence(s) — byte-equivalence FAILED.")
        sys.exit(1)
    print(f"\n✅ {len(files)} campaigns verified, {templates} templates created, 0 diffs.")
    sys.exit(0)


if __name__ == "__main__":
    main()
