"""Log in to the Universitas Hasanuddin EPrints repository."""

from __future__ import annotations

import argparse
from datetime import date
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

LOGIN_URL = (
    "https://repository.unhas.ac.id/cgi/users/login"
    "?target=https%3A%2F%2Frepository.unhas.ac.id%2Fcgi%2Fusers%2Fhome"
)
ITEMS_URL = "https://repository.unhas.ac.id/cgi/users/home?screen=Items"
RTA_LOGIN_URL = "https://regtugasakhir.unhas.ac.id/admin/auth/login"
RTA_ITEMS_URL = "https://regtugasakhir.unhas.ac.id/admin/dashboard/tugas_akhirs"
PAGE_TIMEOUT_MS = 30_000
NAVIGATION_TIMEOUT_MS = 60_000


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is required. "
            "Copy .env.example to .env and fill in your credentials."
        )
    return value


@dataclass(frozen=True)
class RtaRecord:
    nim: str
    name: str
    title: str
    abstract: str
    prodi: str
    faculty: str
    prodi_format: str
    supervisors: tuple[tuple[str, str], ...]


def login(page: Page, username: str, password: str) -> None:
    """Log in and navigate to the user's Items page."""
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    if page.locator("#login_username").count() == 0:
        body = " ".join(page.locator("body").inner_text().split())
        if "blocked" in body.casefold() or "unauthorized" in body.casefold():
            raise RuntimeError(
                "EPrints blocked the login page before the form loaded. "
                "Wait and retry, or use a different network/IP."
            )
        raise RuntimeError("EPrints login form did not load.")
    page.locator("#login_username").fill(username)
    page.locator("#login_password").fill(password)
    page.locator('input[name="_action_login"]').click()

    page.wait_for_load_state("domcontentloaded")
    if page.get_by_role("heading", name="Login").is_visible():
        raise RuntimeError(
            "Login failed: the login form is still visible. "
            "Check the username and password."
        )

    page.goto(ITEMS_URL, wait_until="domcontentloaded")
    current_url = urlparse(page.url)
    if current_url.path != "/cgi/users/home" or current_url.query != "screen=Items":
        raise RuntimeError(
            f"Could not open the Items page; browser ended at {page.url}"
        )


def require_saved_login(page: Page) -> None:
    """Verify that a persisted EPrints session is still authenticated."""
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
    page.goto(ITEMS_URL, wait_until="domcontentloaded")
    body = " ".join(page.locator("body").inner_text().split())
    if "blocked" in body.casefold() or "unauthorized" in body.casefold():
        raise RuntimeError(
            "EPrints blocked this browser request before the Items page loaded. "
            "The saved login cannot be verified from this network; retry later "
            "or use a different network/IP."
        )
    if page.locator("#login_username").count() or page.get_by_role(
        "heading", name="Login"
    ).count():
        raise RuntimeError(
            "Saved EPrints session is not logged in. Run "
            "`python eprint_login.py --login` once in a visible browser."
        )


