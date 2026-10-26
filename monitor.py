#!/usr/bin/env python3
"""
Job monitor. Polls Greenhouse, Lever, and Ashby job boards for the companies
in companies.yaml and sends a phone alert (via ntfy) for every new matching role.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
CONFIG_FILE = ROOT / "companies.yaml"
STATE_FILE = ROOT / "seen.json"
TIMEOUT = 20
UA = {"User-Agent": "personal-job-monitor/1.0"}
MAX_ALERTS_PER_RUN = 15


# ---------- fetchers: each yields dicts with id, title, location, url, posted ----------

def fetch_greenhouse(slug):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        yield {
            "id": f"greenhouse:{slug}:{j.get('id')}",
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "posted": j.get("first_published") or j.get("updated_at"),
        }


def lever_pay(sr):
    if not isinstance(sr, dict) or not sr.get("min"):
        return ""
    cur = sr.get("currency", "")
    return f"{cur} {int(sr['min']):,}-{int(sr.get('max') or sr['min']):,}".strip()


def fetch_lever(slug):
    r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json():
        cats = j.get("categories") or {}
        loc = cats.get("location", "") or ""
        if (j.get("workplaceType") or "").lower() == "remote":
            loc = f"{loc} Remote".strip()
        yield {
            "id": f"lever:{slug}:{j.get('id')}",
            "title": j.get("text", ""),
            "location": loc,
            "url": j.get("hostedUrl", ""),
            "posted": j.get("createdAt"),
            "pay": lever_pay(j.get("salaryRange")),
        }


def fetch_ashby(slug):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        locs = [j.get("location") or ""]
        for s in j.get("secondaryLocations") or []:
            locs.append(s.get("location", "") if isinstance(s, dict) else str(s))
        if j.get("isRemote") or (j.get("workplaceType") or "").lower() == "remote":
            locs.append("Remote")
        yield {
            "id": f"ashby:{slug}:{j.get('id')}",
            "title": j.get("title", ""),
            "location": " / ".join(x for x in locs if x),
            "url": j.get("jobUrl", ""),
            "posted": j.get("publishedAt"),
            "pay": ((j.get("compensation") or {}).get("compensationTierSummary") or ""),
        }


def fetch_workday(url, searches=("content", "editorial")):
    """url is the public careers page, e.g. https://acme.wd5.myworkdayjobs.com/External"""
    m = re.match(r"https?://([^/]+)/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", url.strip())
    if not m:
        raise ValueError("bad Workday URL")
    host, site = m.group(1), m.group(2)
    tenant = host.split(".")[0]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    hdrs = {**UA, "Content-Type": "application/json", "Accept": "application/json"}
    for search in searches:
        for offset in range(0, 100, 20):
            r = requests.post(api, json={"appliedFacets": {}, "limit": 20, "offset": offset,
                                         "searchText": search}, headers=hdrs, timeout=TIMEOUT)
            r.raise_for_status()
            posts = r.json().get("jobPostings") or []
            for j in posts:
                loc = j.get("locationsText", "") or ""
                if re.match(r"^\d+ Locations?$", loc):
                    loc = ""             # multi-location; let it through
                yield {
                    "id": f"workday:{tenant}:{j.get('externalPath')}",
                    "title": j.get("title", ""),
                    "location": loc,
                    "url": f"https://{host}/{site}{j.get('externalPath', '')}",
                    "posted": j.get("postedOn"),
                }
            if len(posts) < 20:
                break


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "workday": fetch_workday}


# ---------- matching ----------

def rx(patterns):
    return [re.compile(p, re.I) for p in (patterns or [])]


def any_match(patterns, text):
    return any(p.search(text or "") for p in patterns)


def title_ok(title, cfg):
    return any_match(cfg["_title_in"], title) and not any_match(cfg["_title_out"], title)


def location_ok(location, cfg):
    if not location.strip():
        return True                      # some boards leave location blank; don't miss them
    if any_match(cfg["_loc_allow"], location):
        return True
    if any_match(cfg["_loc_block"], location):
        return False
    return not cfg["_loc_allow"]         # no allow list means accept everything not blocked


# ---------- helpers ----------

