import json
import os
import sys

if not os.path.exists("config.json"):
    print("Error: config.json not found! Please run 'python setup.py' first.")
    sys.exit(1)

with open("config.json", "r") as f:
    config = json.load(f)

PRINTER_IP = config.get("PRINTER_IP", "")
ACCESS_CODE = config.get("ACCESS_CODE", "")
SERIAL_NUMBER = config.get("SERIAL_NUMBER", "")