def lookup_rta_record(
    page: Page, email: str, password: str, nim: str
) -> RtaRecord:
    """Look up a student's academic record in the RTA admin system."""
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
    page.goto(RTA_ITEMS_URL, wait_until="domcontentloaded")
    if page.locator("#search_values").count() == 0:
        page.goto(RTA_LOGIN_URL, wait_until="domcontentloaded")
    email_field = page.get_by_placeholder("Masukkan email..")
    password_field = page.get_by_placeholder("Masukkan password..")
    if page.locator("#search_values").count() == 0:
        for attempt in range(3):
            if page.locator("#search_values").count():
                break
            if page.locator("input[placeholder='Masukkan email..']").count() == 0:
                page.goto(RTA_LOGIN_URL, wait_until="domcontentloaded")
            try:
                email_field.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
                password_field.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
                break
            except PlaywrightTimeoutError:
                if attempt == 2:
                    raise RuntimeError("RTA login page did not load its form.")
    if page.locator("#search_values").count() == 0:
        email_field.fill(email)
        password_field.fill(password)
        page.get_by_role("button", name="Masuk").click()
        page.wait_for_load_state("domcontentloaded")
        if page.url.rstrip("/") == RTA_LOGIN_URL.rstrip("/"):
            raise RuntimeError("RTA login failed: the login form is still visible.")
        page.goto(RTA_ITEMS_URL, wait_until="domcontentloaded")
    page.locator("#search_values").fill(nim)
    page.locator("#implementFilters").click()
    page.get_by_role("link", name="Detail").first.wait_for()
    page.get_by_role("link", name="Detail").first.click()
    page.wait_for_load_state("domcontentloaded")

    body_text = " ".join(page.locator("body").inner_text().split())
    match = re.search(r"Nama Lengkap\s*:\s*(.*?)\s+Nomor Induk\s*:", body_text)
    if not match:
        raise RuntimeError(f"Could not find a full name for NIM {nim}.")
    name = match.group(1).strip()
    nim_match = re.search(r"Nomor Induk\s*:\s*(.*?)\s+Angkatan\s*:", body_text)
    prodi_match = re.search(
        r"Program Studi \(prodi\)\s*:\s*(.*?)\s+Kode Dikti Prodi\s*:", body_text
    )
    title_match = re.search(
        r"Judul \(Indonesia\)\s+(.*?)\s+Judul \(Inggris\)", body_text
    )
    faculty_elements = page.locator('[id^="fakultas-name-"]').all()
    prodi_format_elements = page.locator('[id^="prodi-name-eprint-"]').all()
    abstract_label = page.locator("strong", has_text="Abstrak").first
    abstract = " ".join(abstract_label.locator("../..").inner_text().split())
    abstract = re.sub(r"^Abstrak\s+", "", abstract)
    supervisor_names = [
        element.inner_text().strip()
        for element in page.locator('[id^="pembimbings-name-"]').all()
    ]
    supervisor_ids = [
        element.inner_text().strip()
        for element in page.locator('[id^="pembimbings-nidn-"]').all()
    ]
    supervisors = tuple(zip(supervisor_names, supervisor_ids))
    if (
        not nim_match
        or not prodi_match
        or not title_match
        or not faculty_elements
        or not prodi_format_elements
        or not abstract
        or not supervisors
        or len(supervisor_names) != len(supervisor_ids)
    ):
        raise RuntimeError(f"Could not find complete RTA data for NIM {nim}.")
    return RtaRecord(
        nim=nim_match.group(1).strip(),
        name=name,
        title=title_match.group(1).strip(),
        abstract=abstract,
        prodi=prodi_match.group(1).strip(),
        faculty=faculty_elements[0].inner_text().strip(),
        prodi_format=prodi_format_elements[0].inner_text().strip(),
        supervisors=supervisors,
    )


def first_pdf(month: str) -> Path:
    files = pdf_files(month)
    if not files:
        raise RuntimeError(f"No PDF files found in file/{month}.")
    return files[0]


def pdf_files(month: str) -> list[Path]:
    return sorted(Path("file", month).glob("*.pdf"), key=lambda path: path.name)


def title_exists(page: Page, title: str) -> bool:
    page.goto(ITEMS_URL, wait_until="domcontentloaded")
    return page.get_by_text(title, exact=True).count() > 0


def _split_person_name(name: str) -> tuple[str, str]:
    person = re.sub(r"^(?:(?:Prof|Dr|dr)\.\s*)+", "", name).split(",", 1)[0]
    parts = person.split()
    if not parts:
        raise RuntimeError(f"Could not parse person name: {name}")
    if len(parts) == 1:
        return ".", parts[0]
    return " ".join(parts[:-1]), parts[-1]


def _select_division(page: Page, faculty: str, prodi_format: str) -> None:
    prodi = prodi_format.split("#", 1)[-1]
    prodi = re.sub(r"\s*\([^)]*\)\s*$", "", prodi).strip()
    prodi = re.sub(r"\s*-\s*(?:D4|S1|S2|S3|Sp2)\s*$", "", prodi, flags=re.I)
    target_faculty = re.sub(
        r"^fakultas\s+", "", re.sub(r"\s+", " ", faculty).strip(), flags=re.I
    ).casefold()
    target_prodi = re.sub(r"\s+", " ", prodi).strip().casefold()
    options = page.locator("#c19_divisions option").all()
    for option in options:
        label = re.sub(r"\s+", " ", option.inner_text()).strip()
        label_match = re.match(r"^(.*?):\s*(.*)$", label)
        if not label_match:
            continue
        option_faculty = re.sub(
            r"^fakultas\s+", "", label_match.group(1), flags=re.I
        ).casefold()
        option_prodi = label_match.group(2).casefold()
        if option_faculty == target_faculty and option_prodi == target_prodi:
            page.locator("#c19_divisions").select_option(
                option.get_attribute("value") or ""
            )
            return
    raise RuntimeError(f"Could not match repository division for {faculty}: {prodi}.")


def _add_subjects(page: Page, faculty: str, prodi_format: str) -> None:
    """Add the required subject hierarchy for the study program."""
    prodi = prodi_format.split("#", 1)[-1].upper()
    if "MATEMATIKA" not in prodi:
        raise RuntimeError(
            f"No subject mapping is configured for program: {prodi_format}"
        )
    page.locator("dt.ep_subjectinput_tree").filter(has_text="Q Science").locator(
        "a"
    ).click()
    page.locator('input[name="_internal_c31_Q1_add"]').click()
    page.wait_for_load_state("domcontentloaded")
    page.locator('input[name="_internal_c31_QA_add"]').click()
    page.wait_for_load_state("domcontentloaded")


