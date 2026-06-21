# Mobile App Inventory Tracer

Mobile App Inventory Tracer is a branch-aware Azure DevOps scanner that identifies mobile applications across large Git estates, extracts app metadata, captures contributor and activity signals, and writes Excel-ready inventory reports as the scan runs.

It is designed for engineering, platform, security, and enterprise architecture teams that need a reliable inventory of mobile codebases without cloning every repository or depending on broad keyword search.

## Highlights

- Scans Azure DevOps projects, repositories, and every Git branch returned by refs
- Detects Android, iOS, Flutter, React Native, Expo, Ionic, Capacitor, Cordova, Xamarin, and .NET MAUI signals
- Parses structured manifests and project files instead of relying on naive keyword matching
- Extracts app name, version, bundle/package identifier, source of identifier, contributors, and latest branch activity
- Splits Excel output into active and older branch worksheets, defaulting to `Active 90d` and `Older 90d`
- Streams CSV and JSON rows while the scan is running
- Optionally enriches detected identifiers with public Apple App Store and Google Play metadata
- Runs as a Python package, CLI, importable library, or Docker container
- Keeps Azure DevOps access read-only and fetches only allow-listed metadata/configuration files

## How It Works

The scanner uses the Azure DevOps REST API to list projects, repositories, branches, tree items, selected file contents, and commit history. For each branch, it fetches only files that can provide strong mobile signals or metadata, such as:

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
- Azure pipeline YAML files

Detection is evidence-based. A repository branch is included when structured signals meet the configured confidence threshold. Generic `.csproj` files, generic `config.xml` files, and weak pipeline-only clues are not enough on their own to classify a branch as an app.

## What It Detects

| Category | Strong signals |
| --- | --- |
| Android | Android manifest, Gradle Android application plugin, `applicationId`, `namespace`, `versionName` |
| iOS | `Info.plist`, `InfoPlist.strings`, Xcode build settings, bundle identifiers, marketing version |
| Flutter | `pubspec.yaml` Flutter SDK dependency with native project layout |
| React Native / Expo | `package.json`, Expo config, native Android/iOS project layout |
| Ionic / Capacitor / Cordova | Capacitor config, Cordova widget config, Ionic config, package dependencies |
| Xamarin / MAUI | `.csproj`, `UseMaui`, mobile target frameworks, `ApplicationId` |
| Mobile pipelines | Mobile build/task evidence as supporting context |

## App Metadata Extraction

When possible, the scanner extracts:

- `mobile_name`, such as `Agsnap`
- `mobile_version`, such as `1.0.2`
- `mobile_identifier`, such as `com.pepsico.agsnap`
- `mobile_identifier_source`, such as `Info.plist`, `Gradle applicationId/namespace`, or `Xcode build settings`
- `mobile_identifier_status`, either `found` or `missing_from_scanned_files`

It resolves common indirection patterns before deciding that an identifier is missing:

- Gradle property placeholders such as `${appId}`
- Xcode build setting placeholders such as `$(PRODUCT_BUNDLE_IDENTIFIER)`
- MSBuild `.props` values
- iOS plist references
- Android string resources for display names

The scanner does not invent identifiers. If a repo generates identifiers from CI/CD variables, private variable groups, secrets, flavors, external files, or runtime build logic that is not present in Azure DevOps, the identifier is reported as missing.

## Store Enrichment

Store lookup is optional and disabled by default. Enable it with `--store-lookup`.

When enabled:

- Apple App Store lookup uses the detected bundle identifier against Apple public lookup data
- Google Play lookup checks the public app details page by package identifier
- Results are cached by identifier and platform during the scan
- Lookup runs only after a branch has already been classified as mobile

Google Play public lookup can confirm public listings, but it cannot see private/internal apps or Play Console-only listings. Authenticated Google Play Developer API support can be layered in separately for organizations that own the apps and can provide Android Publisher OAuth credentials.

## Installation

### Requirements

- Python 3.10 or newer
- Azure DevOps PAT with read access to Projects and Code
- Network access to `dev.azure.com`
- Optional network access to Apple and Google Play endpoints when `--store-lookup` is enabled

### Local Setup

```bash
git clone https://github.com/h0p3sf4ll/mobile-app-inventory-tracer.git
cd mobile-app-inventory-tracer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

Set your Azure DevOps token as an environment variable:

```bash
export ADO_PAT="your-token-here"
```

## Quick Start

Scan every project in an Azure DevOps organization:

```bash
mobile-app-inventory-tracer --org PepsiCoIT --out-dir reports
```

Scan one project:

```bash
mobile-app-inventory-tracer --org PepsiCoIT --project "Go_To_Market" --out-dir reports
```

Only include medium and high confidence matches:

```bash
mobile-app-inventory-tracer --org PepsiCoIT --out-dir reports --min-confidence medium
```

Fast profile for very large organizations:

```bash
mobile-app-inventory-tracer \
  --org PepsiCoIT \
  --out-dir reports \
  --min-confidence medium \
  --max-workers 12 \
  --branch-workers 32 \
  --content-workers 32 \
  --activity-mode latest
