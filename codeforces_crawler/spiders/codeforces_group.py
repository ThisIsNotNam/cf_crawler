import scrapy
import re
import os
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import InvalidSessionIdException
    from selenium.common.exceptions import WebDriverException
except ImportError:
    uc = None
    By = None
    InvalidSessionIdException = Exception
    WebDriverException = Exception


class CodeforcesGroupSpider(scrapy.Spider):
    name = "codeforces_group"

    custom_settings = {
        'SPIDER_MIDDLEWARES': {
            'scrapy.spidermiddlewares.httperror.HttpErrorMiddleware': None,
        }
    }

    def __init__(self, *args, **kwargs):
        self.contest_id = kwargs.pop("contest_id", None)
        self.contest_name = kwargs.pop("contest_name", None)
        super().__init__(*args, **kwargs)

        config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        self.username = config["username"]
        self.password = config["password"]
        self.group_id = config["group_id"]
        self.cookies = config.get("cookies", {})
        self.cookies_expiry = config.get("cookies_expiry", {})

        self.login_url = "https://codeforces.com/enter"

    def save_config(self, config):
        config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def _find_local_chrome_binary(self):
        """Find the Chrome (or Chromium) binary, trying multiple strategies."""
        import shutil
        import subprocess

        # 1. Standard Windows install paths
        candidates = [
            os.path.join(os.environ.get("PROGRAMFILES", ""),       "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""),  "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""),       "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", ""),       "Google", "Chrome Beta",  "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", ""),       "Google", "Chrome Dev",   "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", ""),       "Chromium", "Application", "chrome.exe"),
        ]
        for path in candidates:
            if path and os.path.exists(path):
                return path

        # 2. PATH lookup
        for name in ("chrome", "chromium", "chromium-browser", "google-chrome"):
            found = shutil.which(name)
            if found:
                return found

        # 3. Windows registry (HKCU and HKLM)
        try:
            import winreg
            reg_paths = [
                r"SOFTWARE\Google\Chrome\BLBeacon",
                r"SOFTWARE\Wow6432Node\Google\Chrome\BLBeacon",
            ]
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for reg_path in reg_paths:
                    try:
                        with winreg.OpenKey(hive, reg_path) as key:
                            version, _ = winreg.QueryValueEx(key, "version")
                            # version found means Chrome is installed; derive exe path
                            pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
                            exe = os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe")
                            if os.path.exists(exe):
                                return exe
                    except OSError:
                        pass
        except ImportError:
            pass

        # 4. `where chrome` shell command (Windows)
        try:
            result = subprocess.run(
                ["where", "chrome"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                first_line = result.stdout.strip().splitlines()[0]
                if os.path.exists(first_line):
                    return first_line
        except Exception:
            pass

        return None

    def login_with_browser(self):
        if uc is None:
            raise RuntimeError(
                "undetected_chromedriver is required. Install with: pip install undetected-chromedriver"
            )

        local_chrome = self._find_local_chrome_binary()
        if not local_chrome:
            raise RuntimeError(
                "Could not find a Chrome/Chromium binary. "
                "Install Google Chrome, or set CHROME_BIN environment variable to your chrome.exe path."
            )
        # Also honour an explicit override via env var
        local_chrome = os.environ.get("CHROME_BIN", local_chrome)
        self.logger.info(f"Using Chrome binary: {local_chrome}")

        self.logger.info(
            "Opening browser for Cloudflare verification/login. "
            "Complete verification/login manually in Chrome; crawler will continue automatically."
        )

        cf_profile_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".selenium_profile")
        os.makedirs(cf_profile_dir, exist_ok=True)

        options = uc.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument(f"--user-data-dir={os.path.abspath(cf_profile_dir)}")

        # Pass browser_executable_path directly so uc never runs its own
        # find_chrome_executable(), which returns None on non-standard installs.
        driver = uc.Chrome(options=options, headless=False, browser_executable_path=local_chrome, version_main=147)
        try:
            driver.get(self.login_url)

            # Give the page time to load / trigger Cloudflare challenge
            time.sleep(3)

            auth_timeout_seconds = 600
            poll_seconds = 2
            deadline = time.time() + auth_timeout_seconds
            auto_filled_once = False

            while time.time() < deadline:
                try:
                    # Don't break if Cloudflare challenge is still active
                    page_title = driver.title
                    if "just a moment" in page_title.lower():
                        time.sleep(poll_seconds)
                        continue

                    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
                    if cookies.get("X-User-Sha1"):
                        # Also confirm we're actually on Codeforces, not a redirect
                        if "codeforces.com" in driver.current_url:
                            break

                    if not auto_filled_once:
                        handle_inputs = driver.find_elements(By.NAME, "handleOrEmail")
                        password_inputs = driver.find_elements(By.NAME, "password")
                        if handle_inputs and password_inputs:
                            handle_inputs[0].clear()
                            handle_inputs[0].send_keys(self.username)
                            password_inputs[0].clear()
                            password_inputs[0].send_keys(self.password)

                            submit_buttons = driver.find_elements(
                                By.CSS_SELECTOR,
                                'input[type="submit"], button[type="submit"]',
                            )
                            if submit_buttons:
                                submit_buttons[0].click()
                            else:
                                password_inputs[0].submit()
                            auto_filled_once = True
                            self.logger.info("Submitted login form; waiting for Cloudflare/auth completion...")

                except InvalidSessionIdException as exc:
                    raise RuntimeError(
                        "Browser session closed before authentication finished. "
                        "Keep the Chrome window open until login completes."
                    ) from exc
                except WebDriverException:
                    # Transient DOM/devtools glitches during Cloudflare redirects.
                    pass

                time.sleep(poll_seconds)
            else:
                raise RuntimeError(
                    "Timed out waiting for successful authentication. "
                    "Please complete Cloudflare verification/login within 10 minutes."
                )

            raw_cookies = driver.get_cookies()
            if not raw_cookies:
                raise RuntimeError("Login succeeded but no cookies were returned by the browser.")

            cookies = {c["name"]: c["value"] for c in raw_cookies}
            cookies_expiry = {c["name"]: c["expiry"] for c in raw_cookies if "expiry" in c}

            config = {
                "username": self.username,
                "password": self.password,
                "group_id": self.group_id,
                "cookies": cookies,
                "cookies_expiry": cookies_expiry,
            }
            self.save_config(config)
            self.cookies = cookies
            self.cookies_expiry = cookies_expiry
            self.logger.info("Saved fresh Codeforces cookies to config.json")
        finally:
            time.sleep(1)
            driver.quit()

    # Real Chrome headers — must match what the browser sent when cf_clearance was issued.
    CHROME_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    def _make_request(self, url, callback, cb_kwargs=None, meta=None, dont_filter=False):
        """Wrapper that always attaches cookies + Chrome headers."""
        return scrapy.Request(
            url=url,
            callback=callback,
            cb_kwargs=cb_kwargs or {},
            cookies=self.cookies,
            headers=self.CHROME_HEADERS,
            meta=meta or {},
            dont_filter=dont_filter,
        )


    def _cookies_are_valid(self):
        """Return True only if cf_clearance exists and hasn't expired yet."""
        if not self.cookies or "cf_clearance" not in self.cookies:
            return False
        expiry = self.cookies_expiry.get("cf_clearance")
        if expiry and time.time() > expiry - 60:  # 60-second safety margin
            self.logger.info("cf_clearance cookie has expired — need fresh login.")
            return False
        return True

    def start_requests(self):
        if not self._cookies_are_valid():
            self.login_with_browser()

        if self.contest_id:
            contest_url = f"https://codeforces.com/gym/{self.contest_id}"
            yield self._make_request(
                contest_url,
                self.parse_contest,
                cb_kwargs={
                    "contest_name": self.contest_name or None,
                    "contest_id": self.contest_id,
                },
                dont_filter=True,
            )
            return

        contests_url = f"https://codeforces.com/group/{self.group_id}/contests"
        yield self._make_request(contests_url, self.parse_contests, dont_filter=True)

    def get_contest_folder_name(self, contest_name, contest_id):
        contest_name = (contest_name or f"Contest {contest_id}").strip()
        contest_name = re.sub(r'[\\/:*?"<>|]', '', contest_name)
        return f"{contest_name} ({contest_id})"

    def get_contest_folder_path(self, contest_name, contest_id):
        if self.contest_id:
            # Single-contest mode: save directly to output/<name> (no group subfolder)
            base = "output"
        else:
            # Group-crawl mode: save to output/<group_id>/<name>
            base = os.path.join("output", self.group_id)
        return os.path.join(base, self.get_contest_folder_name(contest_name, contest_id))

    def parse_contests(self, response):
        # --- DEBUG: dump selectors once so we can fix them if needed ---
        from bs4 import BeautifulSoup as _BS
        _soup = _BS(response.text, "html.parser")
        _tables = _soup.find_all("table")
        self.logger.info(f"DEBUG: found {len(_tables)} <table> elements")
        for _t in _tables[:5]:
            self.logger.info(f"DEBUG table class={_t.get('class')} id={_t.get('id')} | first 120 chars: {str(_t)[:120]}")
        _rows = response.css(".contests-table tr")
        self.logger.info(f"DEBUG: .contests-table tr matched {len(_rows)} rows")
        # save full HTML for inspection
        import os as _os
        _debug_path = _os.path.join(_os.path.dirname(__file__), "..", "..", "debug_contests.html")
        with open(_os.path.abspath(_debug_path), "w", encoding="utf-8") as _f:
            _f.write(response.text)
        self.logger.info(f"DEBUG: full HTML saved to debug_contests.html")
        # ----------------------------------------------------------------

        if response.status == 403:
            if response.meta.get("auth_retry_done"):
                self.logger.error("Still getting 403 after browser login. Check credentials and solve Cloudflare fully.")
                return

            self.logger.warning(f"Got 403 Forbidden on {response.url}. Cookies may be expired. Re-authenticating...")
            self.login_with_browser()
            yield self._make_request(
                response.url, self.parse_contests,
                meta={"auth_retry_done": True}, dont_filter=True,
            )
            return

        if "Codeforces" not in response.text and "login" in response.url.lower():
            self.logger.error("Failed to access group contests page. Cookies might be invalid or expired.")
            return

        for row in response.css("div.datatable table tr[data-contestid]"):
            contest_id = row.attrib.get("data-contestid")
            # Name is a bare text node in the first td, before the <br>
            first_td = row.css("td:first-child")
            contest_name = first_td.xpath("text()").get("").strip()
            if not contest_name:
                contest_name = f"Contest {contest_id}"

            # "Enter »" link is the contest URL
            enter_link = row.css('a[href*="/contest/"]::attr(href)').get()
            if not enter_link:
                continue
            contest_url = urljoin(response.url, enter_link)

            contest_output_folder = self.get_contest_folder_path(contest_name, contest_id)
            if os.path.exists(contest_output_folder):
                self.logger.info(f"Skipping already crawled contest (ID: {contest_id}, Name: {contest_name})")
                continue

            self.logger.info(f"Crawling new contest (ID: {contest_id}, Name: {contest_name})")
            yield self._make_request(
                contest_url, self.parse_contest,
                cb_kwargs={"contest_name": contest_name, "contest_id": contest_id},
            )

    def parse_contest(self, response, contest_name=None, contest_id=None):
        if response.status == 403:
            if response.meta.get("auth_retry_done"):
                self.logger.error("Still getting 403 on contest page after browser login.")
                return

            self.logger.warning(f"Got 403 on contest page {response.url}. Re-authenticating via browser...")
            self.login_with_browser()
            yield self._make_request(
                response.url, self.parse_contest,
                cb_kwargs={"contest_name": contest_name, "contest_id": contest_id},
                meta={"auth_retry_done": True}, dont_filter=True,
            )
            return

        contest_name = contest_name or self.extract_contest_name(response) or f"Contest {contest_id or 'unknown'}"
        contest_id = contest_id or self.contest_id

        for problem in response.css(".problems tr"):
            link = problem.css("a::attr(href)").get()
            if link:
                problem_url = urljoin(response.url, link)
                yield self._make_request(
                    problem_url, self.parse_problem,
                    cb_kwargs={"contest_name": contest_name, "contest_id": contest_id},
                )

    def extract_contest_name(self, response):
        title = response.css("title::text").get()
        if not title:
            return None
        title = title.strip()
        title = re.sub(r"\s*-\s*Codeforces$", "", title, flags=re.IGNORECASE)
        return title

    def parse_problem(self, response, contest_name, contest_id):
        soup = BeautifulSoup(response.text, "html.parser")
        problem_div = soup.select_one(".problemindexholder")
        if not problem_div:
            self.logger.warning(f"No problem div found for {response.url}")
            return

        title_el = problem_div.select_one(".title")
        title = title_el.get_text(strip=True) if title_el else "Unknown Problem"

        time_limit = ""
        memory_limit = ""
        info_div = problem_div.select_one(".problem-statement .header")
        if info_div:
            for p_tag in info_div.find_all('p', recursive=False):
                text = p_tag.get_text(strip=True)
                if "time limit" in text.lower():
                    time_limit = text.replace("time limit per test", "Time limit:").strip()
                elif "memory limit" in text.lower():
                    memory_limit = text.replace("memory limit per test", "Memory limit:").strip()

        markdown = self.html_to_markdown(problem_div, title, time_limit, memory_limit)

        contest_output_folder = self.get_contest_folder_path(contest_name, contest_id)
        os.makedirs(contest_output_folder, exist_ok=True)

        file_name = self.sanitize_markdown_filename(title)
        file_path = os.path.join(contest_output_folder, file_name)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        self.logger.info(f"Saved: {file_path}")

    def sanitize_markdown_filename(self, title):
        """Build a Windows-safe markdown filename from a problem title."""
        file_name = re.sub(r'[\\/:*?"<>|]', '', f"{title}.md")
        file_name = file_name.strip().rstrip(". ")
        if file_name.lower() == ".md":
            return "problem.md"
        return file_name

    # --- Utility ---

    def html_to_markdown(self, problem_div_soup, title, time_limit, memory_limit):
        out = []
        out.append(f"# {title}\n")

        if time_limit or memory_limit:
            if time_limit:
                out.append(f"**{time_limit}**")
            if memory_limit:
                out.append(f"**{memory_limit}**\n")
            out.append("---\n")

        problem_statement_div = problem_div_soup.select_one(".problem-statement")
        if problem_statement_div:
            if problem_statement_div.select_one(".header"):
                problem_statement_div.select_one(".header").decompose()
            problem_content_div = problem_statement_div.find('div', class_=False, recursive=False)
            if problem_content_div:
                out.append("## Problem\n")
                out.append(self.convert_element_to_markdown(problem_content_div))
                out.append("\n")

        input_spec = problem_div_soup.select_one(".input-specification")
        if input_spec:
            out.append("## Input\n")
            out.append(self._strip_leading_section_label(self.convert_element_to_markdown(input_spec), "Input"))
            out.append("\n")

        output_spec = problem_div_soup.select_one(".output-specification")
        if output_spec:
            out.append("## Output\n")
            out.append(self._strip_leading_section_label(self.convert_element_to_markdown(output_spec), "Output"))
            out.append("\n")

        sample_tests = problem_div_soup.select(".sample-tests .sample-test")
        if sample_tests:
            out.append("## Example\n")
            for example in sample_tests:
                input_block = example.select_one(".input pre")
                output_block = example.select_one(".output pre")
                if input_block:
                    out.append("### Input\n")
                    input_text = input_block.get_text("\n", strip=False).rstrip("\n")
                    out.append("```text\n" + input_text + "\n```\n")
                if output_block:
                    out.append("### Output\n")
                    output_text = output_block.get_text("\n", strip=False).rstrip("\n")
                    out.append("```text\n" + output_text + "\n```\n")
            out.append("\n")

        note = problem_div_soup.select_one(".note")
        if note:
            out.append("## Note\n")
            out.append(self._strip_leading_section_label(self.convert_element_to_markdown(note), "Note"))
            out.append("\n")

        return "\n".join(out)

    def _strip_leading_section_label(self, text, label):
        """Remove duplicated section title if source html already starts with it."""
        pattern = rf'^\s*{re.escape(label)}\s*\n+'
        return re.sub(pattern, '', text, flags=re.IGNORECASE)

    def _tex_span_to_latex(self, tex_span):
        """Convert Codeforces tex-span HTML into inline LaTeX text."""
        soup = BeautifulSoup(str(tex_span), "html.parser")
        root = soup.find(class_='tex-span') or soup

        # Some statements use class names instead of semantic tags.
        for upper in root.find_all(class_='upper-index'):
            upper.replace_with(f"^{{{upper.get_text(strip=True)}}}")
        for lower in root.find_all(class_='lower-index'):
            lower.replace_with(f"_{{{lower.get_text(strip=True)}}}")

        for sub_tag in root.find_all('sub'):
            sub_tag.replace_with(f"_{{{sub_tag.get_text(strip=True)}}}")

        for sup_tag in root.find_all('sup'):
            sup_tag.replace_with(f"^{{{sup_tag.get_text(strip=True)}}}")

        # Inside tex-span, <i> usually wraps math tokens; keep token text only.
        for i_tag in root.find_all('i'):
            i_tag.replace_with(i_tag.get_text(strip=True))

        text = root.get_text("", strip=True)
        # Codeforces uses &thinsp; (\u2009) extensively in math blocks. 
        # Some Markdown math renderers (like KaTeX) will fail to parse \u2009.
        # We replace them with a standard space (or let LaTeX handle the spacing itself).
        text = text.replace('\u2009', ' ').replace('\xa0', ' ')
        return text

    def convert_element_to_markdown(self, element):
        for img_tag in element.find_all('img'):
            alt = img_tag.get('alt', '')
            src = img_tag.get('src', '')
            if src.startswith("//"):
                src = "https:" + src
            img_tag.replace_with(BeautifulSoup(f"![{alt}]({src})", 'html.parser'))

        for tex_span in element.find_all(class_='tex-span'):
            tex_span.replace_with(f"${self._tex_span_to_latex(tex_span)}$")

        for i_tag in element.find_all('i'):
            i_tag.replace_with(f"${i_tag.get_text(strip=True)}$")

        for upper in element.find_all(class_='upper-index'):
            upper.replace_with(f"$^{{{upper.get_text(strip=True)}}}$")
            
        for lower in element.find_all(class_='lower-index'):
            lower.replace_with(f"$_{{{lower.get_text(strip=True)}}}$")

        for sub_tag in element.find_all('sub'):
            # If sub is still outside tex-span, wrap its replacement in $ so it renders as math
            sub_tag.replace_with(f"$_{{{sub_tag.get_text(strip=True)}}}$")

        for sup_tag in element.find_all('sup'):
            # If sup is still outside tex-span, wrap its replacement in $ so it renders as math
            sup_tag.replace_with(f"$^{{{sup_tag.get_text(strip=True)}}}$")

        for p_tag in element.find_all('p'):
            p_tag.insert_after('\n\n')

        for ul_tag in element.find_all('ul'):
            for li_tag in ul_tag.find_all('li'):
                li_tag.insert_before('* ')
                li_tag.insert_after('\n')
            ul_tag.insert_after('\n')

        for strong_tag in element.find_all('strong'):
            strong_tag.insert_before('**')
            strong_tag.insert_after('**')

        for pre_tag in element.find_all('pre'):
            pre_text = pre_tag.get_text("\n", strip=False).rstrip("\n")
            pre_tag.replace_with(BeautifulSoup(f"\n```text\n{pre_text}\n```\n", 'html.parser'))

        text = element.get_text(separator='\n', strip=True)
        # Collapse newlines that appear immediately before/after inline math
        # so "$n$\nhãy" becomes "$n$ hãy" instead of each token on its own line.
        text = re.sub(r'\n+(\$[^$\n]+\$)\n+', r' \1 ', text)
        text = re.sub(r'\n+(\$[^$\n]+\$)', r' \1', text)
        text = re.sub(r'(\$[^$\n]+\$)\n+', r'\1 ', text)
        # Collapse runs of 3+ newlines down to 2 (one blank line)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text
