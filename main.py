# main.py
# The brain of the agent. You don't need to change anything in here.

import os
import re
import ssl
import json
import time
import smtplib
import hashlib
import traceback
import html as htmllib
from datetime import date, datetime
from email.message import EmailMessage

import requests
from bs4 import BeautifulSoup
import anthropic

from companies import COMPANIES

STATE_FILE = "seen_jobs.json"
PAGE_FILE = "index.html"
MODEL = "claude-haiku-4-5-20251001"
EMAIL_CAP = 100

GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
client = anthropic.Anthropic()


def fetch(url, browser):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        if r.ok and len(r.text) > 3000:
            return r.text
    except Exception:
        pass
    page = None
    try:
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        return page.content()
    except Exception:
        return ""
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass


def page_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)[:18000]


def extract_jobs(company, text):
    if not text.strip():
        return []
    prompt = (
        f"Below is the text of the careers page for {company}. "
        "List every current job opening you can find. "
        'Return ONLY a JSON array. Each item must look like '
        '{"title": string, "url": string or null, "location": string or null}. '
        "No explanation and no markdown fences.\n\nPAGE TEXT:\n" + text
    )
    try:
        msg = client.messages.create(
            model=MODEL, max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
        raw = " ".join(parts).strip()
        raw = re.sub(r"^```(json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        data = json.loads(raw)
    except Exception:
        return []
    clean = []
    if isinstance(data, list):
        for j in data:
            if isinstance(j, dict):
                clean.append({
                    "title": (str(j.get("title")).strip() if j.get("title") else "(no title)"),
                    "url": (str(j.get("url")).strip() if j.get("url") else None),
                    "location": (str(j.get("location")).strip() if j.get("location") else None),
                })
    return clean


def job_id(company, job):
    key = f"{company}|{job.get('title', '')}|{job.get('url', '')}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def load_seen():
    if os.path.exists(STATE_FILE):
        try:
            return set(json.load(open(STATE_FILE)))
        except Exception:
            return set()
    return None


def save_seen(ids):
    json.dump(sorted(ids), open(STATE_FILE, "w"))


def send_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = GMAIL_ADDRESS
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.send_message(msg)


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Israeli Biopharma - Open Positions</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 0 16px 60px; background:#0f1115; color:#e7e9ee; }
  header { padding: 28px 0 12px; }
  h1 { margin:0 0 4px; font-size: 22px; }
  .meta { color:#9aa3b2; font-size: 13px; }
  #q { width:100%; max-width:520px; margin:16px 0; padding:12px 14px; font-size:15px;
       border:1px solid #2a2f3a; border-radius:10px; background:#171a21; color:#e7e9ee; }
  table { width:100%; border-collapse: collapse; }
  th, td { text-align:left; padding:10px 12px; border-bottom:1px solid #1e222b; font-size:14px; vertical-align:top; }
  th { position:sticky; top:0; background:#0f1115; color:#9aa3b2; font-weight:600; }
  tr:hover td { background:#141821; }
  .co { font-weight:600; white-space:nowrap; }
  a.apply { display:inline-block; padding:6px 12px; background:#2b6cff; color:#fff;
            text-decoration:none; border-radius:8px; font-size:13px; white-space:nowrap; }
  a.apply:hover { background:#1e57d6; }
  .none { color:#6b7280; font-size:13px; }
</style>
</head>
<body>
<header>
  <h1>Israeli Biopharma &mdash; Open Positions</h1>
  <div class="meta">{{COUNT}} openings across the tracked companies &middot; updated {{DATE}}</div>
  <input id="q" placeholder="Filter by company, role, or location...">
</header>
<table>
  <thead><tr><th>Company</th><th>Role</th><th>Location</th><th>Apply</th></tr></thead>
  <tbody>
{{ROWS}}
  </tbody>
</table>
<script>
  const q=document.getElementById('q');
  const rows=[...document.querySelectorAll('tbody tr')];
  q.addEventListener('input',()=>{const v=q.value.toLowerCase();
    rows.forEach(r=>{r.style.display=r.innerText.toLowerCase().includes(v)?'':'none';});});
</script>
</body>
</html>
"""


def build_page(all_jobs):
    rows = []
    for j in sorted(all_jobs, key=lambda x: (x["company"].lower(), x["title"].lower())):
        co = htmllib.escape(j["company"])
        title = htmllib.escape(j["title"])
        loc = htmllib.escape(j["location"] or "")
        link = j["apply"]
        if link:
            apply_cell = '<a class="apply" href="%s" target="_blank" rel="noopener">Open</a>' % htmllib.escape(link)
        else:
            apply_cell = '<span class="none">no link</span>'
        rows.append(f"    <tr><td class='co'>{co}</td><td>{title}</td><td>{loc}</td><td>{apply_cell}</td></tr>")
    page = PAGE_TEMPLATE
    page = page.replace("{{COUNT}}", str(len(all_jobs)))
    page = page.replace("{{DATE}}", datetime.now().strftime("%d %b %Y, %H:%M UTC"))
    page = page.replace("{{ROWS}}", "\n".join(rows) if rows else "    <tr><td colspan='4' class='none'>No openings found this run.</td></tr>")
    open(PAGE_FILE, "w", encoding="utf-8").write(page)


def main():
    seen = load_seen()
    first_run = seen is None
    if first_run:
        seen = set()

    all_ids, new_jobs, errored = set(), [], []
    all_jobs = {}  # jid -> full record, for the webpage

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch()
    try:
        for c in COMPANIES:
            name = c["name"]
            print("Checking:", name, flush=True)
            try:
                html = fetch(c["url"], browser)
                for j in extract_jobs(name, page_to_text(html)):
                    jid = job_id(name, j)
                    all_ids.add(jid)
                    all_jobs[jid] = {
                        "company": name, "title": j["title"],
                        "location": j["location"],
                        "apply": j["url"] or c["url"],
                    }
                    if jid not in seen:
                        j["company"] = name
                        new_jobs.append(j)
            except Exception as e:
                errored.append(name)
                print("   skipped:", e, flush=True)
            time.sleep(1)
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass

    build_page(list(all_jobs.values()))
    print(f"Done: {len(all_ids)} openings seen, {len(new_jobs)} new, "
          f"{len(errored)} sites errored. Webpage written.", flush=True)

    if first_run:
        subject = f"Job watcher is set up - tracking {len(all_ids)} openings"
        body = ("Setup complete. Your browsable job page is now live, and you'll "
                "also get an email whenever a new job appears.")
    elif not new_jobs:
        subject = f"No new biopharma jobs - {date.today()}"
        body = "Nothing new today. Your job page is still up to date."
    else:
        shown = new_jobs[:EMAIL_CAP]
        lines = [
            f"{j['company']} - {j['title']} "
            f"({j.get('location') or 'location not listed'})\n"
            f"{j.get('url') or 'no direct link'}"
            for j in shown
        ]
        extra = (f"\n\n...and {len(new_jobs) - EMAIL_CAP} more."
                 if len(new_jobs) > EMAIL_CAP else "")
        subject = f"{len(new_jobs)} new biopharma jobs - {date.today()}"
        body = "\n\n".join(lines) + extra

    try:
        send_email(subject, body)
        print("Email sent.", flush=True)
    except Exception as e:
        print("EMAIL FAILED:", repr(e), flush=True)
        traceback.print_exc()
        raise

    save_seen(seen | all_ids)


if __name__ == "__main__":
    main()