```

Enable public store enrichment:

```bash
mobile-app-inventory-tracer --org PepsiCoIT --out-dir reports --store-lookup --store-country US
```

The legacy command remains available for compatibility:

```bash
ado-mobile-scanner --org PepsiCoIT --out-dir reports
```

You can also run from source:

```bash
python mobile_app_inventory_tracer.py --org PepsiCoIT --out-dir reports
```

## Docker

Build the image:

```bash
docker build -t mobile-app-inventory-tracer .
```

Run a scan and write reports to a local directory:

```bash
mkdir -p reports
docker run --rm \
  -e ADO_PAT="$ADO_PAT" \
  -v "$PWD/reports:/reports" \
  mobile-app-inventory-tracer \
  --org PepsiCoIT \
  --out-dir /reports \
  --min-confidence medium
```

Run with public store enrichment:

```bash
docker run --rm \
  -e ADO_PAT="$ADO_PAT" \
  -v "$PWD/reports:/reports" \
  mobile-app-inventory-tracer \
  --org PepsiCoIT \
  --out-dir /reports \
  --store-lookup \
  --store-country US
```

The container runs as a non-root `scanner` user and writes to `/reports`.

## CLI Reference

| Option | Required | Default | Description |
| --- | --- | --- | --- |
| `--org` | Yes | | Azure DevOps organization name |
| `--project` | No | all projects | Project name to scan |
| `--pat` | No | `ADO_PAT` | Azure DevOps PAT; prefer the environment variable |
| `--out-dir` | No | current directory | Output directory |
| `--out-prefix` | No | `ado_mobile_repos` | Output filename prefix |
| `--max-workers` | No | `8` | Concurrent repository branch-listing requests |
| `--branch-workers` | No | `16` | Concurrent branch scans |
| `--content-workers` | No | `16` | Concurrent selected-file fetches |
| `--max-commits-per-repo` | No | `0` | Commit history limit per matched branch; `0` means all available history |
| `--timeout` | No | `30` | Azure DevOps HTTP timeout in seconds |
| `--min-confidence` | No | `low` | Minimum detection confidence: `low`, `medium`, or `high` |
| `--branch-age-days` | No | `90` | Active/older worksheet cutoff |
| `--activity-mode` | No | `contributors` | `contributors` walks configured commit history; `latest` only fetches the latest commit |
| `--store-lookup` | No | disabled | Enable public app store enrichment |
| `--store-country` | No | `US` | Two-letter public store country code |
| `--store-timeout` | No | `15` | Store lookup HTTP timeout in seconds |
| `--verbose` | No | disabled | Enable debug logging |

## Outputs

The scanner creates output files as soon as the run starts and appends matching rows as branches are detected:

- `ado_mobile_repos.csv`
- `ado_mobile_repos.json`
- `ado_mobile_repos.xlsx`

The Excel workbook includes:

- `Active 90d`: matched app branches changed within the active window
- `Older 90d`: matched app branches with no changes inside the active window

If you change `--branch-age-days`, worksheet names change accordingly, such as `Active 60d` and `Older 60d`.

## Output Schema

Core inventory fields:

| Field | Description |
| --- | --- |
| `project` | Azure DevOps project name |
| `repo_name` | Repository name |
| `branch_name` | Branch where the app was detected |
| `branch_last_updated` | Latest branch commit timestamp seen by the scanner |
| `branch_age_bucket` | Active/older age bucket |
| `web_url` | Azure DevOps repository URL |
| `mobile_name` | Best-effort app display name |
| `mobile_version` | Best-effort app version |
| `mobile_identifier` | Best-effort bundle/package identifier |
| `mobile_identifier_source` | Source family where the identifier was found |
| `mobile_identifier_status` | `found` or `missing_from_scanned_files` |
| `contributing_developers` | Semicolon-separated unique commit authors |
| `last_updated` | Same value as `branch_last_updated`, retained for compatibility |
| `confidence` | Detection confidence |
| `score` | Weighted evidence score |
| `categories` | Semicolon-separated matched category names |
| `category_*` | Excel-filter-friendly `TRUE` / `FALSE` columns |
| `detection_evidence` | JSON evidence details used for classification |

Store enrichment fields:

| Field | Description |
| --- | --- |
| `store_lookup_status` | Aggregate store lookup status |
| `store_platforms` | Stores where a public listing was found |
| `apple_app_store_name` | Public Apple App Store app name |
| `apple_app_store_identifier` | Bundle identifier returned by Apple |
| `apple_app_store_url` | Public Apple App Store URL |
| `apple_app_store_version` | Public Apple App Store version |
| `apple_app_store_last_updated` | Public Apple App Store release/update timestamp |
| `apple_app_store_lookup_status` | Apple lookup status |
| `google_play_name` | Public Google Play app name |
| `google_play_identifier` | Google Play package identifier checked |
| `google_play_url` | Public Google Play URL |
| `google_play_version` | Best-effort version from public page metadata |
| `google_play_last_updated` | Best-effort update date from public page metadata |
| `google_play_lookup_status` | Google Play public-page lookup status |

Store fields remain empty unless `--store-lookup` is enabled and a public listing is found.

## Library Usage

The scanner can be embedded in another Python application.

```python
from pathlib import Path

