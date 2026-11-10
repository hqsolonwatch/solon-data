# Solon Watch data pipeline

This program collects the public records Solon Watch needs and saves them as files your website and app can read. Once it's set up on GitHub, it runs every morning on its own. You don't need to do anything by hand.

## What it collects

| What | Where it comes from | How often |
|---|---|---|
| Every senator and House member, with phone numbers, offices and contact forms | congress-legislators project (built from official House, Senate and Bioguide records) | Daily |
| Every roll-call vote and how each member voted | House Clerk (clerk.house.gov) and Senate (senate.gov) | Daily |
| Bill titles and plain-English summaries | Congress.gov (summaries by the Congressional Research Service) | Daily |
| Bills coming up for a vote | House floor schedule (docs.house.gov) and Senate floor schedule (senate.gov) | Daily |
| Campaign money: totals, small vs. large donors, PAC money, where donors live, where they work | Federal Election Commission | Daily (each member refreshed weekly) |
| Every candidate running for Congress this cycle | Federal Election Commission | Daily |
| Groups that lobbied on each bill, and what their PACs gave each member | Lobbying Disclosure Act reports (lda.gov) and FEC | Daily (each bill refreshed weekly) |
| District maps, compactness scores, and the counties in each district | U.S. Census Bureau | Monthly |
| Area-by-area election results | OpenElections, or hand-checked files in `overrides/district_results` | With district maps |
| The example data for the pre-launch page | All of the above | Daily |

`data/README.md` lists every file the program creates and what's in it.

## Set it up on GitHub (about 20 minutes, one time)

### 1. Get the keys

1. **Data.gov key (required).** Go to https://api.data.gov/signup, enter your name and email, and copy the key it shows you. One key works for both the FEC and Congress.gov. Use a new key, not the one pasted into an earlier chat.
2. **Lobbying key (recommended).** Go to https://lda.gov, create an account, and request an API key. Without it, lobbying data still works but fills in much more slowly.

### 2. Make a GitHub account and a repository

1. If you don't have one, create a free account at https://github.com.
2. Click the **+** in the top right, then **New repository**.
3. Name it `solon-data`. Choose **Public**. Everything in it is public record, and free GitHub Pages hosting (step 6) needs a public repository.
4. Click **Create repository**.

### 3. Upload the files

1. Unzip `solon-data.zip` on your Mac.
2. Open the unzipped `solon-data` folder in Finder and press **Command + Shift + period**. This shows hidden folders. You should now see a faded `.github` folder. It holds the schedule, so it has to be uploaded too.
3. On your new repository page, click **uploading an existing file**.
4. Select everything inside the `solon-data` folder, including `.github`, and drag it onto the page. Drag the contents, not the folder itself.
5. Wait for the upload to finish, then click **Commit changes**.
6. Check that `.github` appears in the file list. If it doesn't, see "If the .github folder didn't upload" at the end.

### 4. Add your keys

1. In the repository, click **Settings**, then **Secrets and variables**, then **Actions**.
2. Click **New repository secret**. Name: `API_DATA_GOV_KEY`. Secret: your data.gov key. Click **Add secret**.
3. If you have a lobbying key, add another secret named `LDA_API_KEY`.

Secrets are hidden. Nobody, including people viewing the repository, can see them.

### 5. Let the program save its results

1. In **Settings**, click **Actions**, then **General**.
2. Under **Workflow permissions**, choose **Read and write permissions** and click **Save**.

### 6. Turn on free hosting for the files

1. In **Settings**, click **Pages**.
2. Under **Build and deployment**, set **Source** to **Deploy from a branch**, the branch to **main**, and the folder to **/ (root)**. Click **Save**.

Your files will then be online at addresses like:
- `https://YOUR-USERNAME.github.io/solon-data/data/members.json`
- `https://YOUR-USERNAME.github.io/solon-data/site/index.html` (the pre-launch page with fresh data)

### 7. Run it the first time

1. Click the **Actions** tab. If GitHub asks, click the button to enable workflows.
2. Click **Daily data update** on the left, then **Run workflow**, then the green **Run workflow** button.
3. Wait for the green check. The first run can take up to a couple of hours, because it collects every vote since January 2025 and builds the district maps. After that, each run takes a few minutes.

The data the pre-launch page needs is collected first, so the page at `site/index.html` should be complete after this one run.

From then on, the daily update runs every morning at about 5:20 a.m. Central, and the district maps refresh on the 3rd of each month.

## Checking that it's working

- **Actions tab:** a green check means the run finished. A red X means a step had a problem.
- **`data/status_daily.json`:** lists each step, whether it worked, and any error. If something goes wrong, send this file, or the log from the red run, to whoever is helping you (including Claude).
- **Large first runs:** the FEC allows about 1,000 requests an hour, so detailed money data for all 535 members fills in over the first few days. That's expected.

## Settings you might change

All settings are in `config.yaml`. To edit it on GitHub, open the file, click the pencil icon, make the change, and click **Commit changes**.

- **On January 3, 2027, when the new Congress starts:** set `congress: 120`, `election_cycle: 2028`, and `house_years: [2027, 2028]`. Once the Census Bureau publishes 120th Congress district files, set `boundary_year` to that file's year, usually 2026 or 2027.
- **`landing: house_members`:** which two House members the pre-launch page features.
- **`landing: districts`:** which districts the pre-launch page features, or `auto` for the least compact district held by each party.
- **`landing: money_breakdown`:** `states` (where donors live) or `employers` (where donors work).

## Hand-checked corrections

The `overrides` folder lets you replace automatic results with checked ones:

- `summaries.yaml`: your own one-sentence bill summaries.
- `pac_map.yaml`: which PAC belongs to a lobbying group, when the automatic match misses or gets it wrong.
- `group_names.yaml`: how a lobbying group's name is shown.
- `district_results/`: official area-by-area results for a district, for states OpenElections doesn't cover.

## If the .github folder didn't upload

1. In the repository, click **Add file**, then **Create new file**.
2. In the name box, type `.github/workflows/daily.yml`. Typing the slashes creates the folders.
3. Open `.github/workflows/daily.yml` from the unzipped folder in TextEdit, copy everything, and paste it into the big box.
4. Click **Commit changes**.
5. Repeat for `.github/workflows/districts.yml`.

## For developers

```bash
pip install -r requirements.txt
export API_DATA_GOV_KEY=...      # and optionally LDA_API_KEY
python -m pipeline.run districts  # district maps (monthly)
python -m pipeline.run daily      # everything else (daily)
pytest -q                         # offline tests; see tests/test_pipeline.py for optional real-file tests
```

The code is in `pipeline/`. Each source has its own module (`members.py`, `votes.py`, `bills.py`, `upcoming.py`, `fec.py`, `lobbying.py`, `districts.py`, `results.py`), and `landing.py` builds the pre-launch page data. Requests are rate-limited and budgeted per source (see `run.py`), and a failed step doesn't stop the others.
