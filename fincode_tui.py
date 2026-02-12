#!/usr/bin/env python3
"""Textual TUI entry point for FinCode."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure we can import from the root directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from components.app import FinCodeApp

def main():
    # Load environment variables
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    
    # Run the app
    app = FinCodeApp()
    app.run()

if __name__ == "__main__":
    main()
