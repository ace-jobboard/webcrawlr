#!/usr/bin/env python3
"""
University Website Link Checker — Multi-Site Edition
Crawls one site per invocation; results are saved as JSON artifacts.
A final "report" job aggregates all results and sends one combined email.
"""

import os
import sys
import json
import time
import smtplib
import logging
import argparse
from urllib.parse import urljoin, urlparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from collections import deque
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Configuration ─────────────────────────────────────────────────────────────
BASE_URL        = os.environ.get("BASE_URL", "")
SITE_NAME       = os.environ.get("SITE_NAME", BASE_URL)
MAX_PAGES       = int(os.environ.get("MAX_PAGES", "1000"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))
DELAY_SECONDS   = float(os.environ.get("DELAY_SECONDS", "0.5"))
CHECK_EXTERNAL  = os.environ.get("CHECK_EXTERNAL", "false").lower() == "true"
USER_AGENT      = "UniversityLinkBot/2.0 (internal monitoring)"

SMTP_HOST  = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT") or "587")
SMTP_USER  = os.environ.get("SMTP_USER", "")
SMTP_PASS  = os.environ.get("SMTP_PASS", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO   = os.environ.get("EMAIL_TO", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def same_domain(url, base):
    return urlparse(url).netloc == urlparse(base).netloc

def normalize(url):
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return p._replace(fragment="", path=path, query=p.query).geturl()

def is_crawlable(url):
    p = urlparse(url)
    return p.scheme in ("http", "https") and same_domain(url, BASE_URL)

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip",
    ".rar", ".mp4", ".mp3", ".webm", ".avi", ".mov", ".woff", ".woff2",
    ".ttf", ".eot",
}
SKIP_PATH_PATTERNS = (
    "/wp-content/uploads/",   # media files
    "/wp-json/",              # REST API
)
import re as _re
_ARCHIVE_RE = _re.compile(r"/\d{4}(/\d{2})?/?$")  # /2023 or /2023/04

def should_crawl_for_links(url):
    """True if we should fetch this page and extract links from it.
    False for media files and WordPress archive/category/author index pages
    (we still check them as links, we just don't queue them for crawling)."""
    p = urlparse(url)
    path = p.path.lower()
    if any(path.endswith(ext) for ext in MEDIA_EXTENSIONS):
        return False
    if any(pat in path for pat in SKIP_PATH_PATTERNS):
        return False
    if _ARCHIVE_RE.search(p.path):
        return False
    for segment in ("/category/", "/author/", "/tag/", "/page/"):
        if segment in path:
            return False
    return True

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

def check_url(url):
    try:
        resp = SESSION.head(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code in (405, 501):
            resp = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True)
            resp.close()
        if resp.status_code == 503:
            return {"url": url, "status": 503, "ok": True, "error": None}
        return {"url": url, "status": resp.status_code, "ok": resp.status_code < 400, "error": None}
    except requests.exceptions.Timeout:
        return {"url": url, "status": None, "ok": True, "error": None}
    except requests.exceptions.SSLError as e:
        return {"url": url, "status": None, "ok": False, "error": f"SSL error: {e}"}
    except requests.exceptions.ConnectionError as e:
        return {"url": url, "status": None, "ok": False, "error": f"Connection error: {e}"}
    except Exception as e:
        return {"url": url, "status": None, "ok": False, "error": str(e)}

def extract_links(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append(normalize(urljoin(page_url, href)))
    return links


# ── Crawler ───────────────────────────────────────────────────────────────────

def crawl():
    visited_pages = set()
    checked_urls  = {}
    broken        = []
    queue         = deque([normalize(BASE_URL)])
    visited_pages.add(normalize(BASE_URL))
    page_count = 0

    while queue and page_count < MAX_PAGES:
        page_url = queue.popleft()
        page_count += 1
        log.info(f"[{page_count}/{MAX_PAGES}] Crawling: {page_url}")

        try:
            resp   = SESSION.get(page_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            ok = resp.status_code < 400 or resp.status_code == 503
            result = {"url": page_url, "status": resp.status_code,
                      "ok": ok, "error": None, "found_on": "crawler"}
        except requests.exceptions.Timeout:
            checked_urls[page_url] = {"url": page_url, "status": None, "ok": True, "error": None, "found_on": "crawler"}
            continue
        except Exception as e:
            result = {"url": page_url, "status": None, "ok": False,
                      "error": str(e), "found_on": "crawler"}
            checked_urls[page_url] = result
            broken.append(result)
            continue

        checked_urls[page_url] = result
        if not result["ok"]:
            broken.append(result)
            continue

        if "text/html" not in resp.headers.get("Content-Type", ""):
            continue

        for link in extract_links(resp.text, page_url):
            if link in checked_urls:
                continue
            if is_crawlable(link) and should_crawl_for_links(link):
                checked_urls[link] = None
                if link not in visited_pages:
                    visited_pages.add(link)
                    queue.append(link)
            elif CHECK_EXTERNAL or is_crawlable(link):
                log.info(f"  Checking: {link}")
                ext = check_url(link)
                ext["found_on"] = page_url
                checked_urls[link] = ext
                if not ext["ok"]:
                    broken.append(ext)
                time.sleep(DELAY_SECONDS)

        time.sleep(DELAY_SECONDS)

    log.info(f"Done. Pages: {page_count}, URLs checked: {len(checked_urls)}")
    return broken, [r for r in checked_urls.values() if r is not None]


# ── Save results as JSON ──────────────────────────────────────────────────────

def save_results(broken, total):
    out = {
        "site_name":     SITE_NAME,
        "base_url":      BASE_URL,
        "checked_at":    datetime.utcnow().isoformat() + "Z",
        "total_checked": total,
        "broken_count":  len(broken),
        "broken":        broken,
    }
    slug = urlparse(BASE_URL).netloc.replace(".", "_")
    path = Path(f"results_{slug}.json")
    path.write_text(json.dumps(out, indent=2))
    log.info(f"Results saved to {path}")
    return path


# ── Combined HTML email ───────────────────────────────────────────────────────

def build_site_section(site_data):
    name    = site_data["site_name"]
    url     = site_data["base_url"]
    total   = site_data["total_checked"]
    broken  = site_data["broken"]
    checked = site_data["checked_at"]

    status_badge = (
        '<span style="color:#27ae60;font-weight:bold;">✅ All OK</span>'
        if not broken else
        f'<span style="color:#c0392b;font-weight:bold;">🔴 {len(broken)} broken</span>'
    )

    rows = ""
    for item in broken:
        status   = item["status"] or "—"
        error    = item["error"] or ""
        found_on = item.get("found_on", "—")
        rows += f"""
        <tr>
          <td style="padding:5px 8px;border-bottom:1px solid #eee;word-break:break-all;">
            <a href="{item['url']}">{item['url']}</a></td>
          <td style="padding:5px 8px;border-bottom:1px solid #eee;text-align:center;
              color:#c0392b;">{status}</td>
          <td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:11px;
              color:#666;">{error}</td>
          <td style="padding:5px 8px;border-bottom:1px solid #eee;font-size:11px;
              word-break:break-all;"><a href="{found_on}">{found_on}</a></td>
        </tr>"""

    table = f"""
      <table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:8px;">
        <thead><tr style="background:#f5f5f5;">
          <th style="padding:6px 8px;text-align:left;">Broken URL</th>
          <th style="padding:6px 8px;">Status</th>
          <th style="padding:6px 8px;text-align:left;">Error</th>
          <th style="padding:6px 8px;text-align:left;">Found On</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>""" if broken else \
      '<p style="color:#27ae60;margin:4px 0 0;">No broken links found.</p>'

    return f"""
    <div style="margin-bottom:24px;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;">
      <div style="background:#f9f9f9;padding:12px 16px;border-bottom:1px solid #e0e0e0;">
        <strong style="font-size:15px;">{name}</strong>
        &nbsp;<span style="color:#999;font-size:12px;">{url}</span>
        &nbsp;&nbsp;{status_badge}
        <span style="float:right;font-size:11px;color:#bbb;">
          {total} URLs · {checked[:16].replace('T',' ')} UTC
        </span>
      </div>
      <div style="padding:12px 16px;">{table}</div>
    </div>"""


def send_combined_email(results_dir="."):
    files = sorted(Path(results_dir).glob("results_*.json"))
    if not files:
        log.error("No result files found.")
        sys.exit(1)

    sites        = [json.loads(f.read_text()) for f in files]
    total_broken = sum(s["broken_count"] for s in sites)
    total_urls   = sum(s["total_checked"] for s in sites)
    now          = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    subject = (
        f"🔴 {total_broken} broken link(s) across {len(sites)} sites — {now}"
        if total_broken else
        f"✅ All links OK across {len(sites)} sites — {now}"
    )

    summary_rows = "".join(
        f"""<tr>
          <td style="padding:5px 10px;">{s['site_name']}</td>
          <td style="padding:5px 10px;text-align:center;">{s['total_checked']}</td>
          <td style="padding:5px 10px;text-align:center;
              color:{'#c0392b' if s['broken_count'] else '#27ae60'};">
            {s['broken_count'] if s['broken_count'] else '✅'}
          </td>
        </tr>"""
        for s in sites
    )

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:960px;margin:auto;padding:20px;">
      <h2 style="margin-bottom:4px;">🔗 University Website Link Report</h2>
      <p style="color:#888;margin-top:0;">{now} &nbsp;·&nbsp; {len(sites)} sites &nbsp;·&nbsp; {total_urls} total URLs checked</p>

      <h3 style="margin-bottom:8px;">Summary</h3>
      <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:28px;">
        <thead><tr style="background:#f2f2f2;">
          <th style="padding:7px 10px;text-align:left;">Site</th>
          <th style="padding:7px 10px;">URLs Checked</th>
          <th style="padding:7px 10px;">Broken Links</th>
        </tr></thead>
        <tbody>{summary_rows}</tbody>
      </table>

      <h3 style="margin-bottom:12px;">Details</h3>
      {"".join(build_site_section(s) for s in sites)}

      <p style="margin-top:24px;font-size:11px;color:#ccc;">UniversityLinkBot · GitHub Actions</p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    recipients = [e.strip() for e in EMAIL_TO.split(",")]
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, recipients, msg.as_string())
        log.info(f"Combined email sent to: {EMAIL_TO}")
    except Exception as e:
        log.error(f"Failed to send email: {e}")
        sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",     action="store_true", help="Skip email")
    parser.add_argument("--report",      action="store_true", help="Aggregate & email mode")
    parser.add_argument("--results-dir", default=".",         help="Directory with results_*.json")
    args = parser.parse_args()

    if args.report:
        if args.dry_run:
            for f in sorted(Path(args.results_dir).glob("results_*.json")):
                d = json.loads(f.read_text())
                log.info(f"[DRY RUN] {d['site_name']}: {d['broken_count']} broken / {d['total_checked']} checked")
        else:
            send_combined_email(args.results_dir)
        return

    if not BASE_URL:
        log.error("BASE_URL is required.")
        sys.exit(1)

    log.info(f"Crawling: {BASE_URL}")
    broken, all_results = crawl()
    save_results(broken, len(all_results))

    if broken:
        log.warning(f"{len(broken)} broken link(s) found.")
        sys.exit(1)
    else:
        log.info("No broken links found. 🎉")


if __name__ == "__main__":
    main()
