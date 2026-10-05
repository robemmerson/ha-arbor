"""Constants for the Arbor School integration."""

from datetime import timedelta

DOMAIN = "arbor"

# OAuth endpoints
AUTH_BASE_URL = "https://login.arbor.sc"
AUTH_AUTHORIZE_PATH = "/oauth/authorize"
AUTH_TOKEN_PATH = "/oauth/token"
OAUTH_CLIENT_ID = "Arbor"

# API defaults
DEFAULT_SCAN_INTERVAL_MINUTES = 15
MIN_SCAN_INTERVAL_MINUTES = 5
MAX_SCAN_INTERVAL_MINUTES = 240
CALENDAR_SCAN_INTERVAL = timedelta(minutes=60)

# Used only when the academic year can't be discovered from Arbor
DEFAULT_ACADEMIC_YEAR_ID = "24"

# Config keys
CONF_SCHOOL_DOMAIN = "school_domain"
CONF_SCHOOL_NAME = "school_name"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN = "access_token"
CONF_TOKEN_EXPIRY = "token_expiry"
CONF_STUDENTS = "students"
CONF_ACADEMIC_YEAR_ID = "academic_year_id"

# Request headers (mimics mobile app)
API_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Arbor-Client": "Mobile",
    "Mobile-App-Version": "0.9.398",
    "User-Agent": "Arbor/2 CFNetwork/3860.500.112 Darwin/25.4.0",
}

# Data keys used in coordinator
DATA_KPIS = "kpis"
DATA_ASSIGNMENT_COUNTS = "assignment_counts"
DATA_ASSIGNMENTS_DUE = "assignments_due"
DATA_ASSIGNMENTS_OVERDUE = "assignments_overdue"
DATA_ASSIGNMENTS_SUBMITTED = "assignments_submitted"
DATA_CALENDAR = "calendar"
DATA_CLUBS = "clubs"
# Account-level data (not tied to one student) lives under this key
DATA_ACCOUNT = "account"
DATA_SCHOOL_MESSAGES = "school_messages"

# Fired once per newly received school message
EVENT_SCHOOL_MESSAGE = "arbor_school_message"
# Cap on detail fetches/events per poll, e.g. after a long outage
MAX_ANNOUNCED_MESSAGES = 5
# Messages exposed in sensor attributes
RECENT_MESSAGES_LIMIT = 10
# Newest messages whose full body and sender are fetched and cached
MESSAGE_DETAIL_LIMIT = 3
