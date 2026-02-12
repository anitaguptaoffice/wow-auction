# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WoW Auction is a Python-based web application for World of Warcraft auction house data analysis and equipment upgrade calculations. It consists of a FastAPI backend with SQLite database and vanilla HTML/JavaScript frontend.

## Development Commands

### Install Dependencies
```bash
# Install UV package manager if not already installed
pip install uv

# Install project dependencies
uv sync
```

### Run Development Server
```bash
# Run the FastAPI development server with SSL on port 8000
python main.py

# Or run directly with uvicorn
uvicorn backend.api:app --host 0.0.0.0 --port 8000 --reload --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem
```

### Build and Run with Docker
```bash
# Build Docker image
docker build -t wow-auction .

# Run container
docker run -p 8000:8000 wow-auction
```

### Deploy Frontend to GitHub Pages
The frontend is automatically deployed to GitHub Pages when pushing to the main branch via GitHub Actions workflow (`.github/workflows/static.yml`).

## Architecture Overview

### Backend Structure (`backend/`)
- **`api.py`**: Main FastAPI application with authentication endpoints `/register`, `/token`, and auction data endpoint `/auction/{item_id}`
- **`auth.py`**: JWT authentication logic with bcrypt password hashing
- **`data_processor.py`**: Lua file parsing and auction data processing with file monitoring
- **`database.py`**: SQLAlchemy database configuration and session management
- **`models.py`**: User model and Pydantic schemas for request/response validation

### Frontend Structure (`frontend/`)
- **`index.html`**: Main auction house search interface
- **`script.js`**: Client-side logic for API interactions and JWT token handling
- **`style.css`**: Application styling
- **`upgrade_calculator.html`**: Equipment upgrade calculator for blue/green sand calculations

### Key Features
1. **User Authentication**: JWT-based with rate limiting (10 requests/minute for auction endpoint)
2. **Auction Data Processing**: Monitors and parses Lua auction data files in real-time
3. **Equipment Upgrade Calculator**: Calculates upgrade costs using blue and green sand materials
4. **Rate Limiting**: Implemented with slowapi library, IP-based limiting

### Data Flow
1. WoW addon exports auction data to Lua format (`data/auction.lua`)
2. Backend monitors file changes and parses auction data
3. Frontend requests data via authenticated API calls
4. Data is cached and served with rate limiting protection

### Database Schema
- **users** table: Stores user credentials with usage limits tracking

### Security Considerations
- Passwords hashed with bcrypt
- JWT tokens for API authentication
- Rate limiting on all endpoints
- CORS configured for all origins (development setting)
- SSL certificates required for HTTPS

## Common Development Tasks

### Adding New API Endpoints
1. Add route handler in `backend/api.py`
2. Create Pydantic models in `backend/models.py` if needed
3. Apply rate limiting decorator `@limiter.limit("10/minute")` if required
4. Test with curl or frontend integration

### Modifying Auction Data Processing
1. Update parsing logic in `backend/data_processor.py`
2. Modify caching strategy if needed
3. Ensure file monitoring handles new data formats

### Frontend Development
1. Edit files in `frontend/` directory
2. Test with local development server
3. Deploy via GitHub Pages workflow

## Important Notes

- Python 3.13+ required
- Uses UV package manager (not pip directly)
- SSL certificates required (`certs/key.pem` and `certs/cert.pem`)
- SQLite database file `wow-auction.db` created automatically
- Large Lua data file (49MB) in `data/auction.lua` - handle with care
- No automated tests currently configured
- No linting configuration present