def age_text(posted):
    if not posted:
        return ""
    if isinstance(posted, str) and posted.lower().startswith("posted"):
        return posted.lower()
    try:
        if isinstance(posted, (int, float)):
            dt = datetime.fromtimestamp(posted / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if hours < 1:
            return "posted <1h ago"
        if hours < 48:
            return f"posted {int(hours)}h ago"
        return f"posted {int(hours // 24)}d ago"
    except Exception:
        return ""


def notify(server, topic, title, body, click=None, priority="high", tags="briefcase"):
    if not topic:
        print(f"[no NTFY_TOPIC set] {title} | {body}")
        return
    headers = {
        "Title": title.encode("ascii", "ignore").decode(),
        "Priority": priority,
        "Tags": tags,
    }
    if click:
        headers["Click"] = click
    try:
        requests.post(f"{server.rstrip('/')}/{topic}", data=body.encode("utf-8"),
                      headers=headers, timeout=TIMEOUT)
    except Exception as e:
        print(f"Notification failed: {e}")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"companies": [], "jobs": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True))


# ---------- main ----------

def main():
    cfg = yaml.safe_load(CONFIG_FILE.read_text())
    cfg["_title_in"] = rx(cfg.get("title_include"))
    cfg["_title_out"] = rx(cfg.get("title_exclude"))
    cfg["_loc_allow"] = rx(cfg.get("location_allow"))
    cfg["_loc_block"] = rx(cfg.get("location_block"))
    server = cfg.get("ntfy_server", "https://ntfy.sh")
    topic = os.environ.get("NTFY_TOPIC", "").strip()

    state = load_state()
    known_companies = set(state["companies"])
    seen = state["jobs"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    new_alerts, seeded, failures = [], [], []
    checked = 0

    for ats, slugs in (cfg.get("companies") or {}).items():
        fetch = FETCHERS.get(ats)
        if not fetch:
            print(f"Unknown board type '{ats}', skipping")
            continue
        for slug in slugs or []:
            key = f"{ats}:{slug}"
            label = slug.split("//")[-1].split(".")[0] if ats == "workday" else slug
            try:
                if ats == "workday" and cfg.get("workday_search_terms"):
                    jobs = list(fetch(slug, cfg["workday_search_terms"]))
                else:
                    jobs = list(fetch(slug))
            except Exception as e:
                failures.append(f"{key} ({e.__class__.__name__}: {str(e)[:80]})")
                continue
            checked += 1
            first_time = key not in known_companies
            for j in jobs:
                if not (title_ok(j["title"], cfg) and location_ok(j["location"], cfg)):
                    continue
                if j["id"] in seen:
                    continue
                seen[j["id"]] = now
                j["company"] = label
                (seeded if first_time else new_alerts).append(j)
            known_companies.add(key)

    # Alerts for genuinely new postings
    for j in new_alerts[:MAX_ALERTS_PER_RUN]:
        body = f"{j['company']} | {j['location'] or 'location n/a'}"
        if j.get("pay"):
            body += f" | {j['pay']}"
        age = age_text(j["posted"])
        if age:
            body += f" | {age}"
        body += "\nTap to open the posting."
        notify(server, topic, f"NEW: {j['title']}", body, click=j["url"], priority="urgent",
               tags="rotating_light,briefcase")
        print(f"ALERT  {j['company']}: {j['title']} [{j['location']}] {j['url']}")
    if len(new_alerts) > MAX_ALERTS_PER_RUN:
        extra = len(new_alerts) - MAX_ALERTS_PER_RUN
        notify(server, topic, f"{extra} more new roles", "Check the Actions log for the full list.")

    # One digest for companies being watched for the first time
    if seeded or (not state["companies"] and checked):
        lines = [f"- {j['company']}: {j['title']} ({j['location'] or 'n/a'})" for j in seeded[:10]]
        if len(seeded) > 10:
            lines.append(f"...and {len(seeded) - 10} more (see Actions log)")
        body = (f"Watching {checked} boards. {len(seeded)} matching roles already open "
                f"at newly added companies:\n" + ("\n".join(lines) if lines else "none"))
        notify(server, topic, "Job monitor is watching", body, priority="default", tags="eyes")
        for j in seeded:
            print(f"SEEDED {j['company']}: {j['title']} [{j['location']}] {j['url']}")

    state["companies"] = sorted(known_companies)
    state["jobs"] = seen
    save_state(state)

    print(f"\nChecked {checked} boards, {len(new_alerts)} new alerts, {len(seeded)} seeded.")
    if failures:
        print("\nFAILED (fix the slug or remove it from companies.yaml):")
        for f in failures:
            print(f"  {f}")


if __name__ == "__main__":
    sys.exit(main())