from mobile_scanner import ScanConfig, scan_to_reports

config = ScanConfig(
    org="PepsiCoIT",
    pat="your-token-here",
    project=None,
    out_dir=Path("reports"),
    out_prefix="ado_mobile_repos",
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

Use `scan(config)` to receive rows without writing reports:

```python
from mobile_scanner import scan

rows = scan(config)
```

Stream rows into another process:

```python
from mobile_scanner import scan

def handle_row(row):
    print(row["project"], row["repo_name"], row["branch_name"], row["mobile_identifier"])

rows = scan(config, on_result=handle_row)
```

## Project Layout

```text
ado_mobile_scanner.py              Compatibility wrapper
mobile_app_inventory_tracer.py     Source-run wrapper
mobile_scanner/
  activity.py                      Commit authors and last-updated extraction
  azure.py                         Azure DevOps REST client
  cli.py                           CLI argument parsing
  constants.py                     Shared constants and report schema
  detection.py                     Evidence-based mobile branch classification
  metadata.py                      App metadata extraction
  models.py                        Dataclasses and errors
  reports.py                       CSV, JSON, and Excel writers
  scanner.py                       Scan orchestration
  store_lookup.py                  Optional public app store enrichment
  utils.py                         Parsing and cleanup helpers
tests/                             Unit tests
Dockerfile                         Container definition
```

## Accuracy Notes

`mobile_identifier` can be empty when an app identifier is generated outside the files available to the scanner. Common causes include CI/CD variables, private variable groups, build flavors, environment-specific files, secrets, or app catalog packaging steps.

`mobile_name` can be empty when the display name is localized, generated, or declared only in native project files that are not present in the scanned branch.

`mobile_version` can be empty when versioning is generated by pipeline tasks, Gradle logic, Xcode build settings, or environment-specific files.

Placeholder versions such as `999.999.999` are treated as sentinel values and suppressed so they are not mistaken for release versions.

Store metadata is not the same as source metadata. App Store and Google Play values reflect public listing data where available; branch timestamps reflect repository activity.

## Performance Guidance

Start with:

```bash
mobile-app-inventory-tracer --org PepsiCoIT --out-dir reports --max-workers 8 --content-workers 16 --min-confidence medium
```

For very large organizations, the scanner uses three independent pools:

- `--max-workers` lists repository branches
- `--branch-workers` scans branches after they are listed
- `--content-workers` fetches selected manifest and configuration files

Increase concurrency only if Azure DevOps responds quickly and throttling is not observed. Reduce it if you see `429`, timeout, or transient service errors.

Contributor extraction happens only after a branch passes detection. Use `--max-commits-per-repo` if full commit history is too expensive for large estates.

Use `--activity-mode latest` for the fastest large-org inventory pass. It captures `last_updated` from the latest branch commit and leaves `contributing_developers` empty, avoiding full commit-history walks. Use `--activity-mode contributors` when the complete contributor column matters more than speed.

Store lookup is also performed only after detection. Leave `--store-lookup` off for the fastest inventory scan.

## Security

- Use a read-only Azure DevOps PAT scoped to the smallest practical set of projects and repositories
- Prefer `ADO_PAT` over `--pat` so tokens are not stored in shell history
- Do not commit generated reports if they may contain internal repository names or contributor emails
- The scanner does not clone repositories
- The scanner fetches only allow-listed source/configuration files needed for detection
- Docker runs as a non-root user

## Testing

```bash
python -m unittest discover -s tests
python -m compileall ado_mobile_scanner.py mobile_app_inventory_tracer.py mobile_scanner tests
```

## Contributing

Issues and pull requests are welcome. Useful contributions include:

- Additional structured metadata parsers
- Better evidence rules for mobile frameworks
- Authenticated Google Play Developer API enrichment
- Additional report formats
- Performance improvements for very large Azure DevOps organizations

Please include tests for parser, detection, and reporting changes.

## License

Mobile App Inventory Tracer is released under the MIT License. See [LICENSE](LICENSE).
