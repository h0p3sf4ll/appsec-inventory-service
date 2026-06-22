# AppSec Scan Router

AppSec Scan Router is a Python SDK, CLI, and Docker image for mobile application inventory across Azure DevOps
and GitHub Enterprise. It finds mobile app branches, extracts app metadata, captures ownership and activity
signals, validates public store listings when requested, and writes Excel-ready reports while the scan is running.

The scanner is built for engineering, security, platform, and architecture leaders who need a current view of
mobile code without cloning every repository or relying on loose keyword search.

## What It Does

- Scans each repository's default branch
- Uses one fallback branch only when no default branch exists
- Uses Azure DevOps build definitions or GitHub deployment refs as the first fallback signal
- Falls back to deployment-like branch names such as `prod`, `preprod`, `main`, `master`, `development`,
  `develop`, and `dev`
- Detects Android, iOS, Flutter, React Native, Expo, Ionic, Capacitor, Cordova, Xamarin, and .NET MAUI
- Parses structured manifests and project files for stronger accuracy
- Extracts app name, version, bundle/package identifier, contributors, and last branch activity
- Splits Excel output into `Active 90d` and `Older 90d` worksheets by default
- Streams CSV and JSON rows as apps are detected
- Optionally enriches detected identifiers with public Apple App Store and Google Play data
- Runs as a CLI, SDK, importable library, or container

## Install

```bash
python -m pip install appsec-scan-router
```

For local development:

```bash
git clone https://github.com/h0p3sf4ll/mobile-app-inventory-tracer.git
cd mobile-app-inventory-tracer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Run: Azure DevOps

Set a read-only PAT:

```bash
export ADO_PAT="your-token"
```

Scan every project in an organization:

```bash
appsec-scan-router \
  --provider azure-devops \
  --org FabrikamCloud \
  --out-dir reports
```

Scan one project:

```bash
appsec-scan-router \
  --provider azure-devops \
  --org FabrikamCloud \
  --project "Go_To_Market" \
  --out-dir reports
```

`--provider azure-devops` is the default, so existing commands remain valid.

## Run: GitHub Enterprise

Set a GitHub token:

```bash
export GITHUB_TOKEN="your-token"
```

Scan every repository in an organization:

```bash
appsec-scan-router \
  --provider github-enterprise \
  --base-url https://github.fabrikam.example/api/v3 \
  --org FabrikamCloud \
  --out-dir reports
```

Scan one repository:

```bash
appsec-scan-router \
  --provider github-enterprise \
  --base-url https://github.fabrikam.example/api/v3 \
  --org FabrikamCloud \
  --repo mobile-app \
  --out-dir reports
```

`--project` is also accepted as a repository name for GitHub Enterprise. This keeps automation consistent across
providers.

## Docker

Build:

```bash
docker build -t appsec-scan-router .
```

Run against Azure DevOps:

```bash
mkdir -p reports
docker run --rm \
  -e ADO_PAT="$ADO_PAT" \
  -v "$PWD/reports:/reports" \
  appsec-scan-router \
  --provider azure-devops \
  --org FabrikamCloud \
  --out-dir /reports
```

Run against GitHub Enterprise:

```bash
mkdir -p reports
docker run --rm \
  -e GITHUB_TOKEN="$GITHUB_TOKEN" \
  -v "$PWD/reports:/reports" \
  appsec-scan-router \
  --provider github-enterprise \
  --base-url https://github.fabrikam.example/api/v3 \
  --org FabrikamCloud \
  --out-dir /reports
```

Enable public store validation:

```bash
docker run --rm \
  -e GITHUB_TOKEN="$GITHUB_TOKEN" \
  -v "$PWD/reports:/reports" \
  appsec-scan-router \
  --provider github-enterprise \
  --base-url https://github.fabrikam.example/api/v3 \
  --org FabrikamCloud \
  --out-dir /reports \
  --store-lookup \
  --store-country US
