# Greeting Generator Architecture

High level flow for the Raycast → Notion → OpenAI → Gmail workflow:

1. **Raycast command** (future step) accepts a callsign and optional manual inputs
   (recipient list, gift link override, pasted Unleash response for replies).
2. The command invokes the Python CLI in this repo (`python -m messaging_consumer.cli greetings …`).
3. The CLI loads secrets via `MessagingSettings`:
   - Notion API credentials (`NOTION_API_KEY`, `NOTION_COMPANIES_DB_ID`, etc.)
   - OpenAI key + fine-tuned model ID (`OPENAI_API_KEY`, `VOICE_MODEL_ID`)
   - Gmail OAuth creds (`GMAIL_CLIENT_ID`, …) used to create a draft.
4. `NotionContextFetcher` queries the Companies DB by callsign and pulls both the
   dossier summary and embedded news notes so the LLM has company-specific flavor.
5. `GreetingPromptBuilder` stitches together:
   - The Gmail template (HTML with `{placeholder}` markers),
   - Company/news context, recipient info, and any pasted Unleash text,
   - Guardrails that only `{company_blurb}`/`{first_name}` style regions can be rewritten.
6. `VoiceModelClient` calls the OpenAI fine-tuned model and returns the fully
   rendered HTML/markdown greeting in your voice.
7. `GmailDraftService` converts the output to RFC822 and uses the Gmail API to
   create a draft addressed to the provided recipients.

When we expand into reply-assist, Raycast will capture the incoming message body,
ask you to (optionally) paste Unleash output, and run a similar prompt pipeline
before drafting the response. The scaffolding in `messaging_consumer/llm` and
`messaging_consumer/gmail` is designed so that future decision-tree logic can be
inserted without rewriting the integrations.
