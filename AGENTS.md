# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Commands

- Start server: `uvicorn main:app --reload` (runs on http://localhost:8000)
- Install deps: `pip install -r requirements.txt`

## Project Structure

- `main.py` - FastAPI application entry point with health check endpoint
- `requirements.txt` - Python dependencies

## Endpoints

- `GET /` - Welcome message
- `GET /health` - Health check (returns 200 with status: healthy)

## Development Notes

- Uses Uvicorn with reload for development
- Standard FastAPI patterns - no custom utilities or non-standard patterns
