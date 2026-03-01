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

        global_fn     = 1
        all_footnotes = []   # ← تُجمع هنا كل حواشي الملف

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
                text = re.sub(rf'\[\^{re.escape(loc)}\](?!\d)', f'[^{gbl}]', text)

            lines.append(f"{text}\n\n")
            lines.append("---\n\n")

            # ← تأجيل: أعد ترقيم التعريفات وأضفها للقائمة العامة
            for fn in fns:
                m = re.match(r'\[\^(\d+)\]:(.*)', fn, re.DOTALL)
                if m:
                    new_num = local_map.get(m.group(1), m.group(1))
                    all_footnotes.append(f"[^{new_num}]:{m.group(2)}\n")

        # ✅ كل الحواشي في نهاية الملف مرة واحدة
        if all_footnotes:
            lines.append("\n")
            lines.extend(all_footnotes)

        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)

        print(f"  ✔ {heading[:35]:35s}  {len(entries):4d} موضع  ~{total_chars//1024} KB  |  {len(all_footnotes)} حاشية")
        index_lines.append(f"- [{heading}](./{safe}.md) — {len(entries)} موضع\n")

    with open(os.path.join(OUT_DIR, "فهرس.md"), "w", encoding="utf-8") as f:
        f.writelines(index_lines)

    print(f"\n✔ فهرس.md — {len(db)} قسم")