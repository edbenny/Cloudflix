-- ─────────────────────────────────────────────────────────────────
-- CloudFlix migration 003: create watchlist table
-- Run once against the movie_app database.
-- ─────────────────────────────────────────────────────────────────

USE movie_app;

CREATE TABLE watchlist (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  user_id      INT NOT NULL,
  tmdb_id      INT NOT NULL,
  movie_title  VARCHAR(255) NOT NULL,
  poster_path  VARCHAR(255),
  release_year SMALLINT,
  vote_average DECIMAL(3,1),
  added_at     DATETIME DEFAULT CURRENT_TIMESTAMP,

  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,

  -- Prevents the same movie being added twice by the same user
  UNIQUE KEY uniq_user_movie (user_id, tmdb_id),

  -- Speeds up listing one user's watchlist
  INDEX idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Verify
SHOW CREATE TABLE watchlist;
SELECT COUNT(*) AS row_count FROM watchlist;

-- ─────────────────────────────────────────────────────────────────
-- Useful commands (not part of the migration):
-- ─────────────────────────────────────────────────────────────────
--
-- List all watchlist items with usernames:
--   SELECT u.username, w.movie_title, w.release_year, w.added_at
--   FROM watchlist w JOIN users u ON u.id = w.user_id
--   ORDER BY w.added_at DESC;
--
-- Clear one user's watchlist:
--   DELETE FROM watchlist WHERE user_id = ?;
--
-- Drop the table (start over):
--   DROP TABLE watchlist;
