from copy import deepcopy
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

    def test_direct_major_model_match_reaches_floor(self):
        score = calculate_relevance_score(
            feature_strengths=_strengths(
                self.profile,
                major_ai_model_development=2,
            ),
            profile=self.profile,
            importance=1,
        )

        self.assertEqual(score, 85)

    def test_strength_one_does_not_trigger_floor(self):
        score = calculate_relevance_score(
            feature_strengths=_strengths(
                self.profile,
                major_ai_model_development=1,
            ),
            profile=self.profile,
            importance=1,
        )

        self.assertEqual(score, 25)

    def test_strength_zero_does_not_trigger_floor(self):
        score = calculate_relevance_score(
            feature_strengths=_strengths(self.profile),
            profile=self.profile,
            importance=1,
        )

        self.assertEqual(score, 0)

    def test_natural_score_above_floor_is_preserved(self):
        strengths = _strengths(
            self.profile,
            major_ai_model_development=2,
            ai_agents=2,
            ai_assisted_software_engineering=2,
            software_engineering_practices=2,
            developer_tools=2,
            practical_ai_research=2,
            leading_tech_company_major_development=2,
        )
        profile_without_floor = deepcopy(self.profile)
        del profile_without_floor["features"][
            "major_ai_model_development"
        ]["direct_match_score_floor"]

        natural_score = calculate_relevance_score(
            strengths, profile_without_floor, importance=3
        )
        score = calculate_relevance_score(
            strengths, self.profile, importance=3
        )

        self.assertGreater(natural_score, 85)
        self.assertEqual(score, natural_score)

    def test_feature_without_floor_keeps_existing_score(self):
        score = calculate_relevance_score(
            feature_strengths=_strengths(self.profile, ai_agents=2),
            profile=self.profile,
            importance=1,
        )

        self.assertEqual(score, 50)

    def test_importance_and_negative_penalty_run_before_floor(self):
        profile = deepcopy(self.profile)
        profile["features"]["major_ai_model_development"][
            "direct_match_score_floor"
        ] = 70
        strengths = _strengths(
            profile,
            major_ai_model_development=2,
            ai_agents=2,
        )

        self.assertEqual(
            calculate_relevance_score(strengths, profile, importance=3),
            77,
        )

        strengths["deep_infrastructure"] = 2
        self.assertEqual(
            calculate_relevance_score(strengths, profile, importance=3),
            70,
        )

        strengths["deep_infrastructure"] = 0
        self.assertEqual(
            calculate_relevance_score(strengths, profile, importance=0),
            70,
        )

    def test_floor_must_be_numeric_and_in_range(self):
        for invalid_floor in (-1, 101, "85", True, None, float("nan")):
            with self.subTest(invalid_floor=invalid_floor):
                profile = deepcopy(self.profile)
                profile["features"]["major_ai_model_development"][
                    "direct_match_score_floor"
                ] = invalid_floor

                with self.assertRaises(ValueError):
                    calculate_relevance_score(
                        _strengths(profile), profile, importance=1
                    )

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