```

The image runs as a non-root `scanner` user and writes reports to the mounted output directory.

## Permissions

Azure DevOps:

- Projects: read
- Code: read
- Build: read, only needed for pipeline-based fallback branch detection

GitHub Enterprise:

- Repository metadata: read
- Repository contents: read
- Commit history: read
- Deployments: read, only needed for deployment-based fallback branch detection

Use read-only tokens with the smallest practical scope. Prefer environment variables over `--pat` so tokens do not
land in shell history.

## Branch Selection

The scanner evaluates one branch per repository.

If a default branch exists, it scans that branch. If no default branch exists, it tries to identify the branch most
likely to represent shipped code:

- Azure DevOps build definitions and branch filters
- GitHub Enterprise deployment refs for production-like environments
- Branch-name fallback using production and mainline naming patterns

There is no universal production-branch field across Git providers, CI/CD systems, and release platforms. Deployment
fallback is best-effort and depends on the token's access to build or deployment metadata.

## Detection Model

The scanner fetches only files that can provide strong mobile signals or app metadata, including:

- `AndroidManifest.xml`
- `Info.plist`
- `InfoPlist.strings`
- `project.pbxproj`
- `.xcconfig`
- `build.gradle`
- `build.gradle.kts`
- `gradle.properties`
- `package.json`
- `app.json`
- `expo.json`
- `pubspec.yaml`
- `.csproj`
- `.props`
- `capacitor.config.*`
- `ionic.config.json`
- `config.xml`
- Pipeline YAML files

Detection is evidence-based. Generic `.csproj` files, generic `config.xml` files, and weak pipeline-only clues are
not enough on their own to classify a branch as mobile.

## Metadata

The report includes these app fields when they can be read from source:

- `mobile_name`
- `mobile_version`
- `mobile_identifier`
- `mobile_identifier_source`
- `mobile_identifier_status`

The scanner resolves common indirection patterns before marking an identifier missing:

- Gradle placeholders such as `${appId}`
- Xcode settings such as `$(PRODUCT_BUNDLE_IDENTIFIER)`
- MSBuild `.props` values
- iOS plist references
- Android string resources for display names

The scanner does not invent identifiers. Empty identifiers usually mean the value is generated by CI/CD, secrets,
private variable groups, flavors, external files, or build logic that is not present in the scanned branch.

Placeholder versions such as `999.999.999` are suppressed so they are not mistaken for real release versions.

## Store Validation

Store lookup is optional:

```bash
appsec-scan-router \
  --provider azure-devops \
  --org FabrikamCloud \
  --out-dir reports \
  --store-lookup \
  --store-country US
```

When enabled:

- Apple lookup checks public App Store metadata by bundle identifier
- Google Play lookup checks the public app details page by package identifier
- Validation fields return `TRUE` when a public listing is found and `FALSE` otherwise
- Private, internal, unlisted, region-restricted, and console-only apps may not validate through public lookup

Store validation is enrichment, not source-of-truth ownership. Public listings can differ from source metadata.

## Performance

Start with:

```bash
appsec-scan-router \
  --provider azure-devops \
  --org FabrikamCloud \
  --out-dir reports \
  --min-confidence medium \
  --max-workers 8 \
  --branch-workers 16 \
  --content-workers 16
```

For very large organizations:

```bash
appsec-scan-router \
  --provider github-enterprise \
  --base-url https://github.fabrikam.example/api/v3 \
  --org FabrikamCloud \
  --out-dir reports \
  --min-confidence medium \
  --activity-mode latest \
  --max-workers 12 \
  --branch-workers 32 \
  --content-workers 32
