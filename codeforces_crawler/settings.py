BOT_NAME = "codeforces_crawler"

SPIDER_MODULES = ["codeforces_crawler.spiders"]
NEWSPIDER_MODULE = "codeforces_crawler.spiders"

ADDONS = {}

ROBOTSTXT_OBEY = False
DOWNLOAD_DELAY = 1.0
COOKIES_ENABLED = True
TWISTED_REACTOR = 'twisted.internet.selectreactor.SelectReactor'
HTTPERROR_ALLOWED_CODES = []
FEED_EXPORT_ENCODING = "utf-8"

DOWNLOADER_MIDDLEWARES = {
    'codeforces_crawler.middlewares.CloudflareMiddleware': 543,
}
