@echo off
REM One command that makes a fresh clone runnable. See START_HERE.md.
REM uv brings its own Python, so nothing else has to be installed first.
where uv >nul 2>nul || (echo uv is not installed. Get it from https://docs.astral.sh/uv/ and run this again, in a new terminal. & exit /b 1)
uv run --no-project --python 3.12 "%~dp0setup.py" %*
