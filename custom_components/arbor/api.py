"""API client for Arbor School system."""

from __future__ import annotations

import html
import logging
import re
import secrets
import time
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import aiohttp

from .const import (
    API_HEADERS,
    AUTH_AUTHORIZE_PATH,
    AUTH_BASE_URL,
    AUTH_TOKEN_PATH,
    OAUTH_CLIENT_ID,
)

_LOGGER = logging.getLogger(__name__)


class ArborApiError(Exception):
    """General API error."""


class ArborAuthError(ArborApiError):
    """Authentication error."""


class ArborApiClient:
    """Client to interact with the Arbor School API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        school_domain: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        token_expiry: float | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Initialise the API client."""
        self._session = session
        self.school_domain = school_domain
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expiry = token_expiry or 0.0
        # Credentials are stashed so the client can self-recover when the
        # server invalidates the session (e.g. the parent logged in on
        # another device). Without them we can only try the refresh token.
        self._username = username
        self._password = password

    @property
    def _school_base_url(self) -> str:
        """Return the base URL for the school API."""
        return f"https://{self.school_domain}"

    @property
    def _auth_headers(self) -> dict[str, str]:
        """Return headers with Bearer token for authenticated requests."""
        headers = dict(API_HEADERS)
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def _is_token_expired(self) -> bool:
        """Check if the access token is expired or about to expire."""
        # Refresh 5 minutes before actual expiry
        return time.time() >= (self.token_expiry - 300)

    async def authenticate(self, username: str, password: str) -> dict[str, Any]:
        """Run the full 3-step OAuth flow.

        Returns dict with school info and tokens.
        """
        # Stash credentials so subsequent 401/403 responses can trigger a
        # full re-authentication without the caller having to orchestrate it.
        self._username = username
        self._password = password

        # Step 1: Authorize — get authorization code
        authorize_url = f"{AUTH_BASE_URL}{AUTH_AUTHORIZE_PATH}"
        params = {
            "client_id": OAUTH_CLIENT_ID,
            "response_type": "code",
            "state": secrets.token_hex(32),
        }
        payload = {"username": username, "password": password}

        async with self._session.post(
            authorize_url,
            params=params,
            json=payload,
            headers=API_HEADERS,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ArborAuthError(
                    f"Authorization failed (HTTP {resp.status}): {text}"
                )
            data = await resp.json()
            code = data.get("code")
            if not code:
                raise ArborAuthError("No authorization code in response")

        _LOGGER.debug("Step 1 complete: got authorization code")

        # Step 2: Exchange code for school list and refresh tokens
        token_url = f"{AUTH_BASE_URL}{AUTH_TOKEN_PATH}"
        payload = {
            "grant_type": "authorization_code",
            "client_id": OAUTH_CLIENT_ID,
            "code": code,
        }

        async with self._session.post(
            token_url,
            json=payload,
            headers=API_HEADERS,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ArborAuthError(
                    f"Token exchange failed (HTTP {resp.status}): {text}"
                )
            data = await resp.json()

        schools = data.get("schools", [])
        tokens = data.get("tokens", [])

        if not schools or not tokens:
            raise ArborAuthError("No schools found for this account")

        _LOGGER.debug("Step 2 complete: found %d school(s)", len(schools))

        # Step 3: Get access token from school domain
        # Use the first school (most accounts have one)
        school = schools[0]
        self.school_domain = school["domain"]
        school_refresh_token = tokens[0]["refresh_token"]

        await self._exchange_refresh_token(school_refresh_token)

        _LOGGER.debug("Step 3 complete: got access token for %s", self.school_domain)

        return {
            "school_domain": self.school_domain,
            "school_name": school.get("name", self.school_domain),
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "token_expiry": self.token_expiry,
        }

    async def _exchange_refresh_token(self, refresh_token: str) -> None:
        """Exchange a refresh token for a new access token at the school domain."""
        token_url = f"{self._school_base_url}{AUTH_TOKEN_PATH}"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        }

        async with self._session.post(
            token_url,
            json=payload,
            headers=API_HEADERS,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ArborAuthError(
                    f"Refresh token exchange failed (HTTP {resp.status}): {text}"
                )
            data = await resp.json()

        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        self.token_expiry = time.time() + data.get("expires_in", 3600)

    async def _refresh_or_reauth(self) -> None:
        """Obtain fresh tokens: try the refresh token first, then full auth.

        Raises ArborAuthError if neither path can succeed (no refresh token
        *and* no stored credentials, or both paths fail).
        """
        if self.refresh_token:
            try:
                await self._exchange_refresh_token(self.refresh_token)
                return
            except ArborAuthError as err:
                _LOGGER.info(
                    "Refresh token exchange failed (%s); "
                    "attempting full re-authentication",
                    err,
                )

        if self._username and self._password:
            await self.authenticate(self._username, self._password)
            return

        raise ArborAuthError(
            "Cannot obtain a new access token: no valid refresh token and "
            "no stored credentials for re-authentication"
        )

    async def ensure_valid_token(self) -> None:
        """Ensure we have a valid, non-expired access token (by clock)."""
        if self._is_token_expired():
            _LOGGER.debug("Access token near/past expiry, renewing")
            await self._refresh_or_reauth()

    async def _get(self, path: str) -> Any:
        """Make an authenticated GET request to the school API.

        Arbor returns 401 when the bearer token is missing/malformed and 403
        (with body "User is not allowed to access …") once a token has been
        invalidated server-side — most commonly when the same guardian logs
        in on another device. Both are treated as recoverable here: we try
        refresh → full re-auth → retry once.
        """
        await self.ensure_valid_token()
        url = f"{self._school_base_url}{path}"

        auth_error_status: int | None = None
        async with self._session.get(url, headers=self._auth_headers) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            if resp.status in (401, 403):
                auth_error_status = resp.status
                # Drain body for the debug log; don't hold the connection
                # open while we re-authenticate.
                body_preview = (await resp.text())[:200]
            else:
                text = await resp.text()
                raise ArborApiError(f"API request failed (HTTP {resp.status}): {text}")

        _LOGGER.debug(
            "Auth error (HTTP %s) on %s, recovering: %s",
            auth_error_status,
            path,
            body_preview,
        )
        await self._refresh_or_reauth()

        async with self._session.get(url, headers=self._auth_headers) as retry_resp:
            if retry_resp.status != 200:
                text = await retry_resp.text()
                raise ArborApiError(
                    f"API request failed after re-authentication "
                    f"(HTTP {retry_resp.status}): {text}"
                )
            return await retry_resp.json(content_type=None)

    # ── Discovery ─────────────────────────────────────────────

    async def get_dashboard(self) -> dict[str, Any]:
        """Fetch the main dashboard and extract student info."""
        data = await self._get("/guardians/home-ui/dashboard")
        return data

    def parse_students(self, dashboard_data: dict) -> list[dict[str, Any]]:
        """Extract student IDs and names from the dashboard widget tree."""
        students: list[dict[str, Any]] = []
        self._find_students_recursive(dashboard_data, students)
        # Deduplicate by student_id
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for s in students:
            if s["student_id"] not in seen:
                seen.add(s["student_id"])
                unique.append(s)
        return unique

    def _find_students_recursive(
        self, node: dict, results: list[dict[str, Any]]
    ) -> None:
        """Recursively search widget tree for page-toggle items with student URLs."""
        if not isinstance(node, dict):
            return

        if node.get("name") == "page-toggle-item":
            attrs = node.get("attributes", {})
            label = attrs.get("label", "")
            url = attrs.get("url", "")

            # Match student-id from URL
            match = re.search(r"student-id/(\d+)", url)
            if match:
                student_id = match.group(1)
                # Clean name: remove brackets
                name = label.strip("[] ")
                if name:
                    results.append({"student_id": student_id, "name": name})

        for child in node.get("children", []):
            self._find_students_recursive(child, results)

    def parse_academic_year_id(self, page_data: dict) -> str | None:
        """Extract the current academic year ID from a page widget tree."""
        result = self._find_academic_year_recursive(page_data)
        return result

    def _find_academic_year_recursive(self, node: dict) -> str | None:
        """Recursively find the selected academic year toggle."""
        if not isinstance(node, dict):
            return None

        attrs = node.get("attributes", {})
        if node.get("name") == "page-toggle" and attrs.get("label") == "Academic year":
            for child in node.get("children", []):
                child_attrs = child.get("attributes", {})
                if child_attrs.get("selected") == "1":
                    url = child_attrs.get("url", "")
                    match = re.search(r"academic-year-id/(\d+)", url)
                    if match:
                        return match.group(1)

        for child in node.get("children", []):
            result = self._find_academic_year_recursive(child)
            if result:
                return result
        return None

    # ── KPIs (attendance + behaviour) ─────────────────────────

    async def get_kpis(self, student_id: str) -> dict[str, Any]:
        """Fetch KPI data (attendance and behaviour) for a student."""
        data = await self._get(f"/guardians/student/kpis/id/{student_id}/")
        return self._parse_kpis(data)

    def _parse_kpis(self, data: dict) -> dict[str, Any]:
        """Parse KPI response into structured data."""
        result: dict[str, Any] = {
            "attendance_year": None,
            "attendance_last_4_weeks": None,
            "positive_this_term": None,
            "positive_last_term": None,
            "positive_year": None,
            "negative_this_term": None,
            "negative_last_term": None,
            "negative_year": None,
        }

        items = data.get("items", [])
        for item in items:
            fields = item.get("fields", {})
            title = fields.get("title", {}).get("value", "")
            kpi_data = fields.get("data", {}).get("value", {})

            if not kpi_data:
                continue

            if "Attendance" in title:
                result["attendance_year"] = self._round_attendance(
                    kpi_data.get("measureRawValue")
                )
                result["attendance_last_4_weeks"] = self._round_attendance(
                    kpi_data.get("comparisonRawValue")
                )

            elif "Positive" in title:
                result["positive_this_term"] = kpi_data.get("measureRawValue")
                result["positive_last_term"] = kpi_data.get("comparisonRawValue")
                # Parse year total from measureShortLabel
                year_total = self._extract_year_total(
                    kpi_data.get("measureShortLabel", "")
                )
                result["positive_year"] = year_total

            elif "Negative" in title:
                result["negative_this_term"] = kpi_data.get("measureRawValue")
                result["negative_last_term"] = kpi_data.get("comparisonRawValue")
                year_total = self._extract_year_total(
                    kpi_data.get("measureShortLabel", "")
                )
                result["negative_year"] = year_total

        return result

    @staticmethod
    def _round_attendance(value: Any) -> float | int | None:
        """Round an attendance percentage to 2 dp, returning int when whole."""
        if value is None:
            return None
        try:
            rounded = round(float(value), 2)
        except (TypeError, ValueError):
            return None
        if rounded == int(rounded):
            return int(rounded)
        return rounded

    @staticmethod
    def _extract_year_total(label: str) -> int | None:
        """Extract numeric total from label like 'This year: 109 incidents'."""
        match = re.search(r"This year:\s*(\d+)", label)
        if match:
            return int(match.group(1))
        return None

    # ── Assignments ───────────────────────────────────────────

    async def get_assignment_counts(
        self, student_id: str, academic_year_id: str
    ) -> dict[str, int]:
        """Fetch assignment count KPIs."""
        data = await self._get(
            f"/guardians/student/assignments-kpi/student-id/{student_id}"
            f"/academic-year-id/{academic_year_id}"
        )
        result = {"due": 0, "overdue": 0, "submitted": 0}
        if isinstance(data, list):
            for item in data:
                title = item.get("title", "").lower()
                value = int(item.get("mainValue", 0))
                if "that are due" in title:
                    result["due"] = value
                elif "overdue" in title:
                    result["overdue"] = value
                elif "submitted" in title:
                    result["submitted"] = value
        return result

    async def get_assignments_due(
        self, student_id: str, academic_year_id: str
    ) -> list[dict[str, str]]:
        """Fetch list of assignments that are due."""
        data = await self._get(
            f"/guardians/student-ui/assignments-due/student-id/{student_id}"
            f"/academic-year-id/{academic_year_id}"
        )
        return self._parse_assignment_list(data)

    async def get_assignments_overdue(
        self, student_id: str, academic_year_id: str
    ) -> list[dict[str, str]]:
        """Fetch list of overdue assignments."""
        data = await self._get(
            f"/guardians/student-ui/assignments-overdue/student-id/{student_id}"
            f"/academic-year-id/{academic_year_id}"
        )
        return self._parse_assignment_list(data)

    async def get_assignments_submitted(
        self, student_id: str, academic_year_id: str
    ) -> list[dict[str, str]]:
        """Fetch list of submitted assignments."""
        data = await self._get(
            f"/guardians/student-ui/assignments-submitted/student-id/{student_id}"
            f"/academic-year-id/{academic_year_id}"
        )
        return self._parse_assignment_list(data)

    def _parse_assignment_list(self, data: dict) -> list[dict[str, str]]:
        """Parse the page widget tree to extract assignment details."""
        assignments: list[dict[str, str]] = []
        self._find_assignments_recursive(data, assignments)
        return assignments

    def _find_assignments_recursive(
        self, node: dict, results: list[dict[str, str]]
    ) -> None:
        """Recursively find property-row nodes that represent assignments."""
        if not isinstance(node, dict):
            return

        if node.get("name") == "property-row":
            attrs = node.get("attributes", {})
            description = attrs.get("description", "")

            # Look for the content in children
            for child in node.get("children", []):
                if child.get("name") == "property-row-content":
                    content = child.get("content", "") or ""
                    # Skip "View all assignments" link
                    if "View all" in content:
                        continue

                    # Parse title and due date from HTML content
                    title = self._strip_html(content)
                    due_date = self._extract_due_date(content)

                    if title:
                        results.append(
                            {
                                "title": title.strip(),
                                "due_date": due_date,
                                "status": description,
                            }
                        )

        for child in node.get("children", []):
            self._find_assignments_recursive(child, results)

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove HTML tags from text."""
        clean = re.sub(r"<[^>]+>", "", text)
        # Clean up whitespace and special chars
        clean = clean.replace("\u2002", " ").replace("\xa0", " ")
        # Remove the (Due...) part from the title
        clean = re.sub(r"\s*\(Due.*?\)\s*$", "", clean)
        return clean.strip()

    @staticmethod
    def _extract_due_date(text: str) -> str:
        """Extract due date from content like '(Due 16 Apr 2026)'."""
        match = re.search(r"\(Due\s*([\d]+\s+\w+\s+\d{4})\)", text.replace("\xa0", " "))
        if match:
            return match.group(1)
        return ""

    # ── Calendar / Timetable ──────────────────────────────────

    async def get_calendar(
        self, student_id: str, for_date: date | None = None
    ) -> list[dict[str, Any]]:
        """Fetch the daily calendar/timetable for a student."""
        path = f"/guardians/widget-data/get-calendar-data/student-id/{student_id}/"
        if for_date:
            path += f"date/{for_date.isoformat()}"

        data = await self._get(path)
        return self._parse_calendar(data)

    @staticmethod
    def _parse_calendar(data: dict) -> list[dict[str, Any]]:
        """Parse calendar response into structured lesson data."""
        lessons: list[dict[str, Any]] = []
        for item in data.get("items", []):
            fields = item.get("fields", {})
            title_raw = fields.get("title", {}).get("value", "")
            location = fields.get("location", {}).get("value", "")
            start = fields.get("start_datetime", {}).get("value", "")
            end = fields.get("end_datetime", {}).get("value", "")

            # Parse title: "Geography: 7: 7GE-K2" → subject = "Geography"
            parts = title_raw.split(":")
            subject = parts[0].strip() if parts else title_raw

            # Check for form/registration periods (e.g. "Year Groups: 7: 7FN")
            if "Year Groups" in title_raw:
                subject = "Registration"

            lessons.append(
                {
                    "subject": subject,
                    "full_title": title_raw,
                    "location": location,
                    "start": start,
                    "end": end,
                }
            )
        return lessons

    # ── Clubs ─────────────────────────────────────────────────

    async def get_clubs(self, student_id: str) -> dict[str, list[dict[str, str]]]:
        """Fetch the clubs a student is registered for and can register for."""
        data = await self._get(f"/guardians/club-ui/dashboard/student-id/{student_id}")
        return self._parse_clubs(data)

    @classmethod
    def _parse_clubs(cls, data: dict) -> dict[str, list[dict[str, str]]]:
        """Split the club dashboard sections into registered/available lists.

        The page has one section titled "<name> is registered for these clubs"
        and one titled "<name> Can be Registered For These Clubs". Rows are
        parsed the same way as other Arbor property-row lists.
        """
        result: dict[str, list[dict[str, str]]] = {"registered": [], "available": []}
        for section in cls._find_nodes(data, "section"):
            title = _attrs(section).get("title", "").lower()
            bucket = "available" if "can be registered" in title else "registered"
            result[bucket].extend(cls._parse_section_rows(section))
        return result

    @classmethod
    def _parse_section_rows(cls, section: dict) -> list[dict[str, str]]:
        """Extract name/details/url from the direct children of a section."""
        rows: list[dict[str, str]] = []
        for child in section.get("children", []):
            if not isinstance(child, dict):
                continue
            attrs = _attrs(child)
            content = _child_content(child, "property-row-content")
            if content is None:
                content = child.get("content") or ""
            name = (
                cls._clean_text(content)
                or attrs.get("value", "")
                or attrs.get("title", "")
                or attrs.get("label", "")
            )
            if not name:
                continue
            rows.append(
                {
                    "name": name,
                    "details": cls._clean_text(attrs.get("description", "")),
                    "url": _child_url(child) or attrs.get("url", ""),
                }
            )
        return rows

    # ── School messages ───────────────────────────────────────

    async def get_school_messages(self) -> list[dict[str, str]]:
        """Fetch the guardian's school messages, newest first."""
        data = await self._get("/guardians/communication-center-ui/school-messages")
        return self._parse_school_messages(data)

    @classmethod
    def _parse_school_messages(cls, data: dict) -> list[dict[str, str]]:
        """Parse message rows into id/subject/preview/received dicts.

        Each row's content is plain text: the subject and a truncated preview
        separated by runs of whitespace. The description holds the received
        time, e.g. "14 September 2026, 10:34" (with non-breaking spaces).
        """
        messages: list[dict[str, str]] = []
        for row in cls._find_nodes(data, "property-row"):
            match = re.search(r"/id/(\d+)", _child_url(row))
            if not match:
                continue
            raw = html.unescape(_child_content(row, "property-row-content") or "")
            parts = [p.strip() for p in re.split(r"\s{3,}", raw) if p.strip()]
            messages.append(
                {
                    "id": match.group(1),
                    "subject": parts[0] if parts else "",
                    "preview": " ".join(parts[1:]),
                    "received": cls._parse_arbor_datetime(
                        _attrs(row).get("description", "")
                    ),
                }
            )
        return messages

    async def get_school_message(self, message_id: str) -> dict[str, str]:
        """Fetch one school message with its sender and full body."""
        data = await self._get(
            "/guardians/outbound-in-app-message-ui/view-outbound-in-app-message"
            f"/id/{message_id}"
        )
        return self._parse_school_message(message_id, data)

    @classmethod
    def _parse_school_message(cls, message_id: str, data: dict) -> dict[str, str]:
        """Parse the message slide-over (Subject/Received/Sent by + body)."""
        fields = {
            _attrs(row).get("label", ""): _attrs(row).get("value", "")
            for row in cls._find_nodes(data, "property-row")
        }
        body = "\n\n".join(
            html.unescape(node.get("content") or "").replace("\xa0", " ").strip()
            for node in cls._find_nodes(data, "simple-text")
        )
        return {
            "id": message_id,
            "subject": fields.get("Subject") or _attrs(data).get("title", ""),
            "received": cls._parse_arbor_datetime(fields.get("Received", "")),
            "sent_by": fields.get("Sent by", ""),
            "body": body,
        }

    @staticmethod
    def _parse_arbor_datetime(text: str) -> str:
        """Convert "14 September 2026, 10:34" into a naive ISO string."""
        cleaned = " ".join(text.replace("\xa0", " ").split())
        try:
            return datetime.strptime(cleaned, "%d %B %Y, %H:%M").isoformat()
        except ValueError:
            return ""

    # ── Widget tree helpers ───────────────────────────────────

    @classmethod
    def _find_nodes(cls, node: Any, name: str) -> Iterator[dict]:
        """Yield every node in a widget tree with the given widget name."""
        if not isinstance(node, dict):
            return
        if node.get("name") == name:
            yield node
        for child in node.get("children", []):
            yield from cls._find_nodes(child, name)

    @staticmethod
    def _clean_text(text: str) -> str:
        """Strip tags and entities and collapse whitespace."""
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())

    # ── Academic year discovery ────────────────────────────────

    async def discover_academic_year_id(self, student_id: str) -> str | None:
        """Discover the current academic year ID from the assignments page.

        Returns None when the page has no selected academic-year toggle, so
        callers can decide whether to keep a previously stored value.
        """
        data = await self._get(
            f"/guardians/student-ui/assignments-due/student-id/{student_id}"
        )
        year_id = self.parse_academic_year_id(data)
        if not year_id:
            _LOGGER.warning("Could not discover academic year ID")
        return year_id


def _attrs(node: dict) -> dict[str, Any]:
    """Return a widget's attributes; Arbor sends [] instead of {} when empty."""
    attrs = node.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def _child_content(node: dict, child_name: str) -> str | None:
    """Return the content of the first direct child with the given name."""
    for child in node.get("children", []):
        if isinstance(child, dict) and child.get("name") == child_name:
            return child.get("content") or ""
    return None


def _child_url(node: dict) -> str:
    """Return the target URL of a row's event-listener child, if any."""
    for child in node.get("children", []):
        if isinstance(child, dict) and child.get("name") == "event-listener":
            return _attrs(child).get("url", "")
    return ""
