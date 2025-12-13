# hackathon/__init__.py
"""
Package root for the hackathon demo project.

This package contains the `backend` package with the FastAPI application
and `frontend` static files. The previous repository layout used an
intermediate `backup/` folder and a dynamic alias; that behaviour was
removed to keep the package layout simple and standard.

Usage (development):
    python -m uvicorn hackathon.backend.main:app --reload

You can also install the package in editable mode for a more reliable
import path during auto-reload:
    pip install -e .
"""

