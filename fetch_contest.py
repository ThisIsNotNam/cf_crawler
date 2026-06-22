"""
Usage:
  python fetch_contest.py                     # crawl all contests in the group
  python fetch_contest.py 667920              # crawl a single contest by ID
  python fetch_contest.py 667920 "My Contest" # crawl with a custom name
"""
import argparse
import os
import sys

os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "codeforces_crawler.settings")

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from codeforces_crawler.spiders.codeforces_group import CodeforcesGroupSpider


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch problems from Codeforces group contests.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "contest_id",
        nargs="?",
        help="Contest ID to fetch (omit to crawl ALL contests in the group)",
    )
    parser.add_argument(
        "contest_name",
        nargs="?",
        help="Optional display name for the contest folder",
    )
    args = parser.parse_args()

    process = CrawlerProcess(get_project_settings())

    kwargs = {}
    if args.contest_id:
        kwargs["contest_id"] = args.contest_id
    if args.contest_name:
        kwargs["contest_name"] = args.contest_name

    process.crawl(CodeforcesGroupSpider, **kwargs)
    process.start()


if __name__ == "__main__":
    main()
