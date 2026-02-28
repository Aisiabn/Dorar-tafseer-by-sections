import requests
from bs4 import BeautifulSoup
import re
import time
import os
import traceback
from collections import defaultdict
from difflib import SequenceMatcher


BASE    = "https://dorar.net"
INDEX   = "https://dorar.net/tafseer"
DELAY   = 1.2
OUT_DIR = "dorar_by_section"

# للاختبار: ضع عدد السور (None = كل القرآن)
TEST_SURAHS = None


# ─────────────────────────────────────────────
# Session
# ─────────────────────────────────────────────

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent"               : "Mozilla/5.0 (Windows NT 6.1; WOW64) "
                                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                                     "Chrome/109.0.0.0 Safari/537.36",
        "Accept"                   : "text/html,application/xhtml+xml,application/xml;"
                                     "q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language"          : "ar,en-US;q=0.9,en;q=0.8",
        "Connection"               : "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s


def get_page(session, url, referer=INDEX):
    session.headers["Referer"] = referer
    try:
        r = session.get(url, timeout=20)
        print(f"  [{r.status_code}] {url}")
        return r.text if r.status_code == 200 else ""
    except Exception as e:
        print(f"  [ERR] {url} — {e}")
        return ""


# ─────────────────────────────────────────────
# أنماط الروابط
# ─────────────────────────────────────────────

SURAH_RE   = re.compile(r"^/tafseer/(\d+)$")
SECTION_RE = re.compile(r"^/tafseer/(\d+)/(\d+)$")


# ─────────────────────────────────────────────
# تطبيع العنوان + تجميع ذكي
# ─────────────────────────────────────────────

TASHKEEL = re.compile(
    r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]'
)

def normalize_heading(text):
    text = TASHKEEL.sub('', text)
    text = re.sub(r'[أإآٱ]', 'ا', text)
    text = re.sub(r'ى', 'ي', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


_known_keys: list = []

# أنماط التوحيد الصريحة
CANONICAL_PATTERNS = [
    (re.compile(r'مناسبة\s+الآ[يى](ة|تين|ات)\s+لما\s+(قبلها|سبق)'), 'مناسبة الآيات لما قبلها'),
    (re.compile(r'القراءات\s+(ذات|التي\s+لها)\s+الاثر\s+في\s+التفسير'), 'القراءات ذات الأثر في التفسير'),
]

def fuzzy_key(heading: str, threshold: float = 0.82) -> str:
    norm = normalize_heading(heading)

    # تحقق من الأنماط الصريحة أولاً
    for pattern, canonical in CANONICAL_PATTERNS:
        if pattern.search(norm):
            if canonical not in _known_keys:
                _known_keys.append(canonical)
            return canonical

    # المقارنة الاعتيادية
    best_score, best_key = 0.0, None
    for k in _known_keys:
        score = SequenceMatcher(None, norm, k).ratio()
        if score > best_score:
            best_score, best_key = score, k
    if best_score >= threshold:
        return best_key
    _known_keys.append(norm)
    return norm


# ─────────────────────────────────────────────
# روابط السور
# ─────────────────────────────────────────────

def get_surah_links(html):
    soup  = BeautifulSoup(html, "html.parser")
    links = []
    seen  = set()
    for card in soup.find_all("div", class_="card-personal"):
        a = card.find("a", href=SURAH_RE)
        if not a:
            continue
        href  = a["href"]
        title = a.get_text(strip=True)
        if href in seen or not title:
            continue
        seen.add(href)
        num = int(SURAH_RE.match(href).group(1))
        links.append({"url": BASE + href, "title": title, "num": num})
    links.sort(key=lambda x: x["num"])
    return links


# ─────────────────────────────────────────────
# أول رابط مقطع في السورة
# ─────────────────────────────────────────────

def get_first_section_link(html, surah_num):
    soup       = BeautifulSoup(html, "html.parser")
    candidates = []
    for a in soup.find_all("a", href=SECTION_RE):
        m = SECTION_RE.match(a["href"])
        if m and int(m.group(1)) == surah_num:
            candidates.append((int(m.group(2)), BASE + a["href"]))
    if candidates:
        candidates.sort()
        return candidates[0][1]
    for a in soup.find_all("a", href=SECTION_RE):
        if "التالي" in a.get_text():
            return BASE + a["href"]
    return None


# ─────────────────────────────────────────────
# رابط التالي
# ─────────────────────────────────────────────

def get_next_link(html):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=SECTION_RE):
        if "التالي" in a.get_text():
            return BASE + a["href"]
    return None


# ─────────────────────────────────────────────
# عنوان الصفحة
# ─────────────────────────────────────────────

def get_page_title(html):
    soup = BeautifulSoup(html, "html.parser")
    og   = soup.find("meta", property="og:title")
    if og and og.get("content"):
        parts = og["content"].split(" - ", 1)
        return parts[-1].strip()
    t = soup.find("title")
    if t:
        parts = t.get_text().split(" - ")
        return parts[-1].strip()
    return ""


