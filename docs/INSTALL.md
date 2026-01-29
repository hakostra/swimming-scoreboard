# Windows Packaging & Install Guide

This guide explains how to build a standalone Windows package using
PyInstaller and run it on any Windows machine.

## Prerequisites (build machine)
- Windows 10/11
- Python 3.10+ (3.11 recommended)

## 1) Set up a build environment
Open PowerShell in the project folder and run:

- Create and activate a virtual environment:
  - `python -m venv .venv`
  - `\.\.venv\Scripts\Activate.ps1`

- Install dependencies:
  - `python -m pip install --upgrade pip`
  - `python -m pip install -r requirements.txt`
  - `python -m pip install pyinstaller`

## 2) Build the standalone package
From the project root:

- `pyinstaller --noconfirm --clean swimming-scoreboard.spec`

This produces:
- `dist/SwimmingScoreboard/SwimmingScoreboard.exe`
- A `dist/SwimmingScoreboard/` folder containing the full runtime.

## 3) Create a portable ZIP
Zip the entire `dist/SwimmingScoreboard/` folder and distribute it.
The ZIP can be extracted and run on any Windows machine without Python.

## 4) Run on the target machine
1. Extract the ZIP.
2. Run `SwimmingScoreboard.exe` from the extracted folder.
3. Open a browser on the same machine and go to:
   - `http://localhost:8000/scoreboard`
   - `http://localhost:8000/control`