def prepare_first_item(
    eprint_page: Page,
    rta_page: Page,
    eprint_username: str,
    eprint_password: str,
    rta_email: str,
    rta_password: str,
    month: str,
    deposit: bool,
    pdf_path: Path | None = None,
    publication_date: date | None = None,
    use_saved_login: bool = False,
) -> bool:
    """Create one thesis draft through the subjects step."""
    pdf_path = pdf_path or first_pdf(month)
    nim_match = re.match(r"([A-Za-z]\d+)-", pdf_path.name)
    if not nim_match:
        raise RuntimeError(f"Could not extract a NIM from {pdf_path.name}.")
    nim = nim_match.group(1)
    record = lookup_rta_record(rta_page, rta_email, rta_password, nim)

    if use_saved_login:
        require_saved_login(eprint_page)
    else:
        login(eprint_page, eprint_username, eprint_password)
    if title_exists(eprint_page, record.title):
        print(f"Skipped {pdf_path}: title already exists.")
        return False
    eprint_page.get_by_role("button", name="New Item").click()
    eprint_page.locator('input[name="c1_type"][value="thesis"]').check()
    eprint_page.locator('input[name="_action_next"]').first.click()
    eprint_page.wait_for_load_state("domcontentloaded")
    eprint_page.locator(
        '[id="c2_Screen::EPrint::UploadMethod::File_file"]'
    ).set_input_files(str(pdf_path.resolve()))
    eprint_page.locator('select[name$="_security"]').wait_for()
    eprint_page.locator('select[name$="_security"]').select_option("staffonly")
    eprint_page.locator('select[name$="_language"]').select_option("id")
    eprint_page.locator('input[name="_action_next"]').first.click()
    eprint_page.wait_for_load_state("domcontentloaded")
    eprint_page.locator("#c4_title").fill(record.title)
    eprint_page.locator("#c5_abstract").fill(record.abstract)

    degree_match = re.search(r"\b(D4|S1|S2|S3|Sp2)\b", record.prodi, re.I)
    if not degree_match:
        raise RuntimeError(f"Could not determine degree from prodi: {record.prodi}")
    degree = degree_match.group(1).upper()
    thesis_type = {
        "D4": "diploma",
        "S1": "other",
        "S2": "masters",
        "S3": "doctoral",
        "SP2": "postdoctoral",
    }[degree]
    thesis_name = {
        "D4": "other",
        "S1": "mphil",
        "S2": "dphil",
        "S3": "phd",
        "SP2": "other",
    }[degree]
    eprint_page.locator(
        f'input[name="c7_thesis_type"][value="{thesis_type}"]'
    ).check()
    eprint_page.locator(
        f'input[name="c8_thesis_name"][value="{thesis_name}"]'
    ).check()

    creator_given, creator_family = _split_person_name(record.name)
    eprint_page.locator("#c15_creators_1_name_family").fill(creator_family)
    eprint_page.locator("#c15_creators_1_name_given").fill(creator_given)
    eprint_page.locator("#c15_creators_1_id").fill(record.nim)
    supervisor, supervisor_id = record.supervisors[0]
    given, family = _split_person_name(supervisor)
    eprint_page.locator("#c17_contributors_1_name_family").fill(family)
    eprint_page.locator("#c17_contributors_1_name_given").fill(given)
    eprint_page.locator("#c17_contributors_1_id").fill(supervisor_id)

    _select_division(eprint_page, record.faculty, record.prodi_format)
    eprint_page.locator("#c20_ispublished[value='pub']").check()
    eprint_page.locator("#c20_date_type[value='published']").check()
    publication_date = publication_date or date.today()
    eprint_page.locator("#c20_date_year").fill(str(publication_date.year))
    eprint_page.locator("#c20_date_month").select_option(f"{publication_date.month:02d}")
    eprint_page.locator("#c20_date_day").select_option(f"{publication_date.day:02d}")
    eprint_page.locator("#c20_institution").fill("UNIVERSITAS HASANUDDIN")
    eprint_page.locator("#c20_department").fill(record.prodi_format)
    eprint_page.locator('input[name="_action_next"]').first.click()
    eprint_page.wait_for_load_state("domcontentloaded")
    _add_subjects(eprint_page, record.faculty, record.prodi_format)
    eprint_page.locator('input[name="_action_next"]').first.click()
    eprint_page.wait_for_load_state("domcontentloaded")
    if deposit:
        eprint_page.locator('input[name="_action_jump_deposit"]').click()
        eprint_page.wait_for_load_state("domcontentloaded")
        eprint_page.locator('input[name="_action_deposit"]').click()
        eprint_page.wait_for_load_state("domcontentloaded")
    print(
        f"Prepared {pdf_path} for {record.nim} ({record.name}); "
        "title, abstract, degree, creator, contributors, division, "
        "publication details, subjects, and current date filled."
    )
    print(f"Current page: {eprint_page.url}")
    return True


