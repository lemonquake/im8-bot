#!/bin/bash

# Change working directory to script directory (crucial for macOS double-click)
cd "$(dirname "$0")"

# Clear terminal screen
clear

# Set terminal window title (if supported by terminal)
echo -ne "\033]0;IM8 Bot\007"

echo "==================================="
echo "    Starting IM8 Health"
echo "==================================="

# Check for python3, fallback to python
if command -v python3 >/dev/null 2>&1; then
    python3 bot.py
elif command -v python >/dev/null 2>&1; then
    python bot.py
else
    echo "Error: Python is not installed or not in PATH."
    exit 1
fi

echo ""
echo "Press any key to continue..."
read -n 1 -s -r
