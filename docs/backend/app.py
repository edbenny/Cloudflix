import os
import pymysql
import bcrypt
import socket
import re
import requests
from openai import OpenAI
from flask import Flask, request, jsonify, session
from flask_cors import CORS

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-only-change-me')

app.config.update(
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=False,   # change to True after you add HTTPS
    SESSION_COOKIE_HTTPONLY=True
)
CORS(app,
    origins=[
        *[origin.strip() for origin in os.environ.get("CORS_ORIGINS", "http://localhost").split(",") if origin.strip()]
    ],
    supports_credentials=True
)

# ── TMDB CONFIG ──
TMDB_TOKEN = os.getenv("TMDB_BEARER_TOKEN")
TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_TIMEOUT = 10  # seconds


# ── OPENAI CONFIG ──
# Reads OPENAI_API_KEY from the environment (set in .env on each backend EC2).
# Using gpt-4o-mini — cheap (~$0.00015 per summary), fast, plenty good for this.
OPENAI_MODEL = "gpt-4o-mini"
_openai_client = None
def get_openai():
    """Lazy singleton for OpenAI client. Returns None if no API key."""
    global _openai_client
    if _openai_client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            return None
        _openai_client = OpenAI(api_key=key)
    return _openai_client


def tmdb_headers():
    return {
        "accept": "application/json",
        "Authorization": f"Bearer {TMDB_TOKEN}"
    }


def tmdb_get(path, params=None):
    """Forward a GET to TMDB. Returns (json_dict, http_status)."""
    if not TMDB_TOKEN:
        return {"error": "TMDB token not configured on server"}, 500
    try:
        r = requests.get(
            f"{TMDB_BASE}{path}",
            headers=tmdb_headers(),
            params=params,
            timeout=TMDB_TIMEOUT
        )
        return r.json(), r.status_code
    except requests.RequestException as e:
        return {"error": f"TMDB request failed: {str(e)}"}, 502


# ── INPUT SANITIZATION ──
def sanitize_input(value):
    if not isinstance(value, str):
        return None
    value = value.replace('\x00', '')   # remove null bytes
    value = value.strip()
    if not value or len(value) > 150:    # reject empty or too long
        return None
    return value


def is_valid_username(username):
    # Only allow letters, numbers, underscores, hyphens — no SQL special chars
    return bool(re.match(r'^[a-zA-Z0-9_\-]+$', username))


def get_db():
    return pymysql.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        user=os.environ.get('DB_USER', 'cloudflix'),
        password=os.environ.get('DB_PASSWORD', ''),
        database=os.environ.get('DB_NAME', 'movie_app'),
        cursorclass=pymysql.cursors.DictCursor
    )


# ─────────────────────────────────────────────────────────────────
# ROLE HELPERS
# All role checks happen server-side using the session. Frontend
# only uses role for UX (show/hide buttons). Never trust the client.
# ─────────────────────────────────────────────────────────────────

def current_user():
    """Return dict {user_id, username, role} from session, or None."""
    if 'user_id' not in session:
        return None
    return {
        'user_id':  session['user_id'],
        'username': session['username'],
        'role':     session.get('role', 'user'),  # safe default
    }


def is_admin():
    """True if the logged-in user has admin role."""
    return session.get('role') == 'admin'


@app.route("/health")
def health():
    return "OK", 200


@app.route("/whoami")
def whoami():
    return jsonify({"server": socket.gethostname()}), 200


# ── REGISTER ──
@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    username = sanitize_input(username)
    password = sanitize_input(password)

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    if not is_valid_username(username):
        return jsonify({"error": "Username can only contain letters, numbers, _ and -"}), 400

    try:
        db = get_db()
        cursor = db.cursor()

        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            return jsonify({"error": "An account with this username already exists"}), 409

        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        # New users always start with the default role 'user'.
        # Admins are promoted manually via SQL — never via this endpoint.
        cursor.execute(
            "INSERT INTO users (username, password) VALUES (%s, %s)",
            (username, hashed.decode('utf-8'))
        )
        db.commit()

        return jsonify({
            "message": "Account created successfully",
            "username": username,
            "role": "user"
        }), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        db.close()


