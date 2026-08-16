-- Reconstructed from backend queries; verify against the original deployment.
CREATE TABLE IF NOT EXISTS ai_summaries (
  tmdb_id INT PRIMARY KEY,
  summary TEXT NOT NULL,
  review_count INT NOT NULL DEFAULT 0,
  cloudflix_count INT NOT NULL DEFAULT 0,
  tmdb_count INT NOT NULL DEFAULT 0,
  generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
