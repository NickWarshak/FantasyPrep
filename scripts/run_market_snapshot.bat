@echo off
cd /d "%~dp0.."
python -m fantasyprep.market.snapshot --year 2026 >> data\snapshots\snapshot_log.txt 2>&1