# ── LOGIN ──
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    username = sanitize_input(username)
    password = sanitize_input(password)

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    if not is_valid_username(username):
        return jsonify({"error": "Invalid username or password"}), 401

    try:
        db = get_db()
        cursor = db.cursor()

        cursor.execute(
            "SELECT * FROM users WHERE username = %s",
            (username,)
        )
        user = cursor.fetchone()

        if not user:
            return jsonify({"error": "Invalid username or password"}), 401

        password_matches = bcrypt.checkpw(
            password.encode('utf-8'),
            user['password'].encode('utf-8')
        )

        if password_matches:
            # Save role in the session so all later requests know
            # who you are AND what you can do without re-querying.
            session['user_id']  = user['id']
            session['username'] = user['username']
            session['role']     = user.get('role', 'user')

            return jsonify({
                "message":  "Login successful",
                "username": user['username'],
                "role":     session['role']
            }), 200
        else:
            return jsonify({"error": "Invalid username or password"}), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        db.close()


# ── GET CURRENT USER ──
@app.route("/me", methods=["GET"])
def me():
    user = current_user()
    if not user:
        return jsonify({"error": "Not logged in"}), 401
    return jsonify(user), 200


# ── LOGOUT ──
@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True}), 200


# ─────────────────────────────────────────────────────────────────
# TMDB PROXY ROUTES
# ─────────────────────────────────────────────────────────────────

@app.route("/api/tmdb/trending", methods=["GET"])
def tmdb_trending():
    data, status = tmdb_get("/trending/movie/day", params={"language": "en-US"})
    return jsonify(data), status


@app.route("/api/tmdb/discover", methods=["GET"])
def tmdb_discover():
    """
    Discover/popular movies. Accepts:
      ?page=N
      &genre=ID                (TMDB genre id, e.g. 28 for Action)
      &year=YYYY               (primary release year)
      &rating_gte=N            (vote_average >= N)
      &sort=popularity|rating|newest|oldest|alpha
    """
    page = request.args.get("page", "1")
    if not page.isdigit():
        return jsonify({"error": "page must be a positive integer"}), 400

    params = {
        "include_adult": "false",
        "language": "en-US",
        "page": page,
    }

    # Genre (TMDB expects a numeric id, optionally comma-separated)
    genre = request.args.get("genre")
    if genre and re.match(r'^\d+(,\d+)*$', genre):
        params["with_genres"] = genre

    # Year — TMDB filters by primary_release_year
    year = request.args.get("year")
    if year and re.match(r'^\d{4}$', year):
        params["primary_release_year"] = year

    # Minimum rating (vote_average >= N)
    rating_gte = request.args.get("rating_gte")
    if rating_gte:
        try:
            r = float(rating_gte)
            if 0 <= r <= 10:
                params["vote_average.gte"] = r
                # Filter out movies with too few votes to be meaningful
                params["vote_count.gte"] = 50
        except ValueError:
            pass

    # Sorting
    sort_map = {
        "popularity": "popularity.desc",
        "rating":     "vote_average.desc",
        "newest":     "primary_release_date.desc",
        "oldest":     "primary_release_date.asc",
        "alpha":      "original_title.asc",
    }
    sort = request.args.get("sort", "popularity")
    params["sort_by"] = sort_map.get(sort, "popularity.desc")

    # When sorting by rating, also require min vote count so 10/10 obscure films
    # don't push real popular movies off the page
    if sort == "rating" and "vote_count.gte" not in params:
        params["vote_count.gte"] = 200

    # Keep the family-friendly cap from before, BUT skip it when the user
    # explicitly filters — if you ask for Horror you probably don't want
    # only PG-13 horror returned
    if not (genre or year or rating_gte):
        params["certification_country"] = "US"
        params["certification.lte"] = "PG-13"

    data, status = tmdb_get("/discover/movie", params=params)
    return jsonify(data), status