# ─────────────────────────────────────────────
# استخراج الأقسام
# ─────────────────────────────────────────────

# نمط يلتقط title-1
TITLE1_RE = re.compile(
    r'<span[^>]*class=["\'][^"\']*title-1[^"\']*["\'][^>]*>(.*?)</span>',
    re.DOTALL
)

def process_chunk(raw_html, fn_counter):
    """تحوّل مقطع HTML خام إلى نص Markdown مع حواشٍ."""
    chunk = BeautifulSoup(raw_html, "html.parser")

    # حذف روابط التنقل
    for a in chunk.find_all("a"):
        if re.search(r"السابق|التالي|الصفحة|المراجع|اعتماد", a.get_text()):
            a.decompose()

    # الأقواس القرآنية
    for span in chunk.find_all("span", class_="aaya"):
        span.replace_with(f"﴿{span.get_text(strip=True)}﴾")
    for span in chunk.find_all("span", class_="sora"):
        span.replace_with(f" {span.get_text(strip=True)} ")
    for span in chunk.find_all("span", class_="hadith"):
        span.replace_with(f"«{span.get_text(strip=True)}»")
    for span in chunk.find_all("span", class_="title-2"):
        span.replace_with(f"\n#### {span.get_text(strip=True)}\n")

    # عناوين HTML
    for i in range(1, 7):
        for h in chunk.find_all(f"h{i}"):
            h.replace_with(f"\n{'#' * (i + 2)} {h.get_text(strip=True)}\n")

    # الحواشي
    footnotes = []
    for fn_tag in chunk.find_all("span", class_="tip"):
        for inner in fn_tag.find_all("span", class_="aaya"):
            inner.replace_with(f"﴿{inner.get_text(strip=True)}﴾")
        for inner in fn_tag.find_all("span", class_="hadith"):
            inner.replace_with(f"«{inner.get_text(strip=True)}»")
        fn_text = fn_tag.get_text(strip=True)
        if fn_text:
            footnotes.append(f"[^{fn_counter}]: {fn_text}")
            fn_tag.replace_with(f" [^{fn_counter}]")
            fn_counter += 1

    for br in chunk.find_all("br"):
        br.replace_with(" ")

    clean = re.sub(r' {2,}', ' ', chunk.get_text(separator=" ", strip=True))
    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()

    return clean, footnotes, fn_counter


def extract_articles(html):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["nav", "header", "footer", "script", "style", "form"]):
        tag.decompose()
    for pat in [
        re.compile(r"\bmodal\b"),
        re.compile(r"\breadMore\b"),
        re.compile(r"\balert-dorar\b"),
        re.compile(r"\bcard-personal\b"),
        re.compile(r"\bdefault-gradient\b"),
        re.compile(r"\bfooter-copyright\b"),
    ]:
        for tag in soup.find_all(True, class_=pat):
            tag.decompose()

    articles = soup.find_all("article")
    if not articles:
        return []

    results    = []
    fn_counter = 1

    for block in articles:
        h_tag        = block.find(["h5", "h4", "h3"])
        main_heading = h_tag.get_text(strip=True) if h_tag else ""
        if h_tag:
            h_tag.decompose()
        if not main_heading:
            continue

        inner_html = str(block)

        # ① entry كامل للـ article — title-1 تُعامَل كـ ####
        full_html = TITLE1_RE.sub(
            lambda m: f'<span class="title-2">{m.group(1)}</span>',
            inner_html
        )
        clean_full, fns_full, fn_counter = process_chunk(full_html, fn_counter)
        if clean_full:
            results.append({
                "heading"  : main_heading,
                "text"     : clean_full,
                "footnotes": fns_full,
            })

        # ② كل title-1 → entry مستقل إضافي للتجميع by-section
        parts = TITLE1_RE.split(inner_html)
        # parts = [قبل_أول_title1, نص_title1, محتوى_1, نص_title1, محتوى_2, ...]
        it = iter(parts[1:])
        for title_text, content in zip(it, it):
            sub_heading = re.sub(r'<[^>]+>', '', title_text).strip()
            if not sub_heading:
                continue
            clean_sub, fns_sub, fn_counter = process_chunk(content, fn_counter)
            if clean_sub:
                results.append({
                    "heading"  : sub_heading,
                    "text"     : clean_sub,
                    "footnotes": fns_sub,
                })

    return results


# ─────────────────────────────────────────────
# الزحف
# ─────────────────────────────────────────────

