import pytest
import yaml

from prospector.offer import OfferConfigError, load_offer


def write(tmp_path, data):
    path = tmp_path / "offer.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


MINIMAL = {
    "company_name": "Testco",
    "what_you_sell": "We do a thing.",
    "ideal_customer": {"target_titles": ["COO"]},
}


class TestLoad:
    def test_template_loads(self):
        offer = load_offer("config/offer.yaml")
        assert offer.target_titles

    def test_missing_file(self):
        with pytest.raises(OfferConfigError, match="No offer config"):
            load_offer("does/not/exist.yaml")

    def test_missing_required_key(self, tmp_path):
        bad = dict(MINIMAL)
        del bad["what_you_sell"]
        with pytest.raises(OfferConfigError, match="missing required keys"):
            load_offer(write(tmp_path, bad))

    def test_missing_target_titles(self, tmp_path):
        bad = dict(MINIMAL, ideal_customer={})
        with pytest.raises(OfferConfigError, match="target_titles"):
            load_offer(write(tmp_path, bad))

    def test_invalid_yaml(self, tmp_path):
        path = tmp_path / "offer.yaml"
        path.write_text("company_name: [unclosed\n")
        with pytest.raises(OfferConfigError, match="not valid YAML"):
            load_offer(path)

    def test_defaults_applied(self, tmp_path):
        offer = load_offer(write(tmp_path, MINIMAL))
        assert offer.voice.max_email_words == 110
        assert offer.opt_out_line
        assert offer.proof_points == []

    def test_unknown_voice_keys_ignored(self, tmp_path):
        data = dict(MINIMAL, voice={"sender_name": "Leo", "nonsense_key": 1})
        offer = load_offer(write(tmp_path, data))
        assert offer.voice.sender_name == "Leo"


class TestPlaceholders:
    def test_template_flagged(self):
        offer = load_offer("config/offer.yaml")
        assert "company_name" in offer.unfilled_placeholders()

    def test_filled_config_clean(self, tmp_path):
        data = dict(
            MINIMAL,
            company_name="Leo Ops",
            what_you_sell="We take over fulfillment for supplement brands.",
            voice={"sender_name": "Leo", "sender_email": "leo@leoops.com"},
        )
        assert load_offer(write(tmp_path, data)).unfilled_placeholders() == []


class TestPromptRendering:
    def test_offer_block_contains_constraints(self, tmp_path):
        data = dict(MINIMAL, proof_points=[], reference_customers=[])
        block = load_offer(write(tmp_path, data)).as_prompt_block()
        # With no proof points, the prompt must forbid results claims.
        assert "make NO results claims" in block
        assert "do not name any customer" in block

    def test_offer_block_lists_proof(self, tmp_path):
        data = dict(MINIMAL, proof_points=["Cut errors 80%"])
        block = load_offer(write(tmp_path, data)).as_prompt_block()
        assert "Cut errors 80%" in block

    def test_voice_block_has_optout(self, tmp_path):
        offer = load_offer(write(tmp_path, dict(MINIMAL, opt_out_line="Reply no.")))
        assert "Reply no." in offer.as_voice_block()
