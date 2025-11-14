from datetime import datetime

from messaging_consumer.notion_ingest import NotionContextFetcher


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.databases = self
        self.pages = self
        self.blocks = self
        self.children = self
        self.calls = []

    # Database query for companies
    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return self.responses.pop(0)

    # Page retrieve for news relation
    def retrieve(self, page_id):
        self.calls.append(("retrieve", page_id))
        return self.responses.pop(0)

    # Blocks children list
    def list(self, block_id):
        self.calls.append(("list", block_id))
        return self.responses.pop(0)


def _company_page():
    return {
        "id": "company-page",
        "properties": {
            "Name": {"title": [{"plain_text": "Mercury"}]},
            "Callsign": {"rich_text": [{"plain_text": "mercury"}]},
            "Owner": {"people": [{"name": "Will Mitchell"}]},
            "Intel Summary": {"rich_text": [{"plain_text": "Great momentum."}]},
            "Last Intel Update": {"date": {"start": "2024-06-01"}},
            "News Items": {"relation": [{"id": "news-1"}]},
        },
    }


def _news_page():
    return {
        "id": "news-1",
        "properties": {
            "Title": {"title": [{"plain_text": "Mercury raises"}]},
            "Summary": {"rich_text": [{"plain_text": "Closed a big round."}]},
            "URL": {"url": "https://example.com"},
            "Week Of": {"date": {"start": "2024-05-31"}},
        },
    }


def _block_list():
    return {
        "results": [
            {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "News"}]}},
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"plain_text": "• Launching new card"}]},
            },
        ]
    }


def test_fetch_company_context():
    fake = FakeClient(
        responses=[
            {"results": [_company_page()]},
            _news_page(),
            _block_list(),
        ]
    )

    fetcher = NotionContextFetcher(
        api_key="FAKE",
        companies_db_id="companies",
        intel_db_id="intel",
        client=fake,
        max_news_items=3,
    )

    ctx = fetcher.get_company_context("Mercury")
    assert ctx is not None
    assert ctx.callsign == "mercury"
    assert ctx.owners == ["Will Mitchell"]
    assert len(ctx.news_highlights) == 2  # relation + inline bullet
    assert ctx.news_highlights[0].title == "Mercury raises"
