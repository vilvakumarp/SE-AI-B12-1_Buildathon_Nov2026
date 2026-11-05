from rag_kb import retrieve


def retrieve_knowledge(query: str, category: str) -> dict:
    matches, index = retrieve(query, category)
    chosen = []
    seen_text = set()
    for score, chunk in matches:
        text = str(chunk.get("text", "")).strip()
        if not text or text in seen_text:
            continue
        seen_text.add(text)
        chosen.append((score, chunk))
    source_names = list(dict.fromkeys(str(chunk.get("source", "")) for _, chunk in chosen if chunk.get("source")))
    return {"summary": "No matching company policy was found." if not chosen else "\n\n".join(chunk["text"] for _, chunk in chosen), "sources": source_names, "scores": [score for score, _ in chosen], "links": index.get("links", [])}
