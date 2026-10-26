# Job Monitor

Checks company job boards (Greenhouse, Lever, Ashby) every 15 minutes and sends an alert to your phone the moment a matching content leadership role appears. Tap the alert to open the posting.

## Setup (about 15 minutes)

### 1. Phone alerts
1. Install the **ntfy** app (iOS or Android).
2. Tap **+** to subscribe to a topic. Invent a long, random name, for example `matt-content-roles-x7k2q9`. Anyone who knows the name can read it, so don't use something guessable.
3. Allow notifications for ntfy. On iPhone, also allow Time Sensitive notifications so alerts break through Focus modes.

### 2. GitHub repository
1. Sign in at github.com (create a free account if needed).
2. Click **New repository**. Name it `job-monitor`. Choose **Public**.
   Public repos get unlimited free Actions minutes. Private repos get 2,000 free minutes a month, and checking every 15 minutes uses about 2,900. Your company list will be visible; your alert topic stays secret.
3. On the empty repo page, click **uploading an existing file**. Drag in `monitor.py`, `companies.yaml`, and `README.md`. Click **Commit changes**.
4. Click **Add file > Create new file**. In the name box, type exactly:
   `.github/workflows/monitor.yml`
   Paste in the contents of `monitor.yml`. Click **Commit changes**.

### 3. Connect the alerts
1. In the repo, go to **Settings > Secrets and variables > Actions**.
2. Click **New repository secret**. Name: `NTFY_TOPIC`. Value: your topic name from step 1. Save.
3. Go to **Settings > Actions > General**, scroll to **Workflow permissions**, select **Read and write permissions**, Save.

### 4. First run
1. Go to the **Actions** tab. If prompted, enable workflows.
2. Click **Job monitor > Run workflow**.
3. Within a minute your phone gets a "Job monitor is watching" alert listing roles that are already open. That confirms everything works.
4. Click into the run and open the **Check job boards** step. Any company listed under **FAILED** has the wrong slug. Fix or delete it in `companies.yaml`.

From then on it runs on its own. You only hear from it when something new posts.

## Day to day
- **Add a company:** edit `companies.yaml` in GitHub (pencil icon), add the slug under the right board type, commit. Its current openings show up once in a digest, then only new ones alert.
- **Widen or narrow titles:** edit `title_include` / `title_exclude`. Several extra patterns are already there, just remove the `#`.
- **Pause:** Actions tab > Job monitor > the `...` menu > Disable workflow.

## Limits
- GitHub sometimes runs scheduled jobs 5 to 20 minutes late at busy times. Still well ahead of LinkedIn and Indeed.
- Only covers companies on Greenhouse, Lever, or Ashby. Workday and custom career sites (Thomson Reuters, Lockheed, AHA) are not covered.
- "Remote" roles based outside the US can slip through. Tighten `location_allow` if that gets noisy.
