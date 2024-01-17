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

## TODO
* Nothing right now :)