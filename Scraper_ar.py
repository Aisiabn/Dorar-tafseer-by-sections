import requests
from bs4 import BeautifulSoup
import re
import time
import sys
import traceback
import os


BASE      = "https://dorar.net"
INDEX     = "https://dorar.net/arabia"
MAIN_PAGE = "https://dorar.net/arabia/5197"
DELAY     = 1.0
OUT_DIR   = "dorar_arabia"


def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent"     : "Mozilla/5.0 (Windows NT 6.1; WOW64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/109.0.0.0 Safari/537.36",
        "Accept"         : "text/html,application/xhtml+xml,application/xml;"
                           "q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        "Connection"     : "keep-alive",
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
        print(f"  [ERR] {e}")
        return ""


def get_pane_links(html, base_num=None):
    soup  = BeautifulSoup(html, "html.parser")
    panes = soup.find_all("div", class_="tab-pane")
    links = []
    seen  = set()

    for pane in panes:
        if "active" not in pane.get("class", []):
            continue
        for a in pane.find_all("a", href=True):
            href  = a["href"]
            title = a.get_text(strip=True)
            m     = re.match(r"^/arabia/(\d+)$", href)
            if not m or not title:
                continue
            num = int(m.group(1))
            if base_num and num <= base_num:
                continue
            if href in seen:
                continue
            seen.add(href)
            links.append({"url": BASE + href, "title": title, "num": num})
        break

    return links


def get_all_branches(html):
    soup        = BeautifulSoup(html, "html.parser")
    panes       = soup.find_all("div", class_="tab-pane")
    branches    = []
    seen_first  = set()

    for pane in panes:
        links = []
        for a in pane.find_all("a", href=True):
            href  = a["href"]
            title = a.get_text(strip=True)
            m     = re.match(r"^/arabia/(\d+)$", href)
            if not m or not title:
                continue
            links.append({"url": BASE + href, "title": title, "num": int(m.group(1))})

        if not links:
            continue

        first_url = links[0]["url"]
        if first_url in seen_first:
            continue
        seen_first.add(first_url)

        raw_text     = pane.get_text(strip=True)
        branch_title = re.split(r'تَمهيد|تمهيد|البابُ|الباب|مُقَدِّمة', raw_text)[0].strip()
        if not branch_title:
            branch_title = links[0]["title"]

        branches.append({"title": branch_title, "links": links})

    return branches


def extract_content(html, fn_start=1):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["nav", "header", "footer",
                               "script", "style", "form"]):
        tag.decompose()

    noise_classes = [
        re.compile(r"\bmodal\b"),
        re.compile(r"\breadMore\b"),
        re.compile(r"\balert-dorar\b"),
        re.compile(r"\btitle-manhag\b"),
        re.compile(r"\bdorar_custom_accordion\b"),
        re.compile(r"\bdefault-gradient\b"),
        re.compile(r"\bfooter-copyright\b"),
    ]
    for pattern in noise_classes:
        for tag in soup.find_all(True, class_=pattern):
            tag.decompose()

    for tag in soup.find_all("div", id=re.compile(r"^collapse")):
        tag.decompose()

    block = None
    card  = soup.find("div", class_="card-body")
    if card:
        for pane in card.find_all("div", class_="tab-pane"):
            classes = pane.get("class", [])
            if "active" in classes:
                text  = pane.get_text(strip=True)
                links = pane.find_all("a", href=re.compile(r"^/arabia/\d+$"))
                if len(text) > 200 and len(links) <= 2:
                    block = pane
                    break

    if not block:
        block = soup.find("body") or soup

    for tag in block.find_all(True, class_=re.compile(
            r"\balert-dorar\b|\breadMore\b|\bfixed-bottom\b|\bside-nav\b")):
        tag.decompose()
    for a in block.find_all("a"):
        if re.search(r"السابق|التالي|الصفحة|المراجع المعتمدة|اعتماد المنهجية", a.get_text()):
            a.decompose()

    # تحويل العناوين إلى Markdown
    for i in range(1, 7):
        for h in block.find_all(f"h{i}"):
            h.replace_with(f"\n{'#' * (i + 2)} {h.get_text(strip=True)}\n")

    # استخراج الحواشي
    footnotes  = []
    fn_counter = fn_start
    for fn_tag in block.find_all(
            ["span", "div", "sup"],
            class_=re.compile(r"foot|note|hawashi|fn|tip", re.I)):
        fn_text = fn_tag.get_text(strip=True)
        if fn_text:
            footnotes.append(f"[^{fn_counter}]: {fn_text}")
            fn_tag.replace_with(f" [^{fn_counter}] ")
            fn_counter += 1

    # ── إصلاح تقطيع الفقرات ──
    # <br> → مسافة لمنع انكسار السطر في منتصف الفقرة
    for br in block.find_all("br"):
        br.replace_with(" ")

    # استخراج كل <p> كوحدة كاملة
    paras = block.find_all("p")
    if paras:
        raw = "\n\n".join(
            re.sub(r' {2,}', ' ', p.get_text(separator=" ", strip=True))
            for p in paras if p.get_text(strip=True)
        )
    else:
        raw = re.sub(r' {2,}', ' ', block.get_text(separator=" ", strip=True))

    # الحواشي الإنترلاينية [1] يُنظَر: ...
    inline = re.compile(
        r'\[(\d+)\]\s*(يُنظَر[^\n]*|انظر[^\n]*|\(\([^)]+\)\)[^\n]*)',
        re.UNICODE
    )
    found = {m.group(1): m.group(0).strip() for m in inline.finditer(raw)}
    if found:
        for num, body in found.items():
            footnotes.append(f"[^{num}]: {body}")
        clean = inline.sub(lambda m: f" [^{m.group(1)}] ", raw)
    else:
        clean = raw

    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()
    return {"text": clean, "footnotes": footnotes, "fn_next": fn_counter}


def crawl(session, url, title, level, visited, referer=MAIN_PAGE, fn_counter=None):
    if fn_counter is None:
        fn_counter = [1]
    if url in visited:
        return []
    visited.add(url)

    num  = int(url.split("/")[-1])
    html = get_page(session, url, referer=referer)
    time.sleep(DELAY)

    if not html:
        return [{"url": url, "title": title, "level": level,
                 "text": "(failed)", "footnotes": []}]

    sublinks = get_pane_links(html, base_num=num)

    if sublinks:
        results = [{"url": url, "title": title, "level": level,
                    "text": "", "footnotes": [], "fn_next": fn_counter[0]}]
        for sub in sublinks:
            results += crawl(session, sub["url"], sub["title"],
                             level + 1, visited, referer=url, fn_counter=fn_counter)
        return results
    else:
        parsed = extract_content(html, fn_start=fn_counter[0])
        fn_counter[0] = parsed["fn_next"]
        print(f"    → {len(parsed['text'])} chars | fn up to {fn_counter[0]-1}")
        return [{"url": url, "title": title, "level": level, **parsed}]


def save_markdown(results, branch_title):
    safe_name = re.sub(r'[^\w\u0600-\u06FF]', '_', branch_title)[:40]
    filepath   = os.path.join(OUT_DIR, f"{safe_name}.md")

    lines = [
        f"# {branch_title}\n\n",
        "> المصدر: موسوعة اللغة العربية - الدرر السنية\n\n",
        "---\n\n",
    ]

    for r in results:
        hashes = "#" * min(max(r["level"], 1), 6)
        lines.append(f"{hashes} {r['title']}\n\n")

        if r.get("text"):
            lines.append(f"{r['text']}\n\n")

        if r.get("footnotes"):
            for fn in r["footnotes"]:
                lines.append(f"{fn}\n")
            lines.append("\n")

        if r["level"] >= 3:
            lines.append("---\n\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)

    total_chars = sum(len(r.get("text", "")) for r in results)
    print(f"  → Saved: {filepath} | {len(results)} pages | ~{total_chars // 1024} KB")
    return filepath


if __name__ == "__main__":
    try:
        os.makedirs(OUT_DIR, exist_ok=True)

        session = make_session()
        print("Initializing session...")
        get_page(session, INDEX)
        time.sleep(1)

        html_main = get_page(session, MAIN_PAGE)
        time.sleep(2)

        branches = get_all_branches(html_main)
        print(f"\n[OK] Found {len(branches)} branches:\n")
        for i, b in enumerate(branches, 1):
            print(f"  {i}. {b['title']} ({len(b['links'])} top links)")

        print()
        visited = {MAIN_PAGE}

        for b in branches:
            print(f"\n{'='*50}")
            print(f"Branch: {b['title']}")
            print('='*50)

            safe_name = re.sub(r'[^\w\u0600-\u06FF]', '_', b["title"])[:40]
            filepath  = os.path.join(OUT_DIR, f"{safe_name}.md")
            if os.path.exists(filepath):
                print(f"  ← موجود، تخطي: {filepath}")
                continue

            fn_counter = [1]
            results = []
            for entry in b["links"]:
                results += crawl(session, entry["url"], entry["title"],
                                 level=2, visited=visited, fn_counter=fn_counter)

            save_markdown(results, b["title"])   # ← تصحيح: كان None

        print("\nAll done!")

    except Exception:
        print("=== ERROR ===")
        traceback.print_exc()
