import pytest

from debate.cli import main
from debate.config import load_company, load_era
from debate.prompts import build_system


def test_era_without_a_briefing_is_refused_before_any_model_call(capsys):
    assert main(["run", "--era", "post_covid", "--dry-run"]) == 2
    assert "no briefing for era 'post_covid'" in capsys.readouterr().err


def test_unknown_era_is_an_error():
    with pytest.raises(KeyError):
        load_era("jurassic")


def test_pre_covid_prompt_carries_cutoff_and_no_later_facts():
    system = build_system("cfo", company=load_company("biobot"), era=load_era("pre_covid"), max_words=60)
    assert "2017-12-31" in system
    for anachronism in ("SARS-CoV-2", "coronavirus", "pandemic", "lockdown"):
        assert anachronism.lower() not in system.lower()