@app.route("/api/tmdb/search", methods=["GET"])
def tmdb_search():
    """
    Text search for movies by title. Accepts:
      ?query=...   (required, what the user typed)
      &page=N
      &year=YYYY   (optional, restricts to that release year)
    """
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"error": "query parameter is required"}), 400
    if len(query) > 200:
        return jsonify({"error": "query too long"}), 400

    page = request.args.get("page", "1")
    if not page.isdigit():
        return jsonify({"error": "page must be a positive integer"}), 400

    params = {
        "include_adult": "false",
        "language": "en-US",
        "query": query,
        "page": page,
    }
    year = request.args.get("year")
    if year and re.match(r'^\d{4}$', year):
        params["primary_release_year"] = year

    data, status = tmdb_get("/search/movie", params=params)
    return jsonify(data), status


@app.route("/api/tmdb/movie/<int:movie_id>", methods=["GET"])
def tmdb_movie(movie_id):
    data, status = tmdb_get(f"/movie/{movie_id}")
    return jsonify(data), status


@app.route("/api/tmdb/movie/<int:movie_id>/credits", methods=["GET"])
def tmdb_movie_credits(movie_id):
    data, status = tmdb_get(f"/movie/{movie_id}/credits", params={"language": "en-US"})
    return jsonify(data), status


@app.route("/api/tmdb/movie/<int:movie_id>/release_dates", methods=["GET"])
def tmdb_movie_release_dates(movie_id):
    data, status = tmdb_get(f"/movie/{movie_id}/release_dates")
    return jsonify(data), status


@app.route("/api/tmdb/movie/<int:movie_id>/videos", methods=["GET"])
def tmdb_movie_videos(movie_id):
    """Trailers, teasers, featurettes (mostly YouTube)."""
    data, status = tmdb_get(f"/movie/{movie_id}/videos", params={"language": "en-US"})
    return jsonify(data), status


@app.route("/api/tmdb/movie/<int:movie_id>/watch_providers", methods=["GET"])
def tmdb_movie_watch_providers(movie_id):
    """Where to stream/rent/buy. Data attribution: 'Powered by JustWatch'."""
    data, status = tmdb_get(f"/movie/{movie_id}/watch/providers")
    return jsonify(data), status


@app.route("/api/tmdb/movie/<int:movie_id>/recommendations", methods=["GET"])
def tmdb_movie_recommendations(movie_id):
    """Similar/recommended movies (TMDB curated)."""
    data, status = tmdb_get(f"/movie/{movie_id}/recommendations", params={"language": "en-US"})
    return jsonify(data), status


# ─────────────────────────────────────────────────────────────────
# REVIEWS
# Permission model:
#   - Anyone can READ reviews (logged in or not).
#   - Logged-in users can CREATE their own.
#   - The OWNER of a review can UPDATE or DELETE it.
#   - ADMINS can UPDATE or DELETE any review.
# ─────────────────────────────────────────────────────────────────

def _validate_rating(rating):
    """Return int 1..5 or None if invalid."""
    try:
        r = int(rating)
    except (TypeError, ValueError):
        return None
    if r < 1 or r > 5:
        return None
    return r


def _validate_body(body):
    """Return cleaned text, or None if invalid."""
    if not isinstance(body, str):
        return None
    body = body.strip()
    if len(body) < 1 or len(body) > 2000:
        return None
    return body


