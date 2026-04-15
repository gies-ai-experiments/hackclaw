"""Tests for the solution knowledge base."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.helpqueue.solutions import SolutionEntry, SolutionStore, cosine_similarity


def test_cosine_similarity_identical_vectors():
    assert cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors():
    assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector():
    assert cosine_similarity([0, 0], [1, 0]) == pytest.approx(0.0)


def test_store_add_and_list(tmp_path):
    store = SolutionStore(tmp_path / "solutions.json")
    store.add(
        ticket_id="HELP-001",
        problem="Can't publish agent",
        solution="Enable plugin in admin center",
        embedding=[0.1, 0.2, 0.3],
    )
    assert len(store.all()) == 1
    assert store.all()[0].ticket_id == "HELP-001"


def test_store_persists_to_file(tmp_path):
    path = tmp_path / "solutions.json"
    store = SolutionStore(path)
    store.add(
        ticket_id="HELP-001",
        problem="Can't publish agent",
        solution="Enable plugin in admin center",
        embedding=[0.1, 0.2, 0.3],
    )
    # Reload from file
    store2 = SolutionStore(path)
    assert len(store2.all()) == 1
    assert store2.all()[0].solution == "Enable plugin in admin center"


def test_store_find_similar(tmp_path):
    store = SolutionStore(tmp_path / "solutions.json")
    store.add(
        ticket_id="HELP-001",
        problem="Can't publish agent",
        solution="Enable plugin in admin center",
        embedding=[1.0, 0.0, 0.0],
    )
    store.add(
        ticket_id="HELP-002",
        problem="Wifi not working",
        solution="Connect to IllinoisNet",
        embedding=[0.0, 1.0, 0.0],
    )
    matches = store.find_similar([0.9, 0.1, 0.0], threshold=0.7)
    assert len(matches) == 1
    assert matches[0][0].ticket_id == "HELP-001"


def test_store_find_similar_no_match(tmp_path):
    store = SolutionStore(tmp_path / "solutions.json")
    store.add(
        ticket_id="HELP-001",
        problem="Can't publish agent",
        solution="Enable plugin",
        embedding=[1.0, 0.0, 0.0],
    )
    matches = store.find_similar([0.0, 1.0, 0.0], threshold=0.7)
    assert len(matches) == 0


def test_store_empty_find_similar(tmp_path):
    store = SolutionStore(tmp_path / "solutions.json")
    matches = store.find_similar([1.0, 0.0], threshold=0.5)
    assert matches == []
