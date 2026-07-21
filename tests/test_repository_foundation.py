from importlib.metadata import metadata


def test_project_metadata_has_expected_name() -> None:
    assert metadata("dara-cost-aware-search")["Name"] == "dara-cost-aware-search"
