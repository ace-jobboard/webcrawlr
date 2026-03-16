# 🔗 University Link Checker Bot — Multi-Site Edition

Automatically crawls **5 university websites** every hour, checks all links, and sends **one combined email report**.

---

## How It Works

Each hour, GitHub Actions spins up **5 parallel crawler jobs** (staggered 10 minutes apart so they don't all hit the network at once). When all 5 are done, a final **report job** collects the results and sends a single email with:

- A summary table across all 5 sites
- Per-site breakdown of every broken link, its status code, and which page it was found on

```
:00  → Site 1 starts crawling
:10  → Site 2 starts crawling
:20  → Site 3 starts crawling
:30  → Site 4 starts crawling
:40  → Site 5 starts crawling
        ↓  (all finish)
        → Combined email sent
```

---

## Setup (15–20 minutes)

### Step 1 — Create the repository

Create a **new private GitHub repository** and upload these files:

```
university-link-checker/
├── link_checker.py
└── .github/
    └── workflows/
        └── link-checker.yml
```

---

### Step 2 — Add repository Variables (non-sensitive config)

Go to **Settings → Secrets and variables → Actions → Variables tab**:

| Variable Name      | Example Value                       |
|-------------------|-------------------------------------|
| `SITE_1_URL`      | `https://www.university.edu`        |
| `SITE_1_NAME`     | `Main University Site`              |
| `SITE_2_URL`      | `https://library.university.edu`    |
| `SITE_2_NAME`     | `Library`                           |
| `SITE_3_URL`      | `https://admissions.university.edu` |
| `SITE_3_NAME`     | `Admissions`                        |
| `SITE_4_URL`      | `https://research.university.edu`   |
| `SITE_4_NAME`     | `Research`                          |
| `SITE_5_URL`      | `https://alumni.university.edu`     |
| `SITE_5_NAME`     | `Alumni`                            |
| `MAX_PAGES`       | `500` (per site)                    |
| `REQUEST_TIMEOUT` | `10`                                |
| `DELAY_SECONDS`   | `0.5`                               |

---

### Step 3 — Add repository Secrets (sensitive config)

Go to **Settings → Secrets and variables → Actions → Secrets tab**:

| Secret Name  | Description                                          |
|-------------|------------------------------------------------------|
| `SMTP_HOST` | Your mail server, e.g. `smtp.gmail.com`             |
| `SMTP_PORT` | Usually `587`                                        |
| `SMTP_USER` | Sending email account                                |
| `SMTP_PASS` | Password or App Password                             |
| `EMAIL_FROM`| From address                                         |
| `EMAIL_TO`  | Recipient(s) — comma-separate for multiple addresses |

---

### Step 4 — Gmail App Password (if using Gmail)

1. Google Account → **Security** → enable 2-Step Verification
2. Search for **App Passwords** → create one named "Link Checker Bot"
3. Use the 16-character code as `SMTP_PASS`

---

### Step 5 — Test it

1. Repo → **Actions** tab → **"University Link Checker"** → **"Run workflow"**
2. Set `dry_run = true` to see the crawl output without sending email
3. Watch the 5 site jobs run, then the "Send Combined Email Report" job at the end

---

## Email Format

**Subject:** `🔴 12 broken link(s) across 5 sites — 2025-09-01 09:00 UTC`

The email contains:
1. A **summary table** — all 5 sites, URLs checked, broken count
2. A **details section** — per-site table with broken URL / status code / error message / page it was found on

---

## Customisation

| Goal | How |
|------|-----|
| Change schedule | Edit `cron: "0 * * * *"` — e.g. `"0 */6 * * *"` for every 6 hours |
| Adjust stagger gap | Change `sleep 600` (seconds) in each site job |
| Add a 6th site | Duplicate a crawl job block and add `SITE_6_URL` / `SITE_6_NAME` vars |
| Different page limits per site | Add `SITE_1_MAX_PAGES` etc. and reference them in the workflow |
| Ignore certain URLs | Edit `is_crawlable()` in `link_checker.py` |

---

## Cost

**Free.** GitHub Actions free tier: 2,000 minutes/month on private repos (unlimited on public).
5 sites × ~15 min each × 24 runs/day ≈ 1,800 min/month — right within the free tier.
