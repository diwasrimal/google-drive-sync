import sys
import argparse
import os.path
import io
import mimetypes
import sqlite3
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
import iso8601

parser = argparse.ArgumentParser(
    prog=sys.argv[0],
    description="Syncing tool for google drive",
    epilog="Author: Diwas Rimal, License: MIT",
)
parser.add_argument("remotepath")
parser.add_argument("localpath")
parser.add_argument(choices=["fetch", "push"], dest="action")
parser.add_argument(
    "--export-pdf",
    action="store_true",
    dest="use_always_pdf",
    help="Export files like google docs, slides as pdf while fetching",
)
args = parser.parse_args()

DB_FILE = "./db.sqlite3"

# Scopes required by out application
# https://developers.google.com/drive/api/guides/api-specific-auth#drive-scopes
# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.metadata",
]

MIMES = {
    "gfolder": "application/vnd.google-apps.folder",
    "gdocs": "application/vnd.google-apps.document",
    "gsheets": "application/vnd.google-apps.spreadsheet",
    "gslides": "application/vnd.google-apps.presentation",
    "gshortcut": "application/vnd.google-apps.shortcut",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "txt": "text/plain",
}

# File extensions to which google workspace files like docs, spreadsheets, slides
# are exported. Used when EXPORT_ALWAYS_PDF is false
EXPORT_EXTENSIONS = {
    MIMES["gdocs"]: "docx",
    MIMES["gsheets"]: "xlsx",
    MIMES["gslides"]: "pptx",
}
EXPORT_MIMES = EXPORT_EXTENSIONS.keys()
EXPORT_ALWAYS_PDF = args.use_always_pdf

# Filenames (or foldernames) listed in a file named .gsyncignore
# will be ignored while pushing local changes
IGNORE_SPECIFIER = ".gsyncignore"
ALWAYS_IGNORE = {".DS_Store", IGNORE_SPECIFIER}


def main():
    remotepath = args.remotepath.rstrip("/")
    localpath = os.path.expanduser(args.localpath.rstrip("/"))
    action = args.action

    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)
    file_service = service.files()

    remote_folder = find_remote_folder(file_service, remotepath)
    if remote_folder is None:
        return

    if action == "fetch":
        print(f"Fetching from {remotepath}")
        fetch(file_service, remote_folder, localpath)

    elif action == "push":
        if not os.path.exists(localpath):
            print(f"Invalid localpath: {localpath}")
            return
        print(f"Pushing from {localpath}")
        push(file_service, remote_folder, localpath)


def fetch(file_service, remote_folder, localpath):
    """Downloads remote changes"""
    remote_files = list_remote_folder(file_service, remote_folder)

    if not remote_files:
        print(f"{remote_folder['name']} is empty")
        return

    # Make local dir if missing
    if not os.path.exists(localpath):
        print(f"Making folder '{localpath}'")
        os.makedirs(localpath)

    db = get_database_connection(DB_FILE)

    # Fetch files in depth-first manner
    for file in remote_files:
        name = file["name"]
        mime = file["mimeType"]

        # Resolve shortcuts and append to list
        if mime == MIMES["gshortcut"]:
            resolved_file = resolve_remote_shortcut(file_service, file)
            remote_files.append(resolved_file)
            continue

        if mime == MIMES["gfolder"]:
            sub_folder = file
            sub_localpath = f"{localpath}/{name}"
            fetch(file_service, sub_folder, sub_localpath)
            continue

        # Download or export file based on its mime
        should_export, exp_ext, exp_mime = get_export_info(mime)
        dstname = f"{name}.{exp_ext}" if should_export else name
        dstpath = f"{localpath}/{dstname}"
        if not os.path.exists(dstpath):
            should_download = True
        else:
            should_download = remote_modification(file) > local_modification(dstpath)

        if should_download:
            if _ := download_file(file_service, file, dstpath, should_export, exp_mime):
                folder_id = remote_folder["id"]
                file_id = file["id"]
                record_download(
                    db, folder_id, file_id, name, mime, dstname, should_export
                )
                last_modified = to_epoch(remote_modification(file))
                os.utime(dstpath, (last_modified, last_modified))

    db.close()


