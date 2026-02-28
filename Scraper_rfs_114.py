import requests
from bs4 import BeautifulSoup
import re
import time
import os
import traceback


BASE    = "https://dorar.net"
INDEX   = "https://dorar.net/tafseer"
DELAY   = 1.0
OUT_DIR = "dorar_tafseer"


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
# استخراج المحتوى
# ─────────────────────────────────────────────

def extract_content(html):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["nav", "header", "footer", "script", "style", "form"]):
        tag.decompose()
    for pat in [
        re.compile(r"\bmodal\b"),
        re.compile(r"\breadMore\b"),
        re.compile(r"\balert-dorar\b"),
        re.compile(r"\btitle-manhag\b"),
        re.compile(r"\bdefault-gradient\b"),
        re.compile(r"\bfooter-copyright\b"),
        re.compile(r"\bcard-personal\b"),
    ]:
        for tag in soup.find_all(True, class_=pat):
            tag.decompose()
    for tag in soup.find_all("div", id=re.compile(r"^collapse")):
        tag.decompose()

    block = None
    card  = soup.find("div", class_="card-body")
    if card:
        for pane in card.find_all("div", class_="tab-pane"):
            if "active" not in pane.get("class", []):
                continue
            text  = pane.get_text(strip=True)
            links = pane.find_all("a", href=SECTION_RE)
            if len(text) > 200 and len(links) <= 2:
                block = pane
                break

    if not block:
        block = soup.find("body") or soup

    articles   = block.find_all("article") or [block]
    all_text   = []
    footnotes  = []
    fn_counter = 1

    for art in articles:
        # الأقواس القرآنية
        for span in art.find_all("span", class_="aaya"):
            span.replace_with(f"﴿{span.get_text(strip=True)}﴾")
        for span in art.find_all("span", class_="sora"):
            span.replace_with(f" {span.get_text(strip=True)} ")
        for span in art.find_all("span", class_="hadith"):
            span.replace_with(f"«{span.get_text(strip=True)}»")
        for span in art.find_all("span", class_="title-2"):
            span.replace_with(f"\n#### {span.get_text(strip=True)}\n")

        # حذف روابط التنقل
        for a in art.find_all("a"):
            if re.search(r"السابق|التالي|الصفحة|المراجع|اعتماد", a.get_text()):
                a.decompose()

        # عناوين HTML
        for i in range(1, 7):
            for h in art.find_all(f"h{i}"):
                h.replace_with(f"\n{'#' * (i + 2)} {h.get_text(strip=True)}\n")

        # الحواشي — نعالج الأقواس داخل كل حاشية قبل استخراج نصها
        for fn_tag in art.find_all("span", class_="tip"):
            for inner in fn_tag.find_all("span", class_="aaya"):
                inner.replace_with(f"﴿{inner.get_text(strip=True)}﴾")
            for inner in fn_tag.find_all("span", class_="hadith"):
                inner.replace_with(f"«{inner.get_text(strip=True)}»")
            fn_text = fn_tag.get_text(strip=True)
            if fn_text:
                footnotes.append(f"[^{fn_counter}]: {fn_text}")
                fn_tag.replace_with(f" [^{fn_counter}]")
                fn_counter += 1

        # <br> → مسافة
        for br in art.find_all("br"):
            br.replace_with(" ")

        # استخراج فقرة فقرة
        paras = art.find_all("p")
        if paras:
            text = "\n\n".join(
                re.sub(r' {2,}', ' ', p.get_text(separator=" ", strip=True))
                for p in paras if p.get_text(strip=True)
            )
        else:
            text = re.sub(r' {2,}', ' ', art.get_text(separator=" ", strip=True))

        if text.strip():
            all_text.append(text.strip())

    clean = re.sub(r'\n{3,}', '\n\n', "\n\n".join(all_text)).strip()
    return {"text": clean, "footnotes": footnotes}


# ─────────────────────────────────────────────
# الحفظ
# ─────────────────────────────────────────────

def save_markdown(surah_title, surah_num, intro, sections):
    safe     = re.sub(r'[^\w\u0600-\u06FF]', '_', surah_title)[:40]
    filename = f"{surah_num:03d}_{safe}.md"
    filepath = os.path.join(OUT_DIR, filename)

    lines = [
        f"# {surah_title}\n\n",
        f"> المصدر: {BASE}/tafseer/{surah_num}\n\n",
        "---\n\n",
    ]

    global_fn = 1

    def renum(text, fns):
        nonlocal global_fn
        local_map = {}
        for fn in fns:
            m = re.match(r'\[\^(\d+)\]:', fn)
            if m:
                local_map[m.group(1)] = str(global_fn)
                global_fn += 1
        for loc, gbl in local_map.items():
            text = re.sub(rf'\[\^{loc}\]', f'[^{gbl}]', text)
        new_fns = []
        for fn in fns:
            m = re.match(r'\[\^(\d+)\]:(.*)', fn, re.DOTALL)
            if m:
                new_fns.append(f"[^{local_map.get(m.group(1), m.group(1))}]:{m.group(2)}")
        return text, new_fns

    # تعريف السورة
    if intro.get("text"):
        lines.append("## تعريف السورة\n\n")
        text, fns = renum(intro["text"], intro.get("footnotes", []))
        lines.append(f"{text}\n\n")
        for fn in fns:
            lines.append(f"{fn}\n")
        lines.append("\n---\n\n")

    # المقاطع
    for sec in sections:
        lines.append(f"## {sec['title']}\n\n")
        lines.append(f"> {sec['url']}\n\n")
        if sec.get("text"):
            text, fns = renum(sec["text"], sec.get("footnotes", []))
            lines.append(f"{text}\n\n")
            for fn in fns:
                lines.append(f"{fn}\n")
        lines.append("\n---\n\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)

    total = len(intro.get("text", "")) + sum(len(s.get("text", "")) for s in sections)
    print(f"    ✔ {filepath}  |  {len(sections)} مقطع  |  ~{total//1024} KB")
    return filepath


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
        print(f"\n③ {len(surah_links)} سورة\n")

        for surah in surah_links:
            snum   = surah["num"]
            stitle = surah["title"]
            surl   = surah["url"]

            filepath = os.path.join(OUT_DIR, f"{snum:03d}_{re.sub(r'[^\\w\\u0600-\\u06FF]', '_', stitle)[:40]}.md")
            if os.path.exists(filepath):
                print(f"  ← موجود، تخطي: {filepath}")
                continue

            print(f"\n{'='*50}")
            print(f"[{snum}] {stitle}")

            html_surah = get_page(session, surl, referer=INDEX)
            time.sleep(DELAY)
            if not html_surah:
                continue

            intro     = extract_content(html_surah)
            first_url = get_first_section_link(html_surah, snum)
            print(f"  تعريف: {len(intro['text'])} حرف")

            sections = []
            next_url = first_url
            visited  = set()
            sec_idx  = 1

            while next_url and next_url not in visited:
                visited.add(next_url)
                html_sec = get_page(session, next_url, referer=surl)
                time.sleep(DELAY)
                if not html_sec:
                    break
                title  = get_page_title(html_sec)
                parsed = extract_content(html_sec)
                print(f"    [{sec_idx}] {title[:50]}  →  {len(parsed['text'])} حرف")
                sections.append({"url": next_url, "title": title, **parsed})
                next_url = get_next_link(html_sec)
                sec_idx += 1

            print(f"  → {len(sections)} مقطع مكتمل")
            save_markdown(stitle, snum, intro, sections)

        print("\n✔ اكتمل.")

    except SystemExit as e:
        print(e)
    except Exception:
        traceback.print_exc()
