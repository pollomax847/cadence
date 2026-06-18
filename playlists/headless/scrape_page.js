// Usage: node scrape_page.js <url>
// Usage: node scrape_page.js <url>
(async () => {
  const { default: puppeteer } = await import('puppeteer-core');
  const url = process.argv[2];
  if (!url) {
    console.error('Usage: node scrape_page.js <url>');
    process.exit(2);
  }
  const browser = await puppeteer.launch({
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
    executablePath: '/snap/bin/chromium'
  });
  try {
    const page = await browser.newPage();
    await page.setUserAgent('Mozilla/5.0 (compatible; playlists/headless)');
    await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
    const content = await page.content();
    console.log(content);
  } catch (e) {
    console.error('Error fetching page:', e.message || e);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
