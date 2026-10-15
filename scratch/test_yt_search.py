import urllib.request
import urllib.parse
import re

def resolve_top_youtube_video(query: str) -> tuple[str | None, str | None, str]:
    encoded = urllib.parse.quote_plus(query.strip())
    fallback_url = f"https://www.youtube.com/results?search_query={encoded}"
    try:
        url = f"https://www.youtube.com/results?search_query={encoded}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # 1. Match videoId
        matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        if matches:
            video_id = matches[0]
            # 2. Match title near videoId
            title_pattern = rf'"videoId":"{video_id}".*?"title":\{{"runs":\[\{{"text":"(.*?)"\}}'
            t_match = re.search(title_pattern, html, re.DOTALL)
            if not t_match:
                # Alternate pattern
                t_match = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', html)
            title = t_match.group(1) if t_match else query.strip()
            # Clean title unicode escapes
            title = title.encode("utf-8").decode("unicode_escape", errors="ignore")
            return video_id, title, f"https://www.youtube.com/watch?v={video_id}"
    except Exception as exc:
        print("Search error:", exc)
    return None, None, fallback_url

if __name__ == "__main__":
    queries = ["psi song", "despacito", "lofi beats", "rick astley"]
    for q in queries:
        vid, title, url = resolve_top_youtube_video(q)
        print(f"[{q}] -> ID: {vid} | Title: {title} | URL: {url}")
