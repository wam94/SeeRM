from messaging_consumer.llm import VoicePromptBuilder
from messaging_consumer.notion_ingest import CompanyContext, NewsHighlight


def test_prompt_builder_renders_literal_slots(tmp_path):
    builder = VoicePromptBuilder()
    ctx = CompanyContext(
        notion_page_id="page",
        name="Mercury",
        callsign="mercury",
        owners=["Will"],
        summary="Strong growth.",
        last_intel_update=None,
        news_highlights=[NewsHighlight(title="Launch", summary=None, url=None, week_of=None)],
    )

    messages = builder.build_messages(raw_blurb="Noticed your launch.", manual_notes=None, knowledge_base_text=None)
    assert "Noticed your launch" in messages[1]["content"]
