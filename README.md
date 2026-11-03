# SL Events Explorer

Free static community site for upcoming Second Life events. Independent from Linden Lab.

## Publish on GitHub Pages

1. Create a **public** GitHub repository named `sl-events-explorer`.
2. Upload **all contents** of this directory, including `.github/workflows/update.yml` (the dot-folder may be hidden by some file managers). You can also push using Git.
3. Open repository **Settings > Pages**. Under **Build and deployment**, select **Deploy from a branch**, branch `main`, folder `/ (root)`, and Save.
4. Visit `https://YOUR-USERNAME.github.io/sl-events-explorer/` after deployment.
5. In **Actions > Update Second Life events**, run **Run workflow** to test the fetch. Scheduled runs occur approximately hourly, subject to GitHub delays and limits.
6. If the action cannot push, review repository **Settings > Actions > General > Workflow permissions** and enable read/write permissions for workflows if your account allows it.

## Important limitation

The importer deliberately rejects calendar entries without an explicit machine-readable timezone-aware date. It has **not** been tested against the live page because network access to that host was unavailable in the development environment. It may therefore return zero events until the page-specific parser is validated. This is not a complete, production-verified scraper. The site never substitutes fabricated events. Confirm the site's automated access complies with Second Life's terms and robots guidance before enabling regular collection.

`data/events.json` is the public feed. An adapter for an approved API or export can replace `sync_events.py` without changing the site. Dates are shown in the visitor's local timezone. The music category uses keywords and is only approximate.
