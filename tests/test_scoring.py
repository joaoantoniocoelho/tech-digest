from pathlib import Path
import runpy
import unittest

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

import yaml

from app.scoring import calculate_relevance_score


def _load_profile():
    with open("config/interests.yaml") as file:
        return yaml.safe_load(file)


def _strengths(profile, **overrides):
    values = {
        feature_id: 0
        for feature_id in profile["features"]
    }
    values.update(overrides)
    return values


class RelevanceScoringTestCase(unittest.TestCase):
    def setUp(self):
        self.profile = _load_profile()

    def test_single_core_feature_stays_in_band(self):
        score = calculate_relevance_score(
            feature_strengths=_strengths(
                self.profile,
                major_ai_model_development=2,
            ),
            profile=self.profile,
            importance=1,
        )

        self.assertEqual(score, 55)

    def test_zig_like_article_remains_digestible(self):
        score = calculate_relevance_score(
            feature_strengths=_strengths(
                self.profile,
                software_engineering_practices=2,
                programming_languages_frameworks=2,
                developer_tools=1,
                open_source=1,
            ),
            profile=self.profile,
            importance=2,
        )

        self.assertGreaterEqual(score, 60)
        self.assertLess(score, 80)

    def test_three_strong_ai_features_are_high_not_maxed(self):
        score = calculate_relevance_score(
            feature_strengths=_strengths(
                self.profile,
                major_ai_model_development=2,
                ai_agents=2,
                ai_assisted_software_engineering=2,
            ),
            profile=self.profile,
            importance=3,
        )

        self.assertGreaterEqual(score, 75)
        self.assertLess(score, 95)

    def test_stacked_ai_roundup_does_not_hit_ceiling(self):
        score = calculate_relevance_score(
            feature_strengths=_strengths(
                self.profile,
                major_ai_model_development=2,
                ai_agents=2,
                ai_assisted_software_engineering=2,
                software_engineering_practices=2,
                developer_tools=2,
                practical_ai_research=2,
                leading_tech_company_major_development=2,
            ),
            profile=self.profile,
            importance=3,
        )

        self.assertGreaterEqual(score, 75)
        self.assertLess(score, 100)

    def test_more_features_still_rank_higher(self):
        few = calculate_relevance_score(
            feature_strengths=_strengths(
                self.profile,
                ai_agents=2,
            ),
            profile=self.profile,
            importance=2,
        )
        many = calculate_relevance_score(
            feature_strengths=_strengths(
                self.profile,
                ai_agents=2,
                ai_assisted_software_engineering=2,
                software_engineering_practices=2,
            ),
            profile=self.profile,
            importance=2,
        )

        self.assertGreater(many, few)
