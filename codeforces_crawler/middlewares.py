from scrapy import signals
from scrapy.http import HtmlResponse

try:
    from curl_cffi import requests as cf_requests
except ImportError:
    cf_requests = None


class CloudflareMiddleware:
    """
    Routes every Scrapy request through curl_cffi impersonating Chrome.
    This matches Chrome's TLS fingerprint (JA3 hash) so Cloudflare accepts
    the request even when cf_clearance cookies are present.
    """

    IMPERSONATE = "chrome110"

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def process_request(self, request, spider):
        if cf_requests is None:
            spider.logger.warning(
                "curl_cffi not installed — Cloudflare will likely block requests. "
                "Install with: pip install curl_cffi"
            )
            return None

        cookies = {}
        if hasattr(spider, "cookies") and spider.cookies:
            cookies = dict(spider.cookies)
        if request.cookies:
            cookies.update(dict(request.cookies))

        headers = {k.decode(): v[0].decode() for k, v in request.headers.items()}

        try:
            resp = cf_requests.get(
                request.url,
                headers=headers,
                cookies=cookies,
                impersonate=self.IMPERSONATE,
                timeout=30,
                allow_redirects=True,
            )
        except Exception as e:
            spider.logger.error(f"curl_cffi request failed for {request.url}: {e}")
            raise

        return HtmlResponse(
            url=resp.url,
            status=resp.status_code,
            body=resp.content,
            encoding="utf-8",
            request=request,
        )
