@echo off
rem Double-click to start. Leave this window open, then click Copy under ChatGPT replies.
rem Cards go into the disease subdeck that already exists in Anki (e.g. ...::Endocarditis).
rem New diseases get a new subdeck under PARENT below. Make a copy of this file per
rem ChatGPT project, e.g. "Watch Endocrinology.bat" with PARENT=Internal Medicine::Endocrinology and Metabolism.
set "PARENT=Internal Medicine::Cardiology"
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
%PY% gpt_to_anki.py --watch --parent "%PARENT%"
pause
