import sys
import argparse
import os.path
import io
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
import iso8601

parser = argparse.ArgumentParser(
    prog=sys.argv[0], description="Syncing tool for google drive"
)
parser.add_argument("remotepath")
parser.add_argument("localpath")
parser.add_argument(choices=["fetch", "push"], dest="action")
args = parser.parse_args()

# Scopes required by out application
# https://developers.google.com/drive/api/guides/api-specific-auth#drive-scopes
# If modifying these scopes, delete the file token.json.
SCOPES = [
    # "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
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
EXPORT_ALWAYS_PDF = True


def main():
    remotepath = args.remotepath.rstrip("/")
    localpath = args.localpath.rstrip("/")
    action = args.action

    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)
    file_service = service.files()

    if action == "fetch":
        remote_folder = find_remote_folder(file_service, remotepath)
        if remote_folder is None:
            return
        print(f"Fetching from {remotepath}")
        fetch(file_service, remote_folder, localpath)

    elif action == "push":
        push(file_service, remotepath, localpath)


def fetch(file_service, remote_folder, localpath):
    """Downloads remote changes"""
    folder_id = remote_folder["id"]

    fields = "files(id, name, mimeType, modifiedTime, shortcutDetails/targetId)"
    remote_files = (
        file_service.list(q=f"'{folder_id}' in parents", fields=fields)
        .execute()
        .get("files", [])
    )
    if not remote_files:
        print(f"{remote_folder} is empty")
        return

    # Make local dir if missing
    if not os.path.exists(localpath):
        print(f"Making folder '{localpath}'")
        os.makedirs(localpath)

    local_filenames = set(os.listdir(localpath))

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

        dst = f"{localpath}/{name}"
        if name in local_filenames:
            remote_mtime = iso8601.parse_date(file["modifiedTime"])
            local_mtime = datetime.fromtimestamp(os.stat(dst).st_mtime, tz=timezone.utc)
            if remote_mtime > local_mtime:
                download_file(file_service, file, dst)
        else:
            download_file(file_service, file, dst)


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


def resolve_remote_shortcut(file_service, file):
    return file_service.get(fileId=file["shortcutDetails"]["targetId"]).execute()


def push(file_service, remotepath, localpath):
    """Uploads local changes to remote folder"""
    print("Pushing...")


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


def download_file(file_service, file, dst):
    try:
        mime = file["mimeType"]
        if mime == MIMES["gfolder"]:
            raise Exception("Downloading folder not supported")

        if mime in EXPORT_MIMES:
            exp_ext = "pdf" if EXPORT_ALWAYS_PDF else EXPORT_EXTENSIONS[mime]
            exp_mime = MIMES[exp_ext]
            dst = f"{dst}.{exp_ext}"
            request = file_service.export_media(fileId=file["id"], mimeType=exp_mime)
        else:
            request = file_service.get_media(fileId=file["id"])

        print(f"Downloading '{file['name']}' -> '{dst}'")
        f = io.BytesIO()
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        with open(dst, "wb") as outfile:
            outfile.write(f.getbuffer())

    except HttpError as err:
        print(err)
    except Exception as err:
        print(err)


if __name__ == "__main__":
    main()
