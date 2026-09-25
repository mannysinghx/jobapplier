# User guide

## 1. Profile
Enter your name, email, phone, location, time zone, LinkedIn URL and portfolio links. These are the only values used for identity questions on forms.

## 2. Resume & LinkedIn
- **Folder**: enter the folder path and tick the consent box. The app lists PDF/DOCX files at the top level of that folder only and reads them without modifying anything. You can revoke consent at any time.
- **Upload**: alternatively, upload a PDF/DOCX (max 10 MB).
- **LinkedIn**: in LinkedIn, go to Settings → Data privacy → *Get a copy of your data*, then upload the ZIP here. The app never asks for your LinkedIn password.

Each new file becomes a new **version**. Originals are kept and never changed.

## 3. Review facts (important)
Extracted facts (roles, achievements, education, skills, certifications, projects) start as **PENDING**. For each one:
- Check it against the snippet shown (its provenance). Fix wrongly parsed titles or employers with **Edit**.
- **Approve** what is true. **Reject** what is wrong.
- **Conflicts** (for example, resume says "Senior Engineer" but LinkedIn says "Staff Engineer") must be resolved first. Pick the correct fact. The app never silently chooses the more favorable claim.

Only **APPROVED** facts appear in matching evidence, resumes and cover letters.

## 4. Standard answers
Pre-approve reusable answers (salary expectation, start date, relocation, how you heard). **Sensitive answers** (work authorization, sponsorship, criminal history, disability, veteran status, demographics) are used only if you enter and approve them here. They are stored encrypted and masked on screen.

## 5. Preferences
Titles, keywords, seniority, geographies, remote/hybrid/on-site, employment types, salary floor, industries, excluded employers and terms, minimum score, polling interval, daily limit and score weights.

## 6. Sources & boards
Enable a permitted source, then add employer **boards**. A board token is the company slug in the URL:
`boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`, `jobs.ashbyhq.com/<token>`.
**Poll now** fetches immediately. Otherwise the worker polls on your interval. Connector health shows failures and backoff.

## 6b. LinkedIn, Indeed, ZipRecruiter, Dice, Ladders
None of these five sites permits a third-party tool to scrape listings or to apply for you. Each term is quoted in [SOURCE_REGISTRY.md](SOURCE_REGISTRY.md). They are supported through permitted routes:
- **Dice**: Sources → Search (Dice) → add a saved search (keyword, location, remote/hybrid/on-site, posted within). It polls Dice's official job-search server on your schedule. Only a summary comes back; press **Fetch full description from Dice** on a job you care about.
- **Job-alert emails (all five)**: turn on job alerts on the sites. Then Import → upload the alert emails:
  - Gmail: Google Takeout (.mbox), or open an email → ⋮ → Download message (.eml)
  - Apple Mail: Mailbox → Export Mailbox (.mbox), or drag messages to Finder (.eml)
  - Outlook: Save as .eml

  Only emails from those five sites are read.
- **LinkedIn data export**: the same ZIP you upload for your profile also imports your **Saved Jobs** and **Job Applications**. Past applications are recorded, so you're warned before applying to the same role again.
- **Manual add**: paste a listing you're looking at (URL, title, company, optionally the description). If the link points to a Greenhouse/Lever/Ashby board, you're offered to follow that employer's board directly.

Imported jobs usually have only a summary. Paste the full description from your browser for a fuller match. **Applying is always done by you on the site**, using the packet.

## 7. Applications
States: DISCOVERED → MATCHED → PREPARED → NEEDS_REVIEW/APPROVED → SUBMITTED → CONFIRMED (plus REJECTED_BY_USER, BLOCKED_BY_POLICY, EXPIRED, DUPLICATE, FAILED, FOLLOW_UP).

Open an application to see:
- the **score breakdown**, evidence (with fact IDs), unmet requirements and exclusions;
- the **packet**: tailored resume (DOCX download), cover letter, and answers with status colors;
- the **auto-submit checklist**: every policy check, pass or fail, with reasons.

Then:
1. **Prepare** builds the packet. Unsupported or sensitive questions put it in NEEDS_REVIEW. Map questions to your approved answers, or choose *Approve — I'll answer on the employer site*.
2. **Approve**.
3. **Handoff**: open the apply link, submit using the packet, then click **Record manual submission** (optionally with a confirmation number). The packet contents are saved with the record.

## 8. Controls & privacy
- **Pause** stops polling and submissions immediately. For emergencies, see [RUNBOOK.md](RUNBOOK.md).
- **Export** downloads everything about you as a ZIP. **Delete all** removes it (type `DELETE MY DATA`).
- **Retention** sets how long expired jobs, rejected applications and logs are kept.
- **Audit log** records every approval, generation, decision and submission. **Verify chain** checks it has not been tampered with.
