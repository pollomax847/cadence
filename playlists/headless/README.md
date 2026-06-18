Headless scraper helper (puppeteer)

This helper fetches a URL using Puppeteer and prints the full page HTML to stdout.

Install (requires Node.js and npm):

```bash
# optional: create a node v16+ environment
cd playlists/headless
npm install
```

Notes:
- Puppeteer downloads a compatible Chromium by default. If you prefer system Chromium, set PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=1 and ensure Chromium is installed.
- On Linux, you may need additional dependencies (libnss3, libatk1.0-0, libcups2, libxss1, libasound2, libx11-xcb1).

Example usage from the Python script:

```bash
# fetch dynamic page and save to cache file
node playlists/headless/scrape_page.js "https://lescharts.com/year.asp?year=1995" > playlists/cache/lescharts_1995_node.html
```

You can then re-run the Python script which will read cached HTML files before attempting network fetches.
