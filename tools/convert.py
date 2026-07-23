#!/usr/bin/env python3
"""Deterministic legacy(v1)->amnesia-v2 converter (AMS-401 Task 2).

Reads the read-only ads-archive efaq/ tree and emits schema_version:2 kind-based
multi-doc YAML into an OUTPUT dir. Zero fabrication: every emitted headline /
description / destination_url / business_name / logo_asset_ref / budget / cpa is
copied verbatim from the source JSON and recorded in _provenance.json.

Usage: convert.py <ads_efaq_dir> <out_dir>
"""
import sys, os, json, glob, collections
import yaml

ISO_ALPHA2 = {
    "AD","AE","AF","AG","AI","AL","AM","AO","AQ","AR","AS","AT","AU","AW","AX","AZ",
    "BA","BB","BD","BE","BF","BG","BH","BI","BJ","BL","BM","BN","BO","BQ","BR","BS","BT","BV","BW","BY","BZ",
    "CA","CC","CD","CF","CG","CH","CI","CK","CL","CM","CN","CO","CR","CU","CV","CW","CX","CY","CZ",
    "DE","DJ","DK","DM","DO","DZ","EC","EE","EG","EH","ER","ES","ET",
    "FI","FJ","FK","FM","FO","FR","GA","GB","GD","GE","GF","GG","GH","GI","GL","GM","GN","GP","GQ","GR","GS","GT","GU","GW","GY",
    "HK","HM","HN","HR","HT","HU","ID","IE","IL","IM","IN","IO","IQ","IR","IS","IT",
    "JE","JM","JO","JP","KE","KG","KH","KI","KM","KN","KP","KR","KW","KY","KZ",
    "LA","LB","LC","LI","LK","LR","LS","LT","LU","LV","LY",
    "MA","MC","MD","ME","MF","MG","MH","MK","ML","MM","MN","MO","MP","MQ","MR","MS","MT","MU","MV","MW","MX","MY","MZ",
    "NA","NC","NE","NF","NG","NI","NL","NO","NP","NR","NU","NZ","OM",
    "PA","PE","PF","PG","PH","PK","PL","PM","PN","PR","PS","PT","PW","PY","QA","RE","RO","RS","RU","RW",
    "SA","SB","SC","SD","SE","SG","SH","SI","SJ","SK","SL","SM","SN","SO","SR","SS","ST","SV","SX","SY","SZ",
    "TC","TD","TF","TG","TH","TJ","TK","TL","TM","TN","TO","TR","TT","TV","TW","TZ",
    "UA","UG","UM","US","UY","UZ","VA","VC","VE","VG","VI","VN","VU","WF","WS","YE","YT","ZA","ZM","ZW",
}

# Legacy vertical-first Search dirs (top-level of efaq/).
SEARCH_VFIRST = ["career","chat","ex-back","iq","license-plate","love-style","personality","spirit-animal"]
# Islands enumerated dynamically below.

SKIP_FILES = {"shared.json", "template.json", "temporary.json"}


def norm_vertical(v):
    return v.replace("-", "_")


def campaign_files(d):
    return sorted(f for f in glob.glob(os.path.join(d, "*.json"))
                  if os.path.basename(f) not in SKIP_FILES)


def locale_prefix(campaign_name):
    toks = campaign_name.split("_")
    return f"{toks[0]}-{toks[1]}"