```

Concurrency is split across repository preparation, branch scans, and selected-file fetches. Increase workers only
while the provider is responding cleanly. Reduce them if you see throttling, timeouts, or elevated transient errors.

Contributor extraction happens only after a branch passes mobile detection. Use `--activity-mode latest` for the
fastest inventory pass. Use `--activity-mode contributors` when the full contributor column matters more than scan
time.

Leave `--store-lookup` off for the fastest scan.

## CLI Reference

| Option | Default | Description |
| --- | --- | --- |
| `--provider` | `azure-devops` | `azure-devops` or `github-enterprise` |
| `--org` | required | Azure DevOps organization or GitHub owner |
| `--project` | all | Azure DevOps project or GitHub repository name |
| `--repo` | none | GitHub repository name; alias for `--project` |
| `--base-url` | env | GitHub Enterprise API URL |
| `--pat` | env | Provider token; prefer `ADO_PAT`, `GITHUB_TOKEN`, or `GHE_TOKEN` |
| `--out-dir` | current directory | Output directory |
| `--out-prefix` | `appsec_scan_router` | Output filename prefix |
| `--max-workers` | `8` | Concurrent repository preparation tasks |
| `--branch-workers` | `16` | Concurrent branch scans |
| `--content-workers` | `16` | Concurrent selected-file fetches |
| `--max-commits-per-repo` | `0` | Commit limit per matched branch; `0` means all available history |
| `--timeout` | `30` | Provider HTTP timeout in seconds |
| `--min-confidence` | `low` | `low`, `medium`, or `high` |
| `--branch-age-days` | `90` | Active/older worksheet cutoff |
| `--activity-mode` | `contributors` | `contributors` or `latest` |
| `--store-lookup` | disabled | Enable public app store enrichment |
| `--store-country` | `US` | Two-letter store country code |
| `--store-timeout` | `15` | Store lookup timeout in seconds |
| `--verbose` | disabled | Debug logging |

## Outputs

Reports are created when the scan starts and updated as apps are detected:

- `appsec_scan_router.csv`
- `appsec_scan_router.json`
- `appsec_scan_router.xlsx`

The workbook contains:

- `Active 90d`
- `Older 90d`

Changing `--branch-age-days` changes the sheet names, for example `Active 60d` and `Older 60d`.

Core fields:

| Field | Description |
| --- | --- |
| `project` | Azure DevOps project or GitHub owner |
| `repo_name` | Repository name |
| `branch_name` | Branch scanned |
| `branch_last_updated` | Latest commit timestamp seen by the scanner |
| `branch_age_bucket` | Active or older worksheet bucket |
| `web_url` | Repository URL |
| `mobile_name` | Best-effort app display name |
| `mobile_version` | Best-effort app version |
| `mobile_identifier` | Best-effort bundle/package identifier |
| `mobile_identifier_source` | Source family where the identifier was found |
| `mobile_identifier_status` | `found` or `missing_from_scanned_files` |
| `contributing_developers` | Semicolon-separated unique commit authors |
| `last_updated` | Same value as `branch_last_updated`, retained for compatibility |
| `confidence` | Detection confidence |
| `score` | Weighted evidence score |
| `categories` | Semicolon-separated category names |
| `category_*` | Excel-filter-friendly `TRUE` or `FALSE` columns |
| `detection_evidence` | JSON evidence details |

Store fields:

| Field | Description |
| --- | --- |
| `store_lookup_status` | Aggregate lookup status |
| `store_validation_passed` | `TRUE` when every requested store validation passes |
| `store_platforms` | Stores where a public listing was found |
| `apple_app_store_name` | Public Apple App Store app name |
| `apple_app_store_identifier` | Bundle identifier returned by Apple |
| `apple_app_store_url` | Public Apple App Store URL |
| `apple_app_store_version` | Public Apple App Store version |
| `apple_app_store_last_updated` | Public Apple App Store release/update timestamp |
| `apple_app_store_validation_passed` | Apple validation result |
| `apple_app_store_lookup_status` | Apple lookup status |
| `google_play_name` | Public Google Play app name |
| `google_play_identifier` | Google Play package identifier checked |
| `google_play_url` | Public Google Play URL |
| `google_play_version` | Best-effort public page version |
| `google_play_last_updated` | Best-effort public page update date |
| `google_play_validation_passed` | Google Play validation result |
| `google_play_lookup_status` | Google Play lookup status |

## SDK

```python
from pathlib import Path

from appsec_scan_router import ScanConfig, scan_to_reports

config = ScanConfig(
    provider="github-enterprise",
    base_url="https://github.fabrikam.example/api/v3",
    org="FabrikamCloud",
    pat="your-token",
    project=None,
    out_dir=Path("reports"),
    out_prefix="appsec_scan_router",
    max_workers=8,
    branch_workers=16,
    content_workers=16,
    max_commits_per_repo=2000,
    timeout_seconds=30,
    min_confidence="medium",
    branch_age_days=90,
    activity_mode="contributors",
    store_lookup=True,
    store_country="US",
    store_timeout_seconds=15,
)

results, csv_path, json_path, xlsx_path = scan_to_reports(config)
```

Object-oriented usage:

```python
from appsec_scan_router import AppSecScanRouter

router = AppSecScanRouter(config)
results, csv_path, json_path, xlsx_path = router.scan_to_reports()
```

Stream rows into another process:

```python
from appsec_scan_router import scan

def handle_row(row):
    print(row["project"], row["repo_name"], row["branch_name"], row["mobile_identifier"])

rows = scan(config, on_result=handle_row)
```

Legacy imports and commands remain available for compatibility:

```bash
mobile-app-inventory-tracer --org FabrikamCloud --out-dir reports
ado-mobile-scanner --org FabrikamCloud --out-dir reports
```

New integrations should import `appsec_scan_router`.

## Project Layout

```text
appsec_scan_router/
  activity.py
  azure.py
  cli.py
  constants.py
  detection.py
  github.py
  metadata.py
  models.py
  reports.py
  scanner.py
  sdk.py
  store_lookup.py
  utils.py
mobile_scanner/
ado_mobile_scanner.py
mobile_app_inventory_tracer.py
tests/
Dockerfile
```

## Test

```bash
python -m unittest discover -s tests
python -m compileall ado_mobile_scanner.py mobile_app_inventory_tracer.py appsec_scan_router mobile_scanner tests
```

## Security Notes

- Use read-only tokens
- Scope tokens to the smallest practical organization, project, or repository set
- Do not commit generated reports if they contain internal names or contributor emails
- The scanner does not clone repositories
- The scanner fetches only allow-listed source and configuration files
- Docker runs as a non-root user

## License

AppSec Scan Router is released under the MIT License. See [LICENSE](LICENSE).