def push(file_service, remote_folder, localpath):
    """Uploads local changes to remote folder"""
    if remote_folder["mimeType"] == MIMES["gshortcut"]:
        print(f"Skipping shortcut '{localpath}' -> '{remote_folder['name']}'")
        return

    remote_files = {}
    for file in list_remote_folder(file_service, remote_folder):
        remote_files[file["name"]] = file

    remote_filenames = {f["name"] for f in remote_files.values()}
    local_filenames = set(os.listdir(localpath))
    ignored_filenames = find_ignored_files(localpath)

    # Get details of exports done on previous fetch from database
    db = get_database_connection(DB_FILE)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    prev_exports = cur.execute(
        "SELECT * FROM downloads WHERE exported = 1 AND parent_id = ?",
        [remote_folder["id"]],
    ).fetchall()

    for srcname in local_filenames:
        srcpath = f"{localpath}/{srcname}"
        lmodification = local_modification(srcpath)

        # Skip ignored files
        if srcname in ignored_filenames or srcname in ALWAYS_IGNORE:
            print(f"Ignoring {srcpath}")
            continue

        # Recursively push folders
        if os.path.isdir(srcpath):
            sub_localpath = srcpath
            if srcname in remote_filenames:
                subfolder = remote_files[srcname]
            else:
                print(f"Creating remote folder for {srcpath}")
                subfolder = file_service.create(
                    body={
                        "name": srcname,
                        "mimeType": MIMES["gfolder"],
                        "parents": [remote_folder["id"]],
                        "modifiedTime": to_rfc3339(lmodification),
                    },
                ).execute()

            push(file_service, subfolder, sub_localpath)
            continue

        # Determine if the file should be imported to docs, slides, etc format
        # For this, we look into previous export records and
        imp_mime = None
        imp_name = None
        for export in prev_exports:
            if export["local_name"] == srcname:
                imp_mime = export["remote_mime"]
                imp_name = export["remote_name"]

        is_inside_drive = srcname in remote_filenames or imp_name in remote_filenames
        if is_inside_drive:
            remote_file = remote_files[imp_name or srcname]
            if lmodification > remote_modification(remote_file):
                file = update_file(
                    file_service,
                    srcpath,
                    remote_file,
                    to_rfc3339(lmodification),
                )
        else:
            file = upload_file(
                file_service,
                srcpath,
                remote_folder,
                imp_name,
                imp_mime,
                to_rfc3339(lmodification),
            )

    db.close()


def find_ignored_files(localpath) -> set[str]:
    ignorefile = f"{localpath}/{IGNORE_SPECIFIER}"
    return (
        set(open(ignorefile).read().splitlines())
        if os.path.exists(ignorefile)
        else set()
    )


def find_remote_folder(file_service, path):
    """Finds remote folder using remote path"""
    folders = path.strip("/").split("/")

    # Start following folder path from root
    curr_folder = {"id": "root"}
    for i, folder_name in enumerate(folders):
        query = f"'{curr_folder['id']}' in parents and mimeType='{MIMES['gfolder']}'"
        files = (
            file_service.list(q=query, fields="files(id,name,mimeType)")
            .execute()
            .get("files", [])
        )
        for file in files:
            if file["name"] == folder_name and file["mimeType"] == MIMES["gfolder"]:
                curr_folder = file
                break
        else:
            print(f"Folder '{folder_name}' not found in '{'/'.join(folders[:i])}'.")
            return None

    return curr_folder


def list_remote_folder(file_service, folder):
    fields = "files(id, name, mimeType, modifiedTime, shortcutDetails/targetId)"
    return (
        file_service.list(
            q=f"'{folder['id']}' in parents and trashed = false", fields=fields
        )
        .execute()
        .get("files", [])
    )


