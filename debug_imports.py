#!/usr/bin/env python3
"""Debug script to find where imports hang."""
import sys
sys.stdout.reconfigure(line_buffering=True)  # Force immediate output

print("1. Starting...", flush=True)

print("2. Importing pathlib...", flush=True)
from pathlib import Path

print("3. Importing typing...", flush=True)
from typing import Optional, Literal

print("4. Importing pydantic Field...", flush=True)
from pydantic import Field

print("5. Importing pydantic_settings...", flush=True)
from pydantic_settings import BaseSettings

print("6. About to define Settings class...", flush=True)

class Settings(BaseSettings):
    app_name: str = "Test"
    class Config:
        env_file = ".env"

print("7. About to instantiate Settings...", flush=True)
settings = Settings()
print(f"8. Done! app_name={settings.app_name}", flush=True)

print("9. Now testing real config import...", flush=True)
from config import settings as real_settings
print(f"10. Real config loaded! tier={real_settings.tier}", flush=True)

print("11. Testing models import...", flush=True)
from models import Base
print("12. Models imported!", flush=True)

print("13. ALL IMPORTS SUCCESSFUL!", flush=True)
