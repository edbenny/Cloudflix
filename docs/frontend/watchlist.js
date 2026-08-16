// ─────────────────────────────────────────────────────────────────
// CloudFlix shared watchlist client
// Used by index.html, mylist.html, profile.html, movie.html, browse.html
// ─────────────────────────────────────────────────────────────────

window.Watchlist = (function () {

  function isLoggedIn() {
    return !!localStorage.getItem("cloudflix_user");
  }

  function requireLogin() {
    if (!isLoggedIn()) {
      alert("Please sign in to use your watchlist.");
      window.location.href = "login.html";
      return false;
    }
    return true;
  }

  // GET the user's full watchlist
  async function getAll() {
    const res = await fetch("/api/watchlist", { credentials: "include" });
    if (!res.ok) {
      if (res.status === 401) return [];
      throw new Error("HTTP " + res.status);
    }
    return res.json();
  }

  // Quick "is X in my list?" check
  async function contains(tmdbId) {
    if (!isLoggedIn()) return false;
    try {
      const res = await fetch(`/api/watchlist/contains/${tmdbId}`, { credentials: "include" });
      if (!res.ok) return false;
      const data = await res.json();
      return !!data.in_list;
    } catch (err) {
      console.error(err);
      return false;
    }
  }

  // Add a movie. movie = { tmdb_id, movie_title, poster_path, release_year, vote_average }
  async function add(movie) {
    if (!requireLogin()) return null;
    try {
      const res = await fetch("/api/watchlist", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(movie)
      });
      const data = await res.json();
      if (!res.ok) {
        if (res.status === 409) return { already: true };
        alert(data.error || "Failed to add to watchlist");
        return null;
      }
      return data;
    } catch (err) {
      console.error(err);
      alert("Network error — could not add to watchlist.");
      return null;
    }
  }

  // Remove by TMDB id
  async function remove(tmdbId) {
    if (!requireLogin()) return false;
    try {
      const res = await fetch(`/api/watchlist/${tmdbId}`, {
        method: "DELETE",
        credentials: "include"
      });
      const data = await res.json();
      if (!res.ok) {
        // 404 just means already gone — treat as success
        if (res.status !== 404) alert(data.error || "Failed to remove");
        return res.status === 404;
      }
      return true;
    } catch (err) {
      console.error(err);
      alert("Network error — could not remove.");
      return false;
    }
  }

  // Helpers used by every page that renders watchlist cards
  function posterUrl(poster_path) {
    return poster_path
      ? "https://image.tmdb.org/t/p/w500" + poster_path
      : "";
  }

  function esc(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  return { isLoggedIn, getAll, contains, add, remove, posterUrl, esc };
})();