def _validate_tmdb_id(tmdb_id):
    try:
        n = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _serialize_review(row):
    """Shape a DB row into JSON the frontend can use."""
    return {
        "id":          row["id"],
        "user_id":     row["user_id"],
        "username":    row.get("username"),       # joined from users
        "tmdb_id":     row["tmdb_id"],
        "movie_title": row["movie_title"],
        "rating":      row["rating"],
        "body":        row["body"],
        "created_at":  row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at":  row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@app.route("/api/reviews", methods=["GET"])
def list_reviews():
    """List reviews for a movie. Public. Requires ?tmdb_id=N."""
    tmdb_id = _validate_tmdb_id(request.args.get("tmdb_id"))
    if tmdb_id is None:
        return jsonify({"error": "tmdb_id query parameter is required"}), 400

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT r.id, r.user_id, u.username, r.tmdb_id, r.movie_title,
                   r.rating, r.body, r.created_at, r.updated_at
            FROM reviews r
            JOIN users u ON u.id = r.user_id
            WHERE r.tmdb_id = %s
            ORDER BY r.created_at DESC
            """,
            (tmdb_id,)
        )
        rows = cursor.fetchall()
        return jsonify([_serialize_review(r) for r in rows]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


@app.route("/api/reviews/mine", methods=["GET"])
def list_my_reviews():
    """List the current user's reviews. Requires login."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT r.id, r.user_id, u.username, r.tmdb_id, r.movie_title,
                   r.rating, r.body, r.created_at, r.updated_at
            FROM reviews r
            JOIN users u ON u.id = r.user_id
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
            """,
            (user["user_id"],)
        )
        rows = cursor.fetchall()
        return jsonify([_serialize_review(r) for r in rows]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


@app.route("/api/reviews", methods=["POST"])
def create_review():
    """Create a review for a movie. Requires login. One per user per movie."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    data = request.get_json() or {}
    tmdb_id     = _validate_tmdb_id(data.get("tmdb_id"))
    movie_title = sanitize_input(data.get("movie_title"))
    rating      = _validate_rating(data.get("rating"))
    body        = _validate_body(data.get("body"))

    if tmdb_id is None:
        return jsonify({"error": "tmdb_id is required"}), 400
    if not movie_title:
        return jsonify({"error": "movie_title is required"}), 400
    if rating is None:
        return jsonify({"error": "rating must be an integer 1-5"}), 400
    if body is None:
        return jsonify({"error": "body must be 1-2000 characters"}), 400

    try:
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO reviews (user_id, tmdb_id, movie_title, rating, body)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user["user_id"], tmdb_id, movie_title, rating, body)
            )
            db.commit()
        except pymysql.err.IntegrityError:
            # UNIQUE (user_id, tmdb_id) — they already reviewed this movie
            return jsonify({"error": "You have already reviewed this movie"}), 409

        new_id = cursor.lastrowid
        # Read back so the response includes timestamps + username
        cursor.execute(
            """
            SELECT r.id, r.user_id, u.username, r.tmdb_id, r.movie_title,
                   r.rating, r.body, r.created_at, r.updated_at
            FROM reviews r JOIN users u ON u.id = r.user_id
            WHERE r.id = %s
            """,
            (new_id,)
        )
        row = cursor.fetchone()
        return jsonify(_serialize_review(row)), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


