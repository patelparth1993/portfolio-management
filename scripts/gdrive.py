"""
Google Drive integration using a service account (works in CI / GitHub Actions).

Setup (one-time):
  1. Go to console.cloud.google.com → create a project → enable Google Drive API
  2. Create a Service Account → download JSON key
  3. Share your Google Drive statements folder with the service account email
  4. Add the JSON key contents as GitHub secret: GDRIVE_SERVICE_ACCOUNT_JSON
  5. Add your Drive folder ID as GitHub secret: GDRIVE_FOLDER_ID
     (folder ID is the long string in the Drive URL after /folders/)

Local testing:
  Set env vars GDRIVE_SERVICE_ACCOUNT_JSON and GDRIVE_FOLDER_ID before running.
"""
import io
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
STATEMENTS_BASE = ROOT / "statements"


def _get_service(credentials_json: str):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_service_account_info(
        json.loads(credentials_json),
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=creds)


def _detect_institution(filename: str) -> str:
    name_lower = filename.lower()
    if "wealthsimple" in name_lower or "ws" in name_lower:
        return "wealthsimple"
    if "manulife" in name_lower or "ml" in name_lower:
        return "manulife"
    return "wealthsimple"  # default


def download_new_statements(processed_hashes: set) -> list[Path]:
    """
    Download PDFs from Google Drive that haven't been processed yet.
    Reads credentials from environment variables set by GitHub Actions secrets.
    Returns list of downloaded file paths.
    """
    import hashlib
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

    service = _get_service(credentials_json)

    # List all PDFs in the folder (recursively search subfolders too)
    all_files = _list_pdfs_recursive(service, folder_id)
    if not all_files:
        print("  No PDFs found in Google Drive folder.")
        return []

    downloaded = []
    for file_meta in all_files:
        name = file_meta["name"]
        file_id = file_meta["id"]
        institution = _detect_institution(name)
        dest_dir = STATEMENTS_BASE / institution
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / name

        # Download to temp to check hash
        print(f"  Checking: {name}")
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        content = fh.getvalue()
        h = hashlib.md5(content).hexdigest()
        if h in processed_hashes:
            print(f"  Already processed, skipping: {name}")
            continue

        with open(dest_path, "wb") as f:
            f.write(content)
        print(f"  Downloaded: {name} → {institution}/")
        downloaded.append(dest_path)

    print(f"  {len(downloaded)} new file(s) downloaded from Google Drive.")
    return downloaded


def _list_pdfs_recursive(service, folder_id: str) -> list[dict]:
    """List all PDFs in a folder and its subfolders."""
    results = []
    query = f"'{folder_id}' in parents and trashed=false"
    response = service.files().list(
        q=query,
        fields="files(id, name, mimeType)",
    ).execute()

    for f in response.get("files", []):
        if f["mimeType"] == "application/pdf":
            results.append(f)
        elif f["mimeType"] == "application/vnd.google-apps.folder":
            results.extend(_list_pdfs_recursive(service, f["id"]))

    return results
