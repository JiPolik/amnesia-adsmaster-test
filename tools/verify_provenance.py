import json, sys, re, pathlib
scratch = pathlib.Path(sys.argv[1]); ads = pathlib.Path(sys.argv[2])
prov = json.loads((scratch / "out" / "_provenance.json").read_text())
tok = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]')
def nav(obj, path):
    cur = obj
    for m in tok.finditer(path):
        key, idx = m.group(1), m.group(2)
        cur = cur[int(idx)] if idx is not None else cur[key]
    return cur
verbatim = [e for e in prov if e.get("mode") == "verbatim"]
checked = 0; mism = []
cache = {}
for e in verbatim:
    sf = e["source_file"]
    if sf not in cache:
        f = ads / sf
        cache[sf] = json.loads(f.read_text()) if f.exists() else None
    doc = cache[sf]
    if doc is None: mism.append(("MISSING_SRC", sf)); continue
    try: got = nav(doc, e["json_path"])
    except Exception as ex: mism.append(("NAV_FAIL", sf, e["json_path"], str(ex)[:50])); continue
    checked += 1
    if got != e["value"]: mism.append(("VALUE", sf, e["json_path"], repr(e["value"])[:50], repr(got)[:50]))
print(f"verbatim entries: {len(verbatim)}  checked: {checked}  mismatches: {len(mism)}")
for m in mism[:15]: print("  ", m)
sys.exit(1 if mism else 0)
