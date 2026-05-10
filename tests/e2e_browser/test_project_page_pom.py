"""Project 页 E2E：基于新 POM 的业务方法（不与已有 test_project_page 重叠）。"""
from __future__ import annotations

from .pages import ProjectPage


def test_project_page_has_new_button(page, base_url):
    pp = ProjectPage(page, base_url).goto()
    assert pp.has_new_project_button()


def test_project_page_has_activate_button(page, base_url):
    pp = ProjectPage(page, base_url).goto()
    assert pp.has_activate_button()


def test_project_page_lists_known_projects(page, base_url):
    pp = ProjectPage(page, base_url).goto()
    names = pp.get_project_names()
    assert names, f"项目列表不应为空, body={pp.body_text()[:200]}"


def test_project_page_search_does_not_crash(page, base_url):
    pp = ProjectPage(page, base_url).goto()
    pp.search_project("__no_such_project_e2e__")
    body = pp.body_text()
    assert "项目管理" in body