class Converter:
    def __init__(self, efaq, out):
        self.efaq = os.path.abspath(efaq)
        self.out = os.path.abspath(out)
        # grouped[(platform_dir, vertical, locale)] = list of doc dicts (ordered)
        self.grouped = collections.OrderedDict()
        self.feeds = collections.OrderedDict()   # feed_name -> set(languages)
        self.feed_lang_src = {}                   # (feed_name, lang) -> (src_file, jpath)
        self.geos = collections.OrderedDict()     # geo_name -> value
        self.geo_src = {}                          # geo_name -> (src_file, jpath)
        self.provenance = []
        self.dropped = collections.Counter()      # dropped field-class -> count
        self.dropped_geo_values = set()
        self.skipped = []                          # (source, reason)
        self.counts = collections.defaultdict(lambda: collections.Counter())

    def rel(self, path):
        return os.path.relpath(path, self.efaq)

    def prov(self, out_file, kind, name, field, value, src_file, jpath, mode):
        self.provenance.append({
            "out_file": out_file, "kind": kind, "name": name, "field": field,
            "value": value, "source_file": src_file, "json_path": jpath, "mode": mode,
        })

    # ---- bidding -------------------------------------------------------
    def map_bidding(self, bs):
        """Return (bid_strategy, field_name_or_None, value_or_None)."""
        cpa = bs.get("cpa_target")
        roas = bs.get("roasTarget")
        if cpa is not None:
            return "TARGET_CPA", "target_cpa_usd", cpa
        if roas is not None:
            return "MAXIMIZE_CONVERSION_VALUE", "target_roas", roas
        t = bs.get("type")
        enum_map = {
            "maximize_conversions": "MAXIMIZE_CONVERSIONS",
            "maximize_clicks": "MAXIMIZE_CLICKS",
            "target_cpa": "TARGET_CPA",
            "maximize_conversion_value": "MAXIMIZE_CONVERSION_VALUE",
        }
        return enum_map.get(t, "MAXIMIZE_CLICKS"), None, None

    def map_objective(self, tactic):
        return {"PROSPECTING": "TRAFFIC", "RETARGETING": "SALES",
                "REMARKETING": "SALES"}.get(tactic, "TRAFFIC")

    # ---- geo -----------------------------------------------------------
    def resolve_geo(self, geocode, src_file, jpath):
        """Return geo_preset_ref name if geocode is an ISO alpha-2 country, else None (dropped)."""
        if isinstance(geocode, str) and geocode.upper() in ISO_ALPHA2:
            name = geocode.lower()
            if name not in self.geos:
                self.geos[name] = geocode
                self.geo_src[name] = (src_file, jpath)
            return name
        self.dropped["geo_region_non_iso_country"] += 1
        if isinstance(geocode, str):
            self.dropped_geo_values.add(geocode)
        return None

    def feed_ref(self, vertical, platform_short):
        return f"{vertical}_{platform_short}_feed"

    def add_feed_lang(self, feed_name, lang, src_file, jpath):
        self.feeds.setdefault(feed_name, set())
        if lang and lang not in self.feeds[feed_name]:
            self.feeds[feed_name].add(lang)
            self.feed_lang_src[(feed_name, lang)] = (src_file, jpath)

    # ---- search --------------------------------------------------------
    def convert_search(self, vertical_dir, vertical, platform_dir="google/search"):
        for f in campaign_files(vertical_dir):
            data = json.load(open(f))
            rel = self.rel(f)
            for ci, c in enumerate(data.get("search_campaigns", [])):
                self._emit_search_campaign(c, ci, rel, vertical, platform_dir)

    def _emit_search_campaign(self, c, ci, rel, vertical, platform_dir):
        cname = c["name"]
        loc = locale_prefix(cname)
        key = (platform_dir, vertical, loc)
        out_file = f"{platform_dir}/{vertical}/{loc}.yaml"
        feed_name = self.feed_ref(vertical, "search")
        cbase = f"search_campaigns[{ci}]"

        # Campaign doc
        spec = {"objective": self.map_objective(c.get("marketing_tactic")), "status": "PAUSED"}
        self.prov(out_file, "Campaign", cname, "objective", spec["objective"], rel,
                  f"{cbase}.marketing_tactic", "derived")
        if "budget" in c and c["budget"].get("amount") is not None:
            spec["budget_daily_usd"] = c["budget"]["amount"]
            self.prov(out_file, "Campaign", cname, "budget_daily_usd", c["budget"]["amount"], rel,
                      f"{cbase}.budget.amount", "verbatim")
        geo = self.resolve_geo(c.get("geocode"), rel, f"{cbase}.geocode")
        if geo:
            spec["geo_preset_ref"] = geo
        self._add_doc(key, {"apiVersion": "amnesia/v1", "kind": "Campaign",
                            "metadata": {"name": cname}, "spec": spec})
        self.counts[(platform_dir, vertical)]["campaigns"] += 1

        bs = c.get("bidding_strategy", {})
        strat, bfield, bval = self.map_bidding(bs)

        # Note dropped campaign-level field classes
        for fld in ("keywords", "sitelinks", "callouts", "structured_snippets"):
            if fld in c:
                self.dropped[fld] += 1
        if "network_settings" in c:
            self.dropped["network_settings"] += 1
        if "campaign_group" in c:
            self.dropped["campaign_group"] += 1
        if "images" in c:
            self.dropped["campaign_images"] += 1

        for gi, ag in enumerate(c.get("ad_groups", [])):
            agname = ag["name"]
            gbase = f"{cbase}.ad_groups[{gi}]"
            aset = {"status": "PAUSED", "bid_strategy": strat}
            if bfield:
                aset[bfield] = bval
                self.prov(out_file, "AdSet", agname, bfield, bval, rel,
                          f"{cbase}.bidding_strategy.cpa_target" if bfield == "target_cpa_usd"
                          else f"{cbase}.bidding_strategy.roasTarget", "verbatim")
            self._add_doc(key, {"apiVersion": "amnesia/v1", "kind": "AdSet",
                                "metadata": {"name": agname, "campaign_ref": cname}, "spec": aset})
            self.counts[(platform_dir, vertical)]["ad_sets"] += 1
            if "keywords" in ag:
                self.dropped["keywords"] += 1

            rsas = ag.get("responsive_search_ads", [])
            if len(rsas) > 1:
                self.dropped["multi_rsa"] += 1
            for ri, rsa in enumerate(rsas):
                tname = f"{agname}__rsa"
                if len(rsas) > 1:
                    tname = f"{agname}__rsa{ri+1}"
                rbase = f"{gbase}.responsive_search_ads[{ri}]"
                headlines = list(rsa.get("headlines", []))[:15]
                descriptions = list(rsa.get("descriptions", []))[:4]
                if len(rsa.get("descriptions", [])) > 4:
                    self.dropped["descriptions_over_4_truncated"] += 1
                if len(rsa.get("headlines", [])) > 15:
                    self.dropped["headlines_over_15_truncated"] += 1
                tspec = {"status": "ENABLED", "ad_set_ref": agname,
                         "creative_feed_ref": feed_name,
                         "destination_url": rsa["final_url"],
                         "headlines": headlines, "descriptions": descriptions}
                self.prov(out_file, "AdTemplate", tname, "destination_url", rsa["final_url"], rel,
                          f"{rbase}.final_url", "verbatim")
                for hi, h in enumerate(headlines):
                    self.prov(out_file, "AdTemplate", tname, f"headlines[{hi}]", h, rel,
                              f"{rbase}.headlines[{hi}]", "verbatim")
                for di, dd in enumerate(descriptions):
                    self.prov(out_file, "AdTemplate", tname, f"descriptions[{di}]", dd, rel,
                              f"{rbase}.descriptions[{di}]", "verbatim")
                self._add_doc(key, {"apiVersion": "amnesia/v1", "kind": "AdTemplate",
                                    "metadata": {"name": tname, "campaign_ref": cname}, "spec": tspec})
                self.counts[(platform_dir, vertical)]["ad_templates"] += 1
                self.add_feed_lang(feed_name, c.get("language"), rel, f"{cbase}.language")

    # ---- demand gen ----------------------------------------------------
    def convert_demandgen(self, vertical_dir, vertical, platform_dir="google/demandgen"):
        for f in campaign_files(vertical_dir):
            data = json.load(open(f))
            rel = self.rel(f)
            for ci, c in enumerate(data.get("demand_gen_campaigns", [])):
                self._emit_dg_campaign(c, ci, rel, vertical, platform_dir)

    def _emit_dg_campaign(self, c, ci, rel, vertical, platform_dir):
        cname = c["name"]
        loc = locale_prefix(cname)
        key = (platform_dir, vertical, loc)
        out_file = f"{platform_dir}/{vertical}/{loc}.yaml"
        feed_name = self.feed_ref(vertical, "dg")
        cbase = f"demand_gen_campaigns[{ci}]"

        spec = {"objective": self.map_objective(c.get("marketing_tactic")), "status": "PAUSED"}
        self.prov(out_file, "Campaign", cname, "objective", spec["objective"], rel,
                  f"{cbase}.marketing_tactic", "derived")
        if "budget" in c and c["budget"].get("amount") is not None:
            spec["budget_daily_usd"] = c["budget"]["amount"]
            self.prov(out_file, "Campaign", cname, "budget_daily_usd", c["budget"]["amount"], rel,
                      f"{cbase}.budget.amount", "verbatim")
        geo = self.resolve_geo(c.get("geocode"), rel, f"{cbase}.geocode")
        if geo:
            spec["geo_preset_ref"] = geo
        self._add_doc(key, {"apiVersion": "amnesia/v1", "kind": "Campaign",
                            "metadata": {"name": cname}, "spec": spec})
        self.counts[(platform_dir, vertical)]["campaigns"] += 1
        if "campaign_group" in c:
            self.dropped["campaign_group"] += 1

        bs = c.get("bidding_strategy", {})
        strat, bfield, bval = self.map_bidding(bs)

        for gi, ag in enumerate(c.get("ad_groups", [])):
            agname = ag["name"]
            gbase = f"{cbase}.ad_groups[{gi}]"
            aset = {"status": "PAUSED", "bid_strategy": strat}
            if bfield:
                aset[bfield] = bval
                self.prov(out_file, "AdSet", agname, bfield, bval, rel,
                          f"{cbase}.bidding_strategy.cpa_target" if bfield == "target_cpa_usd"
                          else f"{cbase}.bidding_strategy.roasTarget", "verbatim")
            aset["demand_gen"] = {}   # required for google_demand_gen; source carries no audience data
            self._add_doc(key, {"apiVersion": "amnesia/v1", "kind": "AdSet",
                                "metadata": {"name": agname, "campaign_ref": cname}, "spec": aset})
            self.counts[(platform_dir, vertical)]["ad_sets"] += 1

            for ai, ad in enumerate(ag.get("multi_asset_ads", [])):
                tname = ad["name"]
                abase = f"{gbase}.multi_asset_ads[{ai}]"
                headlines = list(ad.get("headlines", []))[:15]
                descriptions = list(ad.get("descriptions", []))[:4]
                if len(ad.get("descriptions", [])) > 4:
                    self.dropped["descriptions_over_4_truncated"] += 1
                if len(ad.get("headlines", [])) > 15:
                    self.dropped["headlines_over_15_truncated"] += 1
                final_urls = ad.get("final_urls", [])
                if len(final_urls) > 1:
                    self.dropped["multi_final_url"] += 1
                dest = final_urls[0]
                logo_imgs = ad.get("logo_images", [])
                if not logo_imgs:
                    self.skipped.append((f"{rel}:{abase}", "google_demand_gen AdTemplate requires logo_asset_ref but source ad has no logo_images"))
                    continue
                logo = logo_imgs[0]
                if len(logo_imgs) > 1:
                    self.dropped["multi_logo_image"] += 1
                # image sets (marketing/square/portrait/tall) have no v2 slot
                for imgfld in ("marketing_images", "square_marketing_images",
                               "portrait_marketing_images", "tall_portrait_marketing_images"):
                    if imgfld in ad:
                        self.dropped["multi_size_image_set"] += 1
                bname = ad.get("business_name")
                tspec = {"status": "ENABLED", "ad_set_ref": agname,
                         "creative_feed_ref": feed_name, "logo_asset_ref": logo,
                         "business_name": bname, "destination_url": dest,
                         "headlines": headlines, "descriptions": descriptions}
                self.prov(out_file, "AdTemplate", tname, "logo_asset_ref", logo, rel,
                          f"{abase}.logo_images[0]", "verbatim")
                self.prov(out_file, "AdTemplate", tname, "business_name", bname, rel,
                          f"{abase}.business_name", "verbatim")
                self.prov(out_file, "AdTemplate", tname, "destination_url", dest, rel,
                          f"{abase}.final_urls[0]", "verbatim")
                for hi, h in enumerate(headlines):
                    self.prov(out_file, "AdTemplate", tname, f"headlines[{hi}]", h, rel,
                              f"{abase}.headlines[{hi}]", "verbatim")
                for di, dd in enumerate(descriptions):
                    self.prov(out_file, "AdTemplate", tname, f"descriptions[{di}]", dd, rel,
                              f"{abase}.descriptions[{di}]", "verbatim")
                self._add_doc(key, {"apiVersion": "amnesia/v1", "kind": "AdTemplate",
                                    "metadata": {"name": tname, "campaign_ref": cname}, "spec": tspec})
                self.counts[(platform_dir, vertical)]["ad_templates"] += 1
                self.add_feed_lang(feed_name, c.get("language"), rel, f"{cbase}.language")

    def _add_doc(self, key, doc):
        self.grouped.setdefault(key, [])
        self.grouped[key].append(doc)

    # ---- write ---------------------------------------------------------
    def write(self):
        os.makedirs(os.path.join(self.out, ".amnesia"), exist_ok=True)
        cfg = {
            "apiVersion": "amnesia/v1", "kind": "RepoConfig",
            "metadata": {"name": "default"},
            "spec": {"schema_version": 2, "default_branch": "main",
                     "platforms": {"google": {"enabled": True,
                                              "account_id": "4616809274",
                                              "credentials_ref": "hmg_test"}}},
        }
        with open(os.path.join(self.out, ".amnesia", "config.yml"), "w") as fh:
            fh.write(dump_doc(cfg))

        # geo presets (sorted by name)
        geo_docs = []
        for name in sorted(self.geos):
            geo_docs.append({"apiVersion": "amnesia/v1", "kind": "GeoPreset",
                             "metadata": {"name": name},
                             "spec": {"include": [{"type": "COUNTRY", "value": self.geos[name]}]}})
            src, jp = self.geo_src[name]
            self.prov("geo/geo-presets.yaml", "GeoPreset", name, "include[0].value",
                      self.geos[name], src, jp, "verbatim")
        if geo_docs:
            os.makedirs(os.path.join(self.out, "geo"), exist_ok=True)
            self._write_multi(os.path.join(self.out, "geo", "geo-presets.yaml"), geo_docs)

        # feeds (sorted by name)
        feed_docs = []
        for name in sorted(self.feeds):
            spec = {}
            langs = sorted(self.feeds[name])
            if langs:
                spec["languages"] = langs
                for lg in langs:
                    src, jp = self.feed_lang_src[(name, lg)]
                    self.prov("feeds/feeds.yaml", "CreativeFeed", name, f"languages[{lg}]",
                              lg, src, jp, "verbatim")
            feed_docs.append({"apiVersion": "amnesia/v1", "kind": "CreativeFeed",
                              "metadata": {"name": name}, "spec": spec})
        if feed_docs:
            os.makedirs(os.path.join(self.out, "feeds"), exist_ok=True)
            self._write_multi(os.path.join(self.out, "feeds", "feeds.yaml"), feed_docs)

        # campaign trees (sorted by key for deterministic layout)
        for key in sorted(self.grouped):
            platform_dir, vertical, loc = key
            d = os.path.join(self.out, platform_dir, vertical)
            os.makedirs(d, exist_ok=True)
            self._write_multi(os.path.join(d, f"{loc}.yaml"), self.grouped[key])

        with open(os.path.join(self.out, "_provenance.json"), "w") as fh:
            json.dump(self.provenance, fh, ensure_ascii=False, indent=1)

    def _write_multi(self, path, docs):
        with open(path, "w") as fh:
            fh.write("---\n".join(dump_doc(d) for d in docs))