def crawl_all(session, surah_links):
    db              = defaultdict(list)
    heading_display = {}

    for surah in surah_links:
        snum   = surah["num"]
        stitle = surah["title"]
        surl   = surah["url"]

        print(f"\n{'='*55}")
        print(f"[{snum:3d}] {stitle}")

        html_surah = get_page(session, surl, referer=INDEX)
        time.sleep(DELAY)
        if not html_surah:
            continue

        intro_articles = extract_articles(html_surah)
        print(f"  تعريف: {len(intro_articles)} أقسام")
        for art in intro_articles:
            key = fuzzy_key(art["heading"])
            if key not in heading_display:
                heading_display[key] = art["heading"]
            db[key].append({
                "surah"     : stitle,
                "surah_num" : snum,
                "page_title": f"تعريف {stitle}",
                "url"       : surl,
                "text"      : art["text"],
                "footnotes" : art["footnotes"],
            })

        first_url = get_first_section_link(html_surah, snum)
        if not first_url:
            print("  ⚠ لم يُوجد أول مقطع")
            continue

        next_url = first_url
        visited  = set()
        sec_num  = 1

        while next_url and next_url not in visited:
            visited.add(next_url)
            html_sec = get_page(session, next_url, referer=surl)
            time.sleep(DELAY)
            if not html_sec:
                break

            page_title = get_page_title(html_sec)
            articles   = extract_articles(html_sec)
            print(f"    [{sec_num:3d}] {page_title[:45]:45s}  {len(articles)} أقسام")

            for art in articles:
                key = fuzzy_key(art["heading"])
                if key not in heading_display:
                    heading_display[key] = art["heading"]
                db[key].append({
                    "surah"     : stitle,
                    "surah_num" : snum,
                    "page_title": page_title,
                    "url"       : next_url,
                    "text"      : art["text"],
                    "footnotes" : art["footnotes"],
                })

            next_url = get_next_link(html_sec)
            sec_num += 1

    return db, heading_display


# ─────────────────────────────────────────────
# الحفظ
# ─────────────────────────────────────────────

def save_by_section(db, heading_display):
    index_lines = [
        "# فهرس أقسام التفسير\n\n",
        f"> {len(db)} قسم مختلف\n\n",
        "---\n\n",
    ]

    for key, entries in sorted(db.items(), key=lambda x: -len(x[1])):
        heading     = heading_display.get(key, key)
        safe        = re.sub(r'[^\w\u0600-\u06FF]', '_', key)[:60]
        filepath    = os.path.join(OUT_DIR, f"{safe}.md")
        n_surahs    = len(set(e["surah_num"] for e in entries))
        total_chars = sum(len(e["text"]) for e in entries)

        lines = [
            f"# {heading}\n\n",
            f"> {len(entries)} موضع — {n_surahs} سورة\n\n",
            "---\n\n",
        ]

        global_fn = 1

        for e in sorted(entries, key=lambda x: x["surah_num"]):
            lines.append(f"## {e['surah']} — {e['page_title']}\n\n")
            lines.append(f"> {e['url']}\n\n")

            text = e["text"]
            fns  = e.get("footnotes", [])

            local_map = {}
            for fn in fns:
                m = re.match(r'\[\^(\d+)\]:', fn)
                if m:
                    local_map[m.group(1)] = str(global_fn)
                    global_fn += 1

            for loc, gbl in local_map.items():
                text = re.sub(rf'\[\^{loc}\]', f'[^{gbl}]', text)

            lines.append(f"{text}\n\n")

            for fn in fns:
                m = re.match(r'\[\^(\d+)\]:(.*)', fn, re.DOTALL)
                if m:
                    new_num = local_map.get(m.group(1), m.group(1))
                    lines.append(f"[^{new_num}]:{m.group(2)}\n")

            lines.append("\n---\n\n")

        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)

        print(f"  ✔ {heading[:35]:35s}  {len(entries):4d} موضع  ~{total_chars//1024} KB")
        index_lines.append(f"- [{heading}](./{safe}.md) — {len(entries)} موضع\n")

    with open(os.path.join(OUT_DIR, "فهرس.md"), "w", encoding="utf-8") as f:
        f.writelines(index_lines)

    print(f"\n✔ فهرس.md — {len(db)} قسم")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        session = make_session()

        print("① تهيئة الجلسة...")
        get_page(session, INDEX, referer=BASE)
        time.sleep(1.5)

        print("\n② جلب الصفحة الرئيسية...")
        html_main = get_page(session, INDEX, referer=BASE)
        time.sleep(2)
        if not html_main:
            raise SystemExit("فشل جلب الصفحة الرئيسية")

        surah_links = get_surah_links(html_main)
        print(f"\n③ {len(surah_links)} سورة مكتشفة")

        if TEST_SURAHS:
            surah_links = surah_links[:TEST_SURAHS]
            print(f"   وضع الاختبار: أول {TEST_SURAHS} سور فقط")
            print("   (غيّر TEST_SURAHS = None لجلب كل القرآن)\n")

        print("\n④ الزحف...")
        db, heading_display = crawl_all(session, surah_links)

        print(f"\n⑤ حفظ {len(db)} قسم...")
        save_by_section(db, heading_display)

        print("\n✔ اكتمل.")

    except SystemExit as e:
        print(e)
    except Exception:
        traceback.print_exc()
