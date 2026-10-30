"""The landing page (roadmap phase 2): where visitors start, what it links to, and that it keeps the CSP."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slim_lims import __version__, about
from slim_lims.main import create_app
from slim_lims.personas import sync_personas
from tests.conftest import login, make_settings

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def demo_client(engine, reference, db):
    sync_personas(db)
    db.commit()
    app = create_app(make_settings(str(engine.url), demo_personas=True), engine=engine, reference=reference)
    with TestClient(app) as client:
        yield client


def test_a_demo_visitor_starts_at_the_landing_page(demo_client):
    response = demo_client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/about"


def test_without_personas_the_landing_page_offers_no_way_in_but_the_login(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    page = client.get("/about")
    assert page.status_code == 200 and "/demo/continue-as" not in page.text


def test_the_landing_page_starts_a_persona_in_one_click(demo_client):
    page = demo_client.get("/about").text
    forms = re.findall(r'<form method="post" action="[^"]*/demo/continue-as">(.*?)</form>', page, re.S)
    assert len(forms) == 6  # Casey, Ana, Rob, Sue, Quinn and Uma (quality, D-109)
    assert all('name="csrf_token" value="' in form and 'name="user_id"' in form for form in forms)


def test_the_landing_page_keeps_the_csp_and_shows_the_diagram(demo_client):
    page = demo_client.get("/about").text
    assert "<script" not in page and " style=" not in page  # no inline script or style (CSP)
    assert f"YourLab running SlimLIMS v{__version__}" in page
    assert '<svg class="arch"' in page and "SlimLIMS architecture by department" in page


def test_every_tour_step_and_highlight_has_its_words(demo_client):
    page = demo_client.get("/about").text
    assert page.count('class="about-tour__who"') == len(about.TOUR)
    assert page.count("<strong>") >= len(about.HIGHLIGHTS)
    assert "{{" not in page and "{%" not in page


def test_links_into_this_repo_resolve():
    links = [link for item in about.HIGHLIGHTS for link in item.links] + list(about.REPOS)
    mine = [link for link in links if link.repo == "slim-lims-oss" and link.path]
    assert mine and all((REPO / link.path).is_file() for link in mine)


def test_staff_still_land_on_the_board_and_find_about(client, staff_user):
    login(client, "staff@lab.example")
    page = client.get("/")
    assert page.status_code == 200 and 'href="/about"' not in page.text  # About is for demo visitors
    assert client.get("/about").status_code == 200
