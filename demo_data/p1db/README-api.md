# P1 API: local setup and launch

This is a local, fictional-actor demo. `X-Actor-Id` is not authentication. Do not host it for shared use until P2 access controls exist.

## 1. Environment (run from the project folder)

Delete any `.venv` folder created earlier on another machine, then build a fresh one on this Mac:

    cd "2 - Clinical Supply & Expiration Tracker"
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements-api.txt

Versions are pinned in `requirements-api.txt`, including Uvicorn 0.54.0. Verified on Python 3.10.12 in a clean environment. Not yet verified on this Mac.

## 2. Create a demo database (new file only)

The loader refuses to overwrite an existing file.

    mkdir -p ~/clinic-demo
    cd demo_data/p1db
    ../../.venv/bin/python loader_p1.py ~/clinic-demo/demo.db

## 3. Launch

    P1_DB_PATH=~/clinic-demo/demo.db ../../.venv/bin/uvicorn api_p1:make_app --factory --host 127.0.0.1 --port 8000

Then open http://127.0.0.1:8000/docs in a browser.

The server refuses to start if the database is missing or not an initialized P1 database. It never creates a file.

## 4. Tests

    cd demo_data/p1db
    ../../.venv/bin/python -m unittest test_p1_db test_api_p1
