#!/usr/bin/env python3
"""AMS-414: append 5 GeoPreset docs + point 144 campaigns at them.
Deterministic text edits — inserts exactly one `geo_preset_ref` line per mapped
campaign doc (after budget_daily_usd), touching nothing else."""
import json, sys, re, pathlib
import yaml

C = pathlib.Path(sys.argv[1])          # amnesia-campaigns
ADS = pathlib.Path(sys.argv[2])        # ads/efaq
PROV = pathlib.Path(sys.argv[3])       # _provenance.json
GEO_JSON = pathlib.Path(sys.argv[4])   # geotargets_countries.json

BLOCKED = ["UA", "RU", "BY", "VE", "IR", "KP", "CU"]
# Verbatim from amnesia platform-google Geocode.java (enum order; GB dedup in main_markets)
EUROPE = ["AD","AL","AT","BA","BE","BG","CH","CY","CZ","DE","DK","EE","ES","FI","FR","GB","GR","HR","HU","IE","IS","IT","LI","LT","LU","LV","MC","MD","ME","MK","MT","NL","NO","PL","PT","RO","RS","SE","SI","SM","SK"]
ANGLOSPHERE = ["US","CA","GB","IE","AU","NZ"]
OCEANIA = ["AU","NZ"]
_MM_RAW = ["US","CA","GB","AU","NZ"] + EUROPE + ["JP","KR","SG","HK"]
MAIN_MARKETS = list(dict.fromkeys(_MM_RAW))  # dedupe, keep first occurrence order

GEOCODE_TO_PRESET = {
    "WW": "worldwide_ex_sanctioned",
    "EUROPE": "europe",
    "ANGLOSPHERE": "anglosphere",
    "OCEANIA": "oceania",
    "PRESET_DXG_MAIN_MARKETS": "main_markets",
}

def preset_doc(name, include, exclude=None):
    lines = ["apiVersion: amnesia/v1", "kind: GeoPreset", "metadata:", f"  name: {name}", "spec:", "  include:"]
    for c in include:
        v = f"'{c}'" if c in ("NO", "ON", "OFF", "YES") else c
        lines += [f"  - type: COUNTRY", f"    value: {v}"]
    if exclude:
        lines.append("  exclude:")
        for c in exclude:
            v = f"'{c}'" if c in ("NO", "ON", "OFF", "YES") else c
            lines += [f"  - type: COUNTRY", f"    value: {v}"]
    return "\n".join(lines) + "\n"

def main():
    ww = json.loads(GEO_JSON.read_text())["worldwide_ex_sanctioned"]

    # 1. append presets to geo/geo-presets.yaml
    geo_file = C / "geo" / "geo-presets.yaml"
    text = geo_file.read_text()
    if "worldwide_ex_sanctioned" in text:
        print("presets already present — skipping append")
    else:
        docs = [
            preset_doc("worldwide_ex_sanctioned", ww, BLOCKED),
            preset_doc("europe", EUROPE),
            preset_doc("anglosphere", ANGLOSPHERE),
            preset_doc("oceania", OCEANIA),
            preset_doc("main_markets", MAIN_MARKETS),
        ]
        if not text.endswith("\n"):
            text += "\n"
        text += "---\n" + "---\n".join(docs)
        geo_file.write_text(text)
        print(f"appended 5 presets (ww include={len(ww)}, europe={len(EUROPE)}, main_markets={len(MAIN_MARKETS)})")

    # 2. build campaign -> preset mapping from provenance + source geocode
    prov = json.loads(PROV.read_text())
    src_by_name = {}
    for e in prov:
        if e["kind"] == "Campaign":
            src_by_name.setdefault(e["name"], e["source_file"])
    def source_geocode(name):
        doc = json.loads((ADS / src_by_name[name]).read_text())
        for key in ("search_campaigns", "demand_gen_campaigns", "demandgen_campaigns", "campaigns"):
            for c in (doc.get(key) or []):
                if c.get("name") == name:
                    return c.get("geocode")
        return None

    # 3. insert geo_preset_ref per mapped campaign doc
    inserted = skipped_cari = already = 0
    for f in sorted(C.rglob("*.yaml")):
        top = f.parts[len(C.parts)]
        if top in (".amnesia", ".github", "tools", "geo", "feeds"):
            continue
        raw = f.read_text()
        chunks = raw.split("\n---\n")
        changed = False
        for i, ch in enumerate(chunks):
            d = yaml.safe_load(ch)
            if not d or d.get("kind") != "Campaign" or d["spec"].get("geo_preset_ref"):
                continue
            name = d["metadata"]["name"]
            geo = source_geocode(name)
            preset = GEOCODE_TO_PRESET.get(geo)
            if preset is None:
                skipped_cari += 1
                continue
            lines = ch.split("\n")
            for j, ln in enumerate(lines):
                if re.match(r"^  budget_daily_usd:", ln):
                    lines.insert(j + 1, f"  geo_preset_ref: {preset}")
                    break
            else:
                raise SystemExit(f"no budget line in {f} doc {i} ({name})")
            chunks[i] = "\n".join(lines)
            changed = True
            inserted += 1
        if changed:
            f.write_text("\n---\n".join(chunks))
    print(f"inserted geo_preset_ref: {inserted} | left unmapped (caribbean): {skipped_cari}")

if __name__ == "__main__":
    main()