@app.route("/api/reviews/<int:review_id>", methods=["PUT"])
def update_review(review_id):
    """Update a review. Owner OR admin only."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    data = request.get_json() or {}
    rating = _validate_rating(data.get("rating"))
    body   = _validate_body(data.get("body"))

    # Either rating or body must be provided (or both)
    if rating is None and body is None:
        return jsonify({"error": "Provide rating (1-5) and/or body (1-2000 chars)"}), 400

    try:
        db = get_db()
        cursor = db.cursor()

        # Fetch the existing row to check ownership
        cursor.execute(
            "SELECT id, user_id FROM reviews WHERE id = %s",
            (review_id,)
        )
        review = cursor.fetchone()
        if not review:
            return jsonify({"error": "Review not found"}), 404

        # Authorization: must be the author OR an admin
        if review["user_id"] != user["user_id"] and not is_admin():
            return jsonify({"error": "Not allowed"}), 403

        # Build update dynamically depending on which fields were provided
        sets, params = [], []
        if rating is not None:
            sets.append("rating = %s")
            params.append(rating)
        if body is not None:
            sets.append("body = %s")
            params.append(body)
        params.append(review_id)

        cursor.execute(
            f"UPDATE reviews SET {', '.join(sets)} WHERE id = %s",
            tuple(params)
        )
        db.commit()

        # Return the updated row
        cursor.execute(
            """
            SELECT r.id, r.user_id, u.username, r.tmdb_id, r.movie_title,
                   r.rating, r.body, r.created_at, r.updated_at
            FROM reviews r JOIN users u ON u.id = r.user_id
            WHERE r.id = %s
            """,
            (review_id,)
        )
        row = cursor.fetchone()
        return jsonify(_serialize_review(row)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


@app.route("/api/reviews/<int:review_id>", methods=["DELETE"])
def delete_review(review_id):
    """Delete a review. Owner OR admin only."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    try:
        db = get_db()
        cursor = db.cursor()

        cursor.execute(
            "SELECT id, user_id FROM reviews WHERE id = %s",
            (review_id,)
        )
        review = cursor.fetchone()
        if not review:
            return jsonify({"error": "Review not found"}), 404

        if review["user_id"] != user["user_id"] and not is_admin():
            return jsonify({"error": "Not allowed"}), 403

        cursor.execute("DELETE FROM reviews WHERE id = %s", (review_id,))
        db.commit()
        return jsonify({"success": True, "deleted_id": review_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


# ─────────────────────────────────────────────────────────────────
# WATCHLIST
# Permission model: a user can only see and modify their own watchlist.
# Watchlists are private — admins do NOT get a special view of others'.
# ─────────────────────────────────────────────────────────────────

def _serialize_watchlist_item(row):
    return {
        "id":           row["id"],
        "tmdb_id":      row["tmdb_id"],
        "movie_title":  row["movie_title"],
        "poster_path":  row.get("poster_path"),
        "release_year": row.get("release_year"),
        "vote_average": float(row["vote_average"]) if row.get("vote_average") is not None else None,
        "added_at":     row["added_at"].isoformat() if row.get("added_at") else None,
    }


@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    """Return the logged-in user's watchlist, most recently added first."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """
            SELECT id, tmdb_id, movie_title, poster_path, release_year,
                   vote_average, added_at
            FROM watchlist
            WHERE user_id = %s
            ORDER BY added_at DESC
            """,
            (user["user_id"],)
        )
        rows = cursor.fetchall()
        return jsonify([_serialize_watchlist_item(r) for r in rows]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


@app.route("/api/watchlist", methods=["POST"])
def add_to_watchlist():
    """Add a movie to the logged-in user's watchlist."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    data = request.get_json() or {}
    tmdb_id      = _validate_tmdb_id(data.get("tmdb_id"))
    movie_title  = sanitize_input(data.get("movie_title"))
    poster_path  = data.get("poster_path")  # optional, may be None
    release_year = data.get("release_year")
    vote_average = data.get("vote_average")

    if tmdb_id is None:
        return jsonify({"error": "tmdb_id is required"}), 400
    if not movie_title:
        return jsonify({"error": "movie_title is required"}), 400

    # Light validation on the optional fields
    if poster_path is not None:
        if not isinstance(poster_path, str) or len(poster_path) > 255:
            poster_path = None
    if release_year is not None:
        try:
            release_year = int(release_year)
            if release_year < 1850 or release_year > 2200:
                release_year = None
        except (TypeError, ValueError):
            release_year = None
    if vote_average is not None:
        try:
            vote_average = float(vote_average)
            if vote_average < 0 or vote_average > 10:
                vote_average = None
        except (TypeError, ValueError):
            vote_average = None

    try:
        db = get_db()
        cursor = db.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO watchlist
                    (user_id, tmdb_id, movie_title, poster_path,
                     release_year, vote_average)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (user["user_id"], tmdb_id, movie_title, poster_path,
                 release_year, vote_average)
            )
            db.commit()
        except pymysql.err.IntegrityError:
            # UNIQUE (user_id, tmdb_id) — already in their watchlist
            return jsonify({"error": "Already in your watchlist", "tmdb_id": tmdb_id}), 409

        new_id = cursor.lastrowid
        cursor.execute(
            """
            SELECT id, tmdb_id, movie_title, poster_path, release_year,
                   vote_average, added_at
            FROM watchlist WHERE id = %s
            """,
            (new_id,)
        )
        row = cursor.fetchone()
        return jsonify(_serialize_watchlist_item(row)), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


@app.route("/api/watchlist/<int:tmdb_id>", methods=["DELETE"])
def remove_from_watchlist(tmdb_id):
    """Remove a movie from the logged-in user's watchlist (by TMDB id)."""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "DELETE FROM watchlist WHERE user_id = %s AND tmdb_id = %s",
            (user["user_id"], tmdb_id)
        )
        db.commit()

        if cursor.rowcount == 0:
            return jsonify({"error": "Not in your watchlist"}), 404
        return jsonify({"success": True, "tmdb_id": tmdb_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


@app.route("/api/watchlist/contains/<int:tmdb_id>", methods=["GET"])
def watchlist_contains(tmdb_id):
    """Quick check: is this movie in the logged-in user's watchlist?"""
    user = current_user()
    if not user:
        return jsonify({"error": "Login required"}), 401

    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "SELECT 1 FROM watchlist WHERE user_id = %s AND tmdb_id = %s LIMIT 1",
            (user["user_id"], tmdb_id)
        )
        in_list = cursor.fetchone() is not None
        return jsonify({"in_list": in_list, "tmdb_id": tmdb_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


# ─────────────────────────────────────────────────────────────────
# AI REVIEW SUMMARIES
# Generates a 2-4 sentence summary of a movie's reviews using OpenAI.
# Summaries are cached in the ai_summaries table so we don't re-bill on
# every page view. We regenerate when the total review count has grown by
# at least REGENERATE_DELTA since the last cached summary.
# ─────────────────────────────────────────────────────────────────

# Minimum reviews required to generate any summary at all
MIN_REVIEWS_FOR_SUMMARY = 3

# If new reviews appear, regenerate once we cross this many additional reviews
REGENERATE_DELTA = 5

# How many of each source to feed the LLM (cap so we don't blow context window)
MAX_REVIEWS_PER_SOURCE = 12


def _fetch_cloudflix_reviews(cursor, tmdb_id):
    """Return list of {rating, body} from our DB for this movie."""
    cursor.execute(
        "SELECT rating, body FROM reviews WHERE tmdb_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (tmdb_id, MAX_REVIEWS_PER_SOURCE)
    )
    return cursor.fetchall() or []


def _fetch_tmdb_reviews(tmdb_id):
    """Return list of TMDB reviews (their author/content)."""
    data, status = tmdb_get(f"/movie/{tmdb_id}/reviews", params={"language": "en-US"})
    if status != 200 or not isinstance(data, dict):
        return []
    return (data.get("results") or [])[:MAX_REVIEWS_PER_SOURCE]


def _build_summary_prompt(movie_title, cloudflix_reviews, tmdb_reviews):
    """Assemble the user-prompt text for the LLM."""
    parts = []
    parts.append(f"Movie: {movie_title}\n")

    if cloudflix_reviews:
        parts.append("\nReviews from this site (with star ratings 1-5):")
        for i, r in enumerate(cloudflix_reviews, 1):
            # Truncate per-review to avoid token blowup
            body = (r.get("body") or "")[:1500]
            parts.append(f"\n[{i}] ({r.get('rating')}/5) {body}")

    if tmdb_reviews:
        parts.append("\n\nReviews from TMDB:")
        for i, r in enumerate(tmdb_reviews, 1):
            body = (r.get("content") or "")[:1500]
            author = r.get("author") or "anonymous"
            parts.append(f"\n[{i}] (by {author}) {body}")

    parts.append(
        "\n\nWrite a concise summary of the consensus in these reviews. "
        "Cover: overall sentiment, the most common praises, and the most common "
        "criticisms. 2 to 4 sentences. Plain text only — no markdown, no headers, "
        "no bullet points. Do not invent details that aren't supported by the reviews."
    )
    return "".join(parts)


def _generate_summary(movie_title, cloudflix_reviews, tmdb_reviews):
    """Call OpenAI. Returns summary string or raises on failure."""
    client = get_openai()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY not configured on server")

    prompt = _build_summary_prompt(movie_title, cloudflix_reviews, tmdb_reviews)

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You summarize movie reviews neutrally and accurately. "
                    "Never invent facts. Never include the movie's title in the "
                    "summary. Never use markdown."
                )
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=250,
        temperature=0.4,
    )
    return resp.choices[0].message.content.strip()


@app.route("/api/tmdb/movie/<int:movie_id>/reviews", methods=["GET"])
def tmdb_movie_reviews(movie_id):
    """Pass-through proxy for TMDB's public reviews on a movie."""
    data, status = tmdb_get(f"/movie/{movie_id}/reviews", params={"language": "en-US"})
    return jsonify(data), status


@app.route("/api/movies/<int:tmdb_id>/ai_summary", methods=["GET"])
def get_ai_summary(tmdb_id):
    """
    Return a cached or freshly-generated AI summary for a movie.
    Public — no auth required (matches the rest of the movie page).

    Response shape:
      { "summary": "...", "review_count": N, "cached": bool }
      or { "error": "...", "review_count": N }
    """
    movie_title = request.args.get("title", "").strip()
    # Optional title — not strictly required, but helps the prompt
    if len(movie_title) > 255:
        movie_title = movie_title[:255]

    try:
        db = get_db()
        cursor = db.cursor()

        # Step 1: look up existing cached summary
        cursor.execute(
            "SELECT summary, review_count, generated_at FROM ai_summaries WHERE tmdb_id = %s",
            (tmdb_id,)
        )
        cached = cursor.fetchone()

        # Step 2: count how many reviews exist *right now*
        cloudflix_reviews = _fetch_cloudflix_reviews(cursor, tmdb_id)
        tmdb_reviews = _fetch_tmdb_reviews(tmdb_id)
        cf_n = len(cloudflix_reviews)
        td_n = len(tmdb_reviews)
        total = cf_n + td_n

        # Step 3: not enough reviews? Bail.
        if total < MIN_REVIEWS_FOR_SUMMARY:
            return jsonify({
                "summary": None,
                "review_count": total,
                "cached": False,
                "reason": "not_enough_reviews",
                "min_required": MIN_REVIEWS_FOR_SUMMARY,
            }), 200

        # Step 4: if we have a cached summary and review count hasn't grown
        # by REGENERATE_DELTA, return cached as-is. Cheap and fast path.
        if cached is not None:
            if total - cached["review_count"] < REGENERATE_DELTA:
                return jsonify({
                    "summary": cached["summary"],
                    "review_count": cached["review_count"],
                    "cached": True,
                }), 200

        # Step 5: generate a fresh summary (this is the only LLM call)
        try:
            summary = _generate_summary(movie_title or f"TMDB ID {tmdb_id}",
                                        cloudflix_reviews, tmdb_reviews)
        except Exception as e:
            # If LLM fails AND we have a stale cached summary, return that
            if cached is not None:
                return jsonify({
                    "summary": cached["summary"],
                    "review_count": cached["review_count"],
                    "cached": True,
                    "warning": "Used stale cache, fresh generation failed",
                }), 200
            return jsonify({"error": f"AI generation failed: {str(e)}"}), 502

        # Step 6: upsert into cache
        cursor.execute(
            """
            INSERT INTO ai_summaries
                (tmdb_id, summary, review_count, cloudflix_count, tmdb_count)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                summary         = VALUES(summary),
                review_count    = VALUES(review_count),
                cloudflix_count = VALUES(cloudflix_count),
                tmdb_count      = VALUES(tmdb_count)
            """,
            (tmdb_id, summary, total, cf_n, td_n)
        )
        db.commit()

        return jsonify({
            "summary": summary,
            "review_count": total,
            "cached": False,
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        db.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
