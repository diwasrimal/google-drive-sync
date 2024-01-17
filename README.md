# Google Drive Sync

This is a simple cli application that allows syncing files in Google Drive
to local storage and vice versa, using Google Drive API.

## Requirements
* Python (Recent)
* A Google Cloud Project with
    * Google Drive API enabled
    * OAuth enabled

Client secrets are to be created by the user of this app. Save the downloadable
OAuth credentials provided to you as `credentials.json` in the project directory

## Setup
Quick setup for unix like systems
```
git clone https://github.com/diwasrimal/google-drive-sync.git
cd google-drive-sync
python3 -m venv venv
. venv/bin/activate
pip3 install -r requirements.txt
python3 gsync.py /Documents/backup ~/Documents/backup fetch
```

## Help
```console
$ python3 gsync.py -h
usage: gsync.py [-h] [--export-pdf] remotepath localpath {fetch,push}

Syncing tool for google drive

positional arguments:
  remotepath
  localpath
  {fetch,push}

options:
  -h, --help    show this help message and exit
  --export-pdf  Export files like google docs, slides as pdf while fetching

Author: Diwas Rimal, License: MIT
```

## Working
* `remotepath` is the path of your drive folder. 'My Drive' is considered root (/).
So a folder My Drive/Documents/exam is just /Documents/exam
* `localpath` is the relative or absolute path of local folders.
* `fetch` downloads new or modifies outdated local files.
* `push` uploads or updates local files to drive.

* A special file `.gsyncignore` can be used to ignore files that you don't want to
upload to drive. For example the file can have
```
.DS_Store
code
secret.json
```

The listed name matches files or folders, so 'code' could ignore a folder named 'code'.
Listing filepaths won't work.


## TODO
* Nothing right now :)