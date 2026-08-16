# CloudFlix

CloudFlix is a multi-tier movie discovery platform built with an Apache-hosted HTML/CSS/JavaScript frontend, a Flask API, MariaDB, TMDB integration, personal watchlists, reviews, and cached AI review summaries. The original AWS environment was decommissioned to avoid ongoing charges.

## Repository status

The core frontend and backend were recovered. Missing authentication pages and stylesheets were reconstructed from surviving snippets and the existing HTML class structure. Reconstructed files are functional approximations, not guaranteed byte-for-byte copies of the originals.

## Structure

- `frontend/` — static pages, styling, and browser-side behavior
- `backend/` — Flask API and dependencies
- `database/` — recovered and inferred MariaDB migrations
- `docs/` — architecture documentation and images

## Local setup

1. Create a MariaDB database and run the SQL scripts in numeric order.
2. Copy `.env.example` to `.env` and enter your credentials.
3. Install backend dependencies with `pip install -r backend/requirements.txt`.
4. Run `python backend/app.py`.
5. Serve `frontend/` from a local web server or Apache.

## Security note

Do not commit a populated `.env` file. The recovered backend was sanitized to remove old infrastructure addresses and credentials.
