#!/usr/bin/env python3
"""
OSS Facebook Ads Library scraper — fresh tokens via undetected-chromedriver,
then GraphQL calls for full metadata including targeting/spend data.

Usage:
    python fb_ads_oss.py search "Nike" --country US
    python fb_ads_oss.py ads 123456789 --country US
    python fb_ads_oss.py ad-detail 9876543210 123456789
    python fb_ads_oss.py company "Bachem" --country US
    python fb_ads_oss.py batch --companies AmbioPharm Auspep Bachem --countries US DE

Output: JSON to stdout by default, --save-dir for files.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger("fb_ads")

DOC_IDS = {
    "page_search": "9333890689970605",
    "page_ads": "8539922039449935",
    "ad_details": "9407590475934210",
}
GRAPHQL_URL = "https://www.facebook.com/api/graphql/"


class FacebookSession:
    def __init__(self):
        self.session = requests.Session()
        self.tokens: Dict[str, str] = {}
        self._setup_session()

    def _setup_session(self):
        self.session.headers.update({
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://www.facebook.com",
            "referer": "https://www.facebook.com/ads/library/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        })

    def open_browser_and_extract_tokens(self, headless: bool = False) -> Dict[str, str]:
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        options = uc.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1400,900")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--lang=en-US")

        driver = uc.Chrome(options=options, version_main=149)
        try:
            # Try to load Firefox Facebook cookies into Chrome
            try:
                import browser_cookie3 as bc3
                driver.get("https://www.facebook.com/")
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                ff_cookies = list(bc3.firefox(domain_name='facebook.com'))
                injected = 0
                for c in ff_cookies:
                    try:
                        driver.add_cookie({
                            "name": c.name,
                            "value": c.value,
                            "domain": ".facebook.com",
                            "path": "/",
                        })
                        injected += 1
                    except Exception:
                        pass
                log.info(f"Injected {injected}/{len(ff_cookies)} Firefox cookies")
            except Exception as e:
                log.warning(f"Could not inject Firefox cookies: {e}")

            log.info("Opening facebook.com/ads/library/ ...")
            driver.get("https://www.facebook.com/ads/library/")
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(3)
            page_source = driver.page_source
            cookies = driver.get_cookies()
            log.info(f"Got {len(cookies)} cookies from browser")

            for c in cookies:
                self.session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".facebook.com"))

            self.tokens = {}
            patterns = {
                "fb_dtsg": r'"DTSGInitData",\[\],\{"token":"([^"]+)"',
                "lsd": r'"LSD",\[\],\{"token":"([^"]+)"',
                "client_revision": r'"client_revision":(\d+),',
                "hsi": r'"haste_session":"([^"]+)"',
                "spin_r": r'"__spin_r":(\d+),',
                "spin_b": r'"__spin_b":"([^"]+)"',
            }
            for key, pat in patterns.items():
                m = re.search(pat, page_source)
                if m:
                    self.tokens[key] = m.group(1)
                    log.info(f"  Extracted {key}: {m.group(1)[:30]}...")

            for c in cookies:
                if c["name"] == "c_user":
                    self.tokens["c_user"] = c["value"]
                if c["name"] == "xs":
                    self.tokens["xs"] = c["value"]

            if not self.tokens.get("fb_dtsg"):
                log.warning("No fb_dtsg token found.")
            return self.tokens
        finally:
            driver.quit()
            log.info("Browser closed.")

    def _get_request_params(self) -> Dict[str, str]:
        t = self.tokens
        return {
            "av": t.get("c_user", ""),
            "__user": t.get("c_user", ""),
            "__a": "1",
            "__req": random.choice("abcdefghij"),
            "__hs": t.get("hsi", ""),
            "dpr": "2",
            "__ccg": "EXCELLENT",
            "__rev": t.get("client_revision", ""),
            "__s": f"{hex(int(time.time()))[2:]}:{hex(random.randint(0, 16**8))[2:]}",
            "__hsi": str(int(time.time() * 1000)),
            "__comet_req": "1",
            "fb_dtsg": t.get("fb_dtsg", ""),
            "jazoest": f"2{sum(ord(c) for c in t.get('fb_dtsg', ''))}",
            "lsd": t.get("lsd", ""),
            "__spin_r": t.get("spin_r", ""),
            "__spin_b": t.get("spin_b", ""),
            "__spin_t": str(int(time.time())),
        }

    def _graphql_post(self, doc_id: str, variables: Dict, friendly_name: str) -> Optional[Dict]:
        data = self._get_request_params()
        data.update({
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": friendly_name,
            "variables": json.dumps(variables),
            "server_timestamps": "true",
            "doc_id": doc_id,
        })
        try:
            resp = self.session.post(GRAPHQL_URL, data=data, timeout=30)
            if resp.status_code != 200:
                log.error(f"GraphQL {friendly_name} returned {resp.status_code}")
                return None
            text = resp.text
            if text.startswith("for (;;);"):
                text = text[9:]
            return json.loads(text)
        except Exception as e:
            log.error(f"GraphQL POST error: {e}")
            return None

    def search_pages(self, query: str, country: str = "US") -> List[Dict]:
        variables = {
            "queryString": query,
            "isMobile": False,
            "country": country,
            "adType": "ALL",
        }
        data = self._graphql_post(DOC_IDS["page_search"], variables, "useAdLibraryTypeaheadSuggestionDataSourceQuery")
        if not data or "data" not in data:
            return []
        pages = []
        results = (data.get("data", {}).get("ad_library_main", {})
                       .get("typeahead_suggestions", {})
                       .get("page_results", []))
        for r in results:
            pages.append({
                "page_id": r.get("page_id"),
                "name": r.get("name"),
                "verification": r.get("verification"),
            })
        return pages

    def get_page_ads(self, page_id: str, country: str = "US", limit: int = 30) -> List[Dict]:
        variables = {
            "activeStatus": "active",
            "adType": "ALL",
            "bylines": [],
            "collationToken": str(uuid.uuid4()),
            "contentLanguages": [],
            "countries": [country],
            "cursor": None,
            "excludedIDs": None,
            "first": limit,
            "isTargetedCountry": False,
            "location": None,
            "mediaType": "all",
            "multiCountryFilterMode": None,
            "pageIDs": [],
            "potentialReachInput": None,
            "publisherPlatforms": [],
            "queryString": "",
            "regions": None,
            "searchType": "page",
            "sessionID": str(uuid.uuid4()),
            "sortData": None,
            "source": None,
            "startDate": None,
            "v": "96184a",
            "viewAllPageID": page_id,
        }
        data = self._graphql_post(DOC_IDS["page_ads"], variables, "AdLibrarySearchPaginationQuery")
        if not data or "data" not in data:
            return []
        ads = []
        edges = (data.get("data", {}).get("ad_library_main", {})
                     .get("search_results_connection", {})
                     .get("edges", []))
        for edge in edges:
            node = edge.get("node", {})
            collated = node.get("collated_results", [])
            for result in collated:
                if not result:
                    continue
                snapshot = result.get("snapshot", {}) or {}
                body = snapshot.get("body", {}) or {}
                body_text = body.get("text") if isinstance(body, dict) else str(body) if body else ""

                ad = {
                    "ad_archive_id": result.get("ad_archive_id"),
                    "page_id": result.get("page_id"),
                    "page_name": result.get("page_name"),
                    "status": result.get("is_active"),
                    "start_date": result.get("start_date"),
                    "end_date": result.get("end_date"),
                    "currency": result.get("currency"),
                    "impressions": result.get("impressions_with_index"),
                    "potential_reach": result.get("reach_estimate"),
                    "publisher_platforms": result.get("publisher_platform", []),
                    "body_text": body_text,
                }
                if snapshot:
                    images = snapshot.get("images", [])
                    if images:
                        ad["image_urls"] = [img.get("original_image_url") or img.get("resized_image_url")
                                           for img in images if isinstance(img, dict) and img]
                    videos = snapshot.get("videos", [])
                    if videos:
                        ad["video_urls"] = [v.get("video_hd_url") or v.get("video_sd_url")
                                           for v in videos if isinstance(v, dict) and v]
                    cards = snapshot.get("cards", [])
                    if cards:
                        ad["cards"] = [
                            {"body": c.get("body"), "title": c.get("title"),
                             "cta": c.get("cta_text"), "link": c.get("link_url"),
                             "image": c.get("resized_image_url")}
                            for c in cards if isinstance(c, dict)
                        ]
                ads.append(ad)
        return ads

    def get_ad_details(self, ad_archive_id: str, page_id: str) -> Optional[Dict]:
        variables = {
            "adArchiveID": ad_archive_id,
            "pageID": page_id,
            "country": "ALL",
            "sessionID": str(uuid.uuid4()),
            "source": None,
            "isAdNonPolitical": True,
            "isAdNotAAAEligible": False,
            "__relay_internal__pv__AdLibraryFinservGraphQLGKrelayprovider": True,
        }
        data = self._graphql_post(DOC_IDS["ad_details"], variables, "AdLibraryAdDetailsV2Query")
        if not data or "data" not in data:
            return None
        main = data.get("data", {}).get("ad_library_main", {})
        ad_details = main.get("ad_details", {})
        if not ad_details:
            return None

        advertiser = ad_details.get("advertiser", {})
        page = advertiser.get("page", {}) or {}
        page_info = (advertiser.get("ad_library_page_info", {}) or {}).get("page_info", {}) or {}
        page_spend = (advertiser.get("ad_library_page_info", {}) or {}).get("page_spend", {}) or {}
        aaa_info = ad_details.get("aaa_info", {}) or {}

        result = {
            "ad": {
                "archive_id": ad_archive_id,
                "page_id": page_id,
                "is_political": page_spend.get("is_political_page", False),
                "targeting": {
                    "locations": [loc.get("name", "") for loc in aaa_info.get("location_audience", [])
                                 if loc and not loc.get("excluded")],
                    "excluded_locations": [loc.get("name", "") for loc in aaa_info.get("location_audience", [])
                                           if loc and loc.get("excluded")],
                    "gender": aaa_info.get("gender_audience", ""),
                    "age_min": aaa_info.get("age_audience", {}).get("min"),
                    "age_max": aaa_info.get("age_audience", {}).get("max"),
                    "eu_total_reach": aaa_info.get("eu_total_reach"),
                    "demographic_breakdown": aaa_info.get("age_country_gender_reach_breakdown", []),
                },
                "spend": None,
                "payer_beneficiary": aaa_info.get("payer_beneficiary_data", []),
            },
            "page": {
                "name": page_info.get("page_name"),
                "category": page_info.get("page_category"),
                "about": (page.get("about", {}) or {}).get("text"),
                "verification": page_info.get("page_verification"),
                "profile_url": page_info.get("page_profile_uri"),
                "likes": page_info.get("likes", 0),
            },
            "instagram": {
                "username": page_info.get("ig_username"),
                "followers": page_info.get("ig_followers", 0),
                "verified": page_info.get("ig_verification", False),
            },
        }
        lifetime = page_spend.get("lifetime_by_disclaimer", [])
        if lifetime and isinstance(lifetime, list) and len(lifetime) > 0:
            result["ad"]["spend"] = lifetime[0].get("spend")
        return result

    def scrape_company(self, company_name: str, country: str = "US",
                       headless: bool = True) -> Dict[str, Any]:
        result = {
            "company": company_name,
            "country": country,
            "pages_found": 0,
            "total_ads": 0,
            "ads_with_targeting": 0,
            "pages": [],
            "errors": [],
        }
        pages = self.search_pages(company_name, country)
        result["pages_found"] = len(pages)
        result["pages"] = pages
        if not pages:
            log.info(f"No pages found for '{company_name}' in {country}")
            return result

        for page in pages:
            page_id = page.get("page_id")
            page_name = page.get("name", company_name)
            if not page_id:
                continue
            log.info(f"  Getting ads for {page_name} in {country}...")
            ads = self.get_page_ads(page_id, country)
            page_result = {"page_id": page_id, "page_name": page_name, "ads": []}
            for ad in ads[:10]:
                ad_id = ad.get("ad_archive_id")
                if not ad_id:
                    continue
                ad_entry = {k: v for k, v in ad.items()}
                details = self.get_ad_details(ad_id, page_id)
                if details:
                    ad_entry["targeting"] = details["ad"]["targeting"]
                    ad_entry["spend"] = details["ad"]["spend"]
                    ad_entry["page_info"] = details["page"]
                    ad_entry["instagram"] = details["instagram"]
                    ad_entry["is_political"] = details["ad"]["is_political"]
                    result["ads_with_targeting"] += 1
                page_result["ads"].append(ad_entry)
                result["total_ads"] += 1
            result["pages"].append(page_result)
        return result


def scrape_companies_batch(companies: List[str], countries: List[str],
                           headless: bool = True, save_dir: str = "output") -> List[Dict]:
    session = FacebookSession()
    session.open_browser_and_extract_tokens(headless=headless)
    if not session.tokens.get("fb_dtsg"):
        log.error("Failed to extract tokens.")
        return []

    all_results = []
    for company in companies:
        company = company.strip()
        if not company:
            continue
        for country in countries:
            log.info(f"\n{'='*60}")
            log.info(f"Scraping '{company}' in {country}...")
            log.info(f"{'='*60}")
            result = session.scrape_company(company, country, headless=headless)
            all_results.append(result)
            if save_dir:
                os.makedirs(save_dir, exist_ok=True)
                safe_name = re.sub(r'[^a-zA-Z0-9]+', '_', company).strip('_')
                fpath = os.path.join(save_dir, f"{safe_name}_{country}.json")
                with open(fpath, "w") as f:
                    json.dump(result, f, indent=2, default=str)
                log.info(f"Saved {fpath}")
    return all_results


def main():
    parser = argparse.ArgumentParser(description="Facebook Ads Library OSS Scraper")
    parser.add_argument("mode", choices=["search", "ads", "ad-detail", "company", "batch"])
    parser.add_argument("query", nargs="?")
    parser.add_argument("--page-id")
    parser.add_argument("--ad-id")
    parser.add_argument("--country", default="US")
    parser.add_argument("--countries", nargs="+", default=["US", "DE"])
    parser.add_argument("--companies", nargs="+")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--save-dir", default="output")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    session = FacebookSession()
    session.open_browser_and_extract_tokens(headless=args.headless)
    if not session.tokens.get("fb_dtsg"):
        log.error("Failed to extract Facebook session tokens.")
        sys.exit(1)

    if args.mode == "search":
        pages = session.search_pages(args.query, args.country)
        print(json.dumps(pages, indent=2))
    elif args.mode == "ads":
        ads = session.get_page_ads(args.page_id, args.country, args.limit)
        print(json.dumps(ads, indent=2, default=str))
    elif args.mode == "ad-detail":
        details = session.get_ad_details(args.ad_id, args.page_id)
        print(json.dumps(details, indent=2, default=str))
    elif args.mode == "company":
        result = session.scrape_company(args.query, args.country)
        print(json.dumps(result, indent=2, default=str))
    elif args.mode == "batch":
        companies = args.companies or []
        if not companies:
            log.error("--companies required for batch mode")
            sys.exit(1)
        log.info(f"Batch scraping {len(companies)} companies in {args.countries}")
        results = scrape_companies_batch(companies, args.countries,
                                          headless=args.headless, save_dir=args.save_dir)
        summary_path = os.path.join(args.save_dir, "_summary.json")
        with open(summary_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        log.info(f"Full summary saved to {summary_path}")


if __name__ == "__main__":
    main()