def dump_doc(doc):
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=4096)


def main():
    efaq, out = sys.argv[1], sys.argv[2]
    conv = Converter(efaq, out)

    # Google Search — vertical-first dirs
    for v in SEARCH_VFIRST:
        vd = os.path.join(conv.efaq, v)
        if os.path.isdir(vd):
            conv.convert_search(vd, norm_vertical(v))

    # Google Search — island
    search_island = os.path.join(conv.efaq, "google", "search")
    if os.path.isdir(search_island):
        for v in sorted(os.listdir(search_island)):
            vd = os.path.join(search_island, v)
            if os.path.isdir(vd):
                conv.convert_search(vd, norm_vertical(v))

    # Google Demand Gen — island
    dg_island = os.path.join(conv.efaq, "google", "demandgen")
    if os.path.isdir(dg_island):
        for v in sorted(os.listdir(dg_island)):
            vd = os.path.join(dg_island, v)
            if os.path.isdir(vd) and v != "shared":
                conv.convert_demandgen(vd, norm_vertical(v))

    conv.write()

    # ---- report ----
    print("=== per-vertical counts ===")
    tc = ts = tt = 0
    for (pd, v) in sorted(conv.counts):
        c = conv.counts[(pd, v)]
        print(f"  {pd}/{v}: campaigns={c['campaigns']} ad_sets={c['ad_sets']} ad_templates={c['ad_templates']}")
        tc += c["campaigns"]; ts += c["ad_sets"]; tt += c["ad_templates"]
    print(f"  TOTAL: campaigns={tc} ad_sets={ts} ad_templates={tt} feeds={len(conv.feeds)} geo_presets={len(conv.geos)}")
    print("\n=== dropped field-classes encountered ===")
    for k in sorted(conv.dropped):
        print(f"  {k}: {conv.dropped[k]}")
    if conv.dropped_geo_values:
        print(f"  (non-ISO geocodes dropped from geo_preset_ref: {sorted(conv.dropped_geo_values)})")
    if conv.skipped:
        print("\n=== skipped (could not faithfully represent) ===")
        for s, r in conv.skipped:
            print(f"  {s}: {r}")
    print(f"\nprovenance entries: {len(conv.provenance)}")


if __name__ == "__main__":
    main()