def resolve_remote_shortcut(file_service, file):
    return file_service.get(fileId=file["shortcutDetails"]["targetId"]).execute()


def get_export_info(mime):
    if mime in EXPORT_MIMES:
        exp_ext = "pdf" if EXPORT_ALWAYS_PDF else EXPORT_EXTENSIONS[mime]
        exp_mime = MIMES[exp_ext]
        return True, exp_ext, exp_mime
    else:
        return False, None, None


def remote_modification(file) -> datetime:
    return iso8601.parse_date(file["modifiedTime"])


def local_modification(filepath) -> datetime:
    mtime = os.stat(filepath).st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).replace(microsecond=0)


def to_epoch(date: datetime) -> float:
    return date.timestamp()


def to_rfc3339(date: datetime) -> str:
    return date.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_credentials():
    creds = None

    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return creds


def get_database_connection(dbfile):
    if os.path.exists(dbfile):
        return sqlite3.connect(dbfile)

    print(f"Making database '{dbfile}'")
    with open(dbfile, "w") as _:
        pass

    db = sqlite3.connect(dbfile)
    cur = db.cursor()
    cur.execute(
        """
        CREATE TABLE downloads (
            file_id NUMERIC PRIMARY KEY NOT NULL,
            parent_id NUMERIC NOT NULL,
            remote_name TEXT NOT NULL,
            remote_mime TEXT NOT NULL,
            local_name TEXT NOT NULL,
            exported INT NOT NULL
        )
        """
    )
    return db


def download_file(
    file_service, file, dstpath, should_export=False, exp_mime=None
) -> bool:
    if should_export:
        print(f"Exporting '{file['name']}' -> '{dstpath}'")
        request = file_service.export_media(fileId=file["id"], mimeType=exp_mime)
    else:
        print(f"Downloading '{file['name']}' -> '{dstpath}'")
        request = file_service.get_media(fileId=file["id"])

    try:
        f = io.BytesIO()
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        with open(dstpath, "wb") as outfile:
            outfile.write(f.getbuffer())
        return True
    except Exception as err:
        print(err)
        return False


def record_download(
    db, parent_id, file_id, remote_name, remote_mime, local_name, is_exported
):
    cur = db.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO
        downloads(file_id, parent_id, remote_name, remote_mime, local_name, exported)
        VALUES(?, ?, ?, ?, ?, ?)""",
        [file_id, parent_id, remote_name, remote_mime, local_name, is_exported],
    )
    db.commit()


def upload_file(
    file_service, localpath, remote_folder, imp_name, imp_mime, modified_date
):
    """Uploads file on localpath to remote folder on drive"""
    localname = localpath.split("/")[-1]
    remotename = imp_name or localname
    localmime = mimetypes.guess_type(localname)[0] or "text/plain"
    metadata = {}
    metadata["parents"] = [remote_folder["id"]]
    metadata["name"] = remotename
    metadata["modifiedTime"] = modified_date
    if imp_mime:
        metadata["mimeType"] = imp_mime or localmime

    try:
        print(f"Uploading '{localpath}' -> '{remotename}'")
        media = MediaFileUpload(localpath, mimetype=localmime, resumable=True)
        file_service.create(
            body=metadata,
            media_body=media,
        ).execute()
    except Exception as err:
        print(err)


def update_file(file_service, localpath, remote_file, modified_date):
    localname = localpath.split("/")[-1]
    localmime = mimetypes.guess_type(localname)[0] or "text/plain"
    metadata = {"name": localname, "modifiedTime": modified_date}
    try:
        print(f"Updating '{localpath}' -> '{remote_file['name']}'")
        media = MediaFileUpload(localpath, mimetype=localmime, resumable=True)
        file_service.update(
            fileId=remote_file["id"],
            body=metadata,
            media_body=media,
        ).execute()
    except Exception as err:
        print(err)


if __name__ == "__main__":
    main()
