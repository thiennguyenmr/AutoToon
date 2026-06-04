"""Crawl truyện từ vivutruyen2.net.

Site là WordPress (Yoast SEO). Trang truyện ở `{BASE_URL}/<slug>/`, danh sách
chương render tĩnh trong `<div class="list">` với mỗi link `<a class="chap-title">`
(sắp xếp giảm dần). Nội dung mỗi chương nằm trong `<div class="... reading">`
gồm các thẻ `<p>`.

Script chỉ dùng thư viện chuẩn của Python (không cần cài thêm gói).
"""

from __future__ import annotations

import argparse
import html
import re
import ssl
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE_URL = "https://vivutruyen2.net"
DEFAULT_SLUG = "chong-toi-phai-long-ban-than-toi"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Tắt verify SSL cho crawl HTML public (giống crawler cũ), không ảnh hưởng
# MinIO/vLLM (HTTP nội bộ).
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
try:
    _SSL_CTX.set_ciphers("DEFAULT:@SECLEVEL=0")
except ssl.SSLError:
    pass


def http_request(url: str, method: str = "GET", timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
        data=b"" if method == "POST" else None,
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


# --- Parse danh sách chương -------------------------------------------------

class ChapterListParser(HTMLParser):
    """Thu thập (title, href) trong các <a class="chap-title" ...>."""

    def __init__(self) -> None:
        super().__init__()
        self.current_href: str | None = None
        self.current_title: str = ""
        self.buffer: list[str] = []
        self.chapters: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr = dict(attrs)
        if "chap-title" in (attr.get("class") or "").split():
            self.current_href = attr.get("href") or ""
            self.current_title = (attr.get("title") or "").strip()
            self.buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_href is not None:
            title = self.current_title or " ".join("".join(self.buffer).split())
            if title and self.current_href:
                self.chapters.append((title, self.current_href))
            self.current_href = None
            self.current_title = ""
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.buffer.append(data)


def fetch_chapter_list(slug: str) -> list[tuple[str, str]]:
    url = f"{BASE_URL}/{slug}/"
    html_text = http_request(url)
    parser = ChapterListParser()
    parser.feed(html_text)
    # Trang liệt kê chương giảm dần (mới nhất trước) → đảo lại tăng dần.
    return list(reversed(parser.chapters))


# --- Parse nội dung một chương ---------------------------------------------

class ChapterContentParser(HTMLParser):
    """Trích text các <p> bên trong div.reading."""

    SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self.in_reading = 0
        self.in_p = 0
        self.skip_depth = 0
        self.paragraphs: list[str] = []
        self.current: list[str] = []

    def _class_of(self, attrs: list[tuple[str, str | None]]) -> str:
        return dict(attrs).get("class") or ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "div" and "reading" in self._class_of(attrs).split():
            self.in_reading += 1
        elif self.in_reading and tag == "div":
            self.in_reading += 1
        elif self.in_reading and tag == "p":
            self.in_p += 1
            self.current = []
        elif self.in_reading and tag == "br" and self.in_p:
            self.current.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.in_reading and tag == "br" and self.in_p:
            self.current.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag == "p" and self.in_p:
            text = " ".join("".join(self.current).split())
            if text:
                self.paragraphs.append(text)
            self.in_p -= 1
            self.current = []
        elif tag == "div" and self.in_reading:
            self.in_reading -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_reading and self.in_p:
            self.current.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.in_reading and self.in_p:
            self.current.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        if self.in_reading and self.in_p:
            self.current.append(html.unescape(f"&#{name};"))


def fetch_chapter_text(chapter_url: str) -> str:
    html_text = http_request(chapter_url)
    parser = ChapterContentParser()
    parser.feed(html_text)
    if not parser.paragraphs:
        raise RuntimeError(f"Không tìm thấy nội dung ở {chapter_url}")
    return "\n\n".join(parser.paragraphs)


# --- Main -------------------------------------------------------------------

def slugify_filename(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-_.]", "", s)
    return s or "chapter"


def crawl(slug: str, out_dir: Path, delay: float, limit: int | None) -> None:
    print(f"[*] Lấy danh sách chương cho slug: {slug}")
    chapters = fetch_chapter_list(slug)
    print(f"[*] Tìm thấy {len(chapters)} chương.")

    if limit is not None:
        chapters = chapters[:limit]

    out_dir.mkdir(parents=True, exist_ok=True)
    combined_path = out_dir / f"{slug}_full.txt"

    with combined_path.open("w", encoding="utf-8") as combined:
        for idx, (title, url) in enumerate(chapters, start=1):
            filename = f"{idx:03d}-{slugify_filename(title)}.txt"
            chapter_path = out_dir / filename

            if chapter_path.exists():
                print(f"[=] {filename} đã có, bỏ qua.")
                text = chapter_path.read_text(encoding="utf-8")
            else:
                print(f"[>] ({idx}/{len(chapters)}) {title} — {url}")
                try:
                    text = fetch_chapter_text(url)
                except Exception as exc:  # noqa: BLE001
                    print(f"    ! Lỗi: {exc}", file=sys.stderr)
                    time.sleep(delay)
                    continue
                chapter_path.write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
                time.sleep(delay)

            combined.write(f"\n\n====== {title} ======\n\n{text}\n")

    print(f"[✓] Xong. File gộp: {combined_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crawl truyện từ vivutruyen2.net")
    p.add_argument("--slug", default=DEFAULT_SLUG, help="story slug")
    p.add_argument("--out", default="dataset/vivutruyen", help="thư mục lưu")
    p.add_argument("--delay", type=float, default=1.0, help="giây nghỉ giữa các request")
    p.add_argument("--limit", type=int, default=None, help="chỉ crawl N chương đầu (debug)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    crawl(args.slug, Path(args.out), args.delay, args.limit)


if __name__ == "__main__":
    main()
