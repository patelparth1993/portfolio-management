"""
Google Drive integration using a service account (works in CI / GitHub Actions).

Drive folder structure expected:
  <GDRIVE_FOLDER_ID>/
    wealthsimple/
      H947126K9CAD_TFSA/          ← self-managed TFSA
      HF4253745CAD_RRSP/          ← self-managed RRSP
      WZ0969XK4CAD_TFSA_PORTFOLIO/ ← managed TFSA
    manulife/
      *.pdf              ← quarterly statements (each covers all Manulife accounts)

Subdirectory names use the pattern {ACCOUNT_NUMBER}_{ACCOUNT_TYPE} so that ingest.py
can extract the account type as a hint for the parser (overrides PDF text detection).

Files are mirrored locally to statements/<institution>/<subfolder>/<filename>.
This preserves per-account subdirectory structure and prevents filename collisions.

Setup (one-time):
  1. Go to console.cloud.google.com → create a project → enable Google Drive API
  2. Create a Service Account → download JSON key
  3. Share your Google Drive statements folder with the service account email
  4. Add the JSON key contents as GitHub secret: GDRIVE_SERVICE_ACCOUNT_JSON
  5. Add your Drive folder ID as GitHub secret: GDRIVE_FOLDER_ID
"""
import hashlib
import io
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
STATEMENTS_BASE = ROOT / "statements"

# Maps Drive folder name (lowercased) → local institution directory name
INSTITUTION_ALIASES = {
    "wealthsimple": "wealthsimple",
    "wealth simple": "wealthsimple",
    "ws": "wealthsimple",
    "manulife": "manulife",
    "ml": "manulife",
}


def _get_service(credentials_json: str):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_info(
        json.loads(credentials_json),
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds)


def _institution_from_path(folder_path: str) -> str | None:
    """Detect institution from any component of the folder path."""
    for part in folder_path.lower().replace("/", " ").split():
        if part in INSTITUTION_ALIASES:
            return INSTITUTION_ALIASES[part]
    return None


def _list_pdfs_recursive(service, folder_id: str, folder_path: str = "") -> list[dict]:
    """
    Recursively list PDFs, attaching folder_path to each result so the caller
    can reconstruct the local directory structure and detect institution.
    """
    results = []
    page_token = None
    while True:
        response = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token,
        ).execute()

        for f in response.get("files", []):
            if f["mimeType"] == "application/pdf":
                f["folder_path"] = folder_path
                results.append(f)
            elif f["mimeType"] == "application/vnd.google-apps.folder":
                child_path = f"{folder_path}/{f['name']}" if folder_path else f["name"]
                results.extend(_list_pdfs_recursive(service, f["id"], child_path))

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return results


def _local_dest(file_meta: dict) -> tuple[str, Path] | tuple[None, None]:
    """
    Determine (institution, local_path) for a Drive file.
    Returns (None, None) if the institution cannot be determined.
    """
    folder_path = file_meta.get("folder_path", "")
    name = file_meta["name"]

    institution = _institution_from_path(folder_path)
    if not institution:
        institution = _institution_from_path(name)
    if not institution:
        return None, None

    # Preserve sub-path below the institution folder (e.g. "H947126K9CAD")
    parts = [p for p in folder_path.split("/") if p]
    # Drop the institution-level folder from the path
    sub_parts = []
    found = False
    for part in parts:
        if not found and part.lower() in INSTITUTION_ALIASES:
            found = True
            continue
        if found:
            sub_parts.append(part)

    dest_dir = STATEMENTS_BASE / institution
    if sub_parts:
        dest_dir = dest_dir / Path(*sub_parts)
    dest_dir.mkdir(parents=True, exist_ok=True)
    return institution, dest_dir / name


def download_new_statements(processed_hashes: set) -> list[Path]:
    """
    Download PDFs from Google Drive that haven't been processed yet.
    Returns list of downloaded file paths.
    """
    from googleapiclient.http import MediaIoBaseDownload

    credentials_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    folder_id = os.environ.get("GDRIVE_FOLDER_ID")

    if not credentials_json:
        raise RuntimeError(
            "GDRIVE_SERVICE_ACCOUNT_JSON environment variable not set.\n"
            "Set it to the contents of your service account JSON key file.\n"
            "In GitHub Actions: add it as a repository secret."
        )
    if not folder_id:
        raise RuntimeError(
            "GDRIVE_FOLDER_ID environment variable not set.\n"
            "Set it to the Google Drive folder ID (from the folder URL)."
        )

    # Support both: JSON contents (GitHub Actions) or a file path (local dev)
    if credentials_json.strip().startswith("{"):
        creds_data = credentials_json
    else:
        cred_path = Path(credentials_json)
        if not cred_path.is_absolute():
            cred_path = ROOT / cred_path
        if not cred_path.exists():
            raise RuntimeError(f"Service account file not found: {cred_path}")
        creds_data = cred_path.read_text()

    service = _get_service(creds_data)

    all_files = _list_pdfs_recursive(service, folder_id)
    if not all_files:
        print("  No PDFs found in Google Drive folder.")
        return []

    downloaded = []
    for file_meta in all_files:
        name = file_meta["name"]
        folder_path = file_meta.get("folder_path", "")

        # Skip CRM2 charges/compensation reports; download investment performance reports
        if name.upper().startswith("CRM2"):
            if "INVESTMENT_PERFORMANCE" not in name.upper():
                continue

        institution, dest_path = _local_dest(file_meta)
        if not institution:
            print(f"  Skipping (unknown institution): {folder_path}/{name}")
            continue

        # Download to memory, check hash before writing
        request = service.files().get_media(fileId=file_meta["id"])
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        content = fh.getvalue()
        h = hashlib.md5(content).hexdigest()
        if h in processed_hashes:
            print(f"  Already processed, skipping: {folder_path}/{name}")
            continue

        dest_path.write_bytes(content)
        print(f"  Downloaded: {folder_path}/{name} → statements/{institution}/")
        downloaded.append(dest_path)

    print(f"  {len(downloaded)} new file(s) downloaded from Google Drive.")
    return downloaded