def prepare_item_in_browser(
    browser: Browser | BrowserContext,
    eprint_username: str,
    eprint_password: str,
    rta_email: str,
    rta_password: str,
    month: str,
    deposit: bool,
    pdf_path: Path,
    publication_date: date | None = None,
    use_saved_login: bool = False,
) -> bool:
    """Prepare one item and close its temporary browser pages."""
    eprint_page = browser.new_page()
    try:
        rta_page = browser.new_page()
    except Exception:
        eprint_page.close()
        raise
    try:
        return prepare_first_item(
            eprint_page,
            rta_page,
            eprint_username,
            eprint_password,
            rta_email,
            rta_password,
            month,
            deposit,
            pdf_path,
            publication_date,
            use_saved_login,
        )
    finally:
        rta_page.close()
        eprint_page.close()


def run(headless: bool, keep_open: bool) -> None:
    load_dotenv()
    username = _required_env("EPRINT_USERNAME")
    password = _required_env("EPRINT_PASSWORD")

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, headless)
        try:
            page = browser.new_page()
            login(page, username, password)
            print(f"Login successful. Current page: {page.url}")
            if keep_open:
                input("Press Enter to close the browser...")
        finally:
            browser.close()


def _launch_browser(playwright: Playwright, headless: bool) -> Browser:
    return playwright.chromium.launch(channel="chromium", headless=headless)


def _launch_saved_context(playwright: Playwright, headless: bool) -> BrowserContext:
    profile = Path(os.getenv("EPRINTS_BROWSER_PROFILE", ".eprints-browser-profile"))
    return playwright.chromium.launch_persistent_context(
        str(profile.resolve()), channel="chromium", headless=headless
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log in to EPrints and open the Items page."
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open a visible browser to create or refresh the saved EPrints session.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show a browser window instead of running headless.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep the browser open after reaching the Items page.",
    )
    parser.add_argument(
        "--lookup-nim",
        metavar="NIM",
        help="Look up a NIM in RTA and print the student's full name.",
    )
    parser.add_argument(
        "--prepare-first",
        metavar="MONTH",
        help="Prepare the first sorted PDF in file/MONTH through the title step.",
    )
    parser.add_argument(
        "--deposit",
        action="store_true",
        help="Deposit the prepared item after reaching the Deposit page.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of sorted PDF files to process (default: 1).",
    )
    args = parser.parse_args()
    if args.login:
        load_dotenv()
        eprint_username = _required_env("EPRINT_USERNAME")
        eprint_password = _required_env("EPRINT_PASSWORD")
        with sync_playwright() as playwright:
            context = _launch_saved_context(playwright, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                login(page, eprint_username, eprint_password)
                require_saved_login(page)
                print("Login EPrints tersimpan.")
                if args.keep_open:
                    input("Tekan Enter untuk menutup browser...")
            finally:
                context.close()
    elif args.prepare_first:
        if args.count < 1:
            parser.error("--count must be at least 1")
        load_dotenv()
        eprint_username = _required_env("EPRINT_USERNAME")
        eprint_password = _required_env("EPRINT_PASSWORD")
        rta_email = _required_env("RTA_EMAIL")
        rta_password = _required_env("RTA_PASSWORD")
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright, headless=not args.headed)
            try:
                files = pdf_files(args.prepare_first)
                if not files:
                    raise RuntimeError(
                        f"No PDF files found in file/{args.prepare_first}."
                    )
                for pdf_path in files[: args.count]:
                    try:
                        prepare_item_in_browser(
                            browser,
                            eprint_username,
                            eprint_password,
                            rta_email,
                            rta_password,
                            args.prepare_first,
                            args.deposit,
                            pdf_path,
                        )
                    except Exception as exc:
                        print(f"Failed {pdf_path}: {exc}")
                if args.keep_open:
                    input("Press Enter to close the browser...")
            finally:
                browser.close()
    elif args.lookup_nim:
        load_dotenv()
        email = _required_env("RTA_EMAIL")
        password = _required_env("RTA_PASSWORD")
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright, headless=not args.headed)
            try:
                record = lookup_rta_record(
                    browser.new_page(), email, password, args.lookup_nim
                )
                print(f"{args.lookup_nim}: {record.name}")
            finally:
                browser.close()
    else:
        run(headless=not args.headed, keep_open=args.keep_open)


if __name__ == "__main__":
    main()
