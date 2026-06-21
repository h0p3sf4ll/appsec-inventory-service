API_VERSION = "7.1"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_WORKERS = 8
DEFAULT_BRANCH_WORKERS = 16
DEFAULT_CONTENT_WORKERS = 16
DEFAULT_COMMIT_PAGE_SIZE = 1000
DEFAULT_BRANCH_AGE_DAYS = 90
DEFAULT_STORE_COUNTRY = "US"
DEFAULT_STORE_TIMEOUT_SECONDS = 15
DEFAULT_ACTIVITY_MODE = "contributors"
MISSING_REQUESTS_MESSAGE = "Missing dependency: requests. Install it with `python -m pip install -r requirements.txt`."


def active_sheet_name(branch_age_days: int = DEFAULT_BRANCH_AGE_DAYS) -> str:
    return f"Active {branch_age_days}d"


def older_sheet_name(branch_age_days: int = DEFAULT_BRANCH_AGE_DAYS) -> str:
    return f"Older {branch_age_days}d"


ACTIVE_SHEET_NAME = active_sheet_name()
OLDER_SHEET_NAME = older_sheet_name()

KNOWN_CATEGORIES = (
    "android",
    "ios",
    "flutter",
    "react_native",
    "ionic_capacitor_cordova",
    "xamarin_maui",
    "pipeline_mobile",
    "android_library",
)

CATEGORY_FIELDNAMES = tuple(f"category_{category}" for category in KNOWN_CATEGORIES)

STORE_FIELDNAMES = (
    "store_lookup_status",
    "store_platforms",
    "apple_app_store_name",
    "apple_app_store_identifier",
    "apple_app_store_url",
    "apple_app_store_version",
    "apple_app_store_last_updated",
    "apple_app_store_lookup_status",
    "google_play_name",
    "google_play_identifier",
    "google_play_url",
    "google_play_version",
    "google_play_last_updated",
    "google_play_lookup_status",
)

CONTENT_FILES_TO_FETCH: tuple[str, ...] = (
    "package.json",
    "app.json",
    "expo.json",
    "app.config.js",
    "app.config.ts",
    "pubspec.yaml",
    "AndroidManifest.xml",
    "Info.plist",
    "InfoPlist.strings",
    "project.pbxproj",
    ".xcconfig",
    "strings.xml",
    "build.gradle",
    "build.gradle.kts",
    "gradle.properties",
    "azure-pipelines.yml",
    "azure-pipeline.yml",
    ".csproj",
    ".props",
    "Podfile",
    "capacitor.config.ts",
    "capacitor.config.json",
    "ionic.config.json",
    "config.xml",
)

CONTENT_FILE_SUFFIXES = tuple(name.lower() for name in CONTENT_FILES_TO_FETCH)

CSV_FIELDNAMES = (
    "project",
    "repo_name",
    "branch_name",
    "branch_last_updated",
    "branch_age_bucket",
    "web_url",
    "mobile_name",
    "mobile_version",
    "mobile_identifier",
    "mobile_identifier_source",
    "mobile_identifier_status",
    "contributing_developers",
    "last_updated",
    "confidence",
    "score",
    "categories",
    *CATEGORY_FIELDNAMES,
    *STORE_FIELDNAMES,
    "detection_evidence",
)
