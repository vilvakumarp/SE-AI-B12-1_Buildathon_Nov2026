# Customer Support Desk Architecture

## Runtime flow

```text
Customer complaint
        |
        v
Query Agent --------------> category, urgency, sentiment, summary
        |
        v
ChromaDB retrieval -------> relevant SOP and web-page chunks
        |
        v
Knowledge Base Agent -----> selected evidence and source metadata
        |
        v
Resolution Agent ----------> grounded response draft
        |
        v
Senior Editor Agent -------> final relevance, language, and tone review
        |
        v
Log Agent -----------------> confirmed-complaint escalation and CSV ticket
        |
        v
Human support -------------> admin updates visible to the customer
```

## Knowledge-base ingestion

The admin uploads DOCX, PDF, Excel, TXT, or Markdown files. DOCX files also provide hyperlink targets. The ingestion layer extracts text and tables, optionally crawls those links with retries, embeds chunks with sentence-transformers, and persists them in `knowledge_base/chroma_db/`.

## Safety boundaries

- The resolution stage receives retrieved evidence and must not invent policy details.
- Low-confidence or missing-evidence cases are escalated.
- OpenAI, Serper, and LangSmith credentials are read from `.env` only.
- API failures fall back to the local evidence-grounded pipeline.
- Normal chat does not create a ticket; the Log Agent runs only after the customer confirms a complaint summary.
- Customer-facing replies include source document names when evidence is retrieved.
