import os
import runpy
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

import yaml
from typesafe_sdk import TypeSafeClient

from app.classifier import (
    CONSERVATIVE_FEATURE_RUBRIC,
    IMPORTANCE_ID,
    IMPORTANCE_INSTRUCTIONS,
    _build_questions,
    _build_why_and_topics,
    classify_article,
    discretize_feature_score,
    discretize_importance,
    select_top_positive_features,
)
from app.digest import render_text_digest


def _load_profile():
    with open("config/interests.yaml") as file:
        return yaml.safe_load(file)


def _fake_score_answer(
    score,
    confidence=0.85,
    probabilities=None,
):
    answer = MagicMock()
    answer.score = score
    answer.confidence = confidence
    answer.probabilities = probabilities or {}
    return answer


def _fake_response(scores_by_id):
    response = MagicMock()
    response.scores = scores_by_id
    return response


def _complete_scores(features, overrides=None):
    scores = {
        feature_id: _fake_score_answer(0.0)
        for feature_id in features
    }
    scores[IMPORTANCE_ID] = _fake_score_answer(1.0)

    if overrides:
        scores.update(overrides)

    return scores


def _zero_strengths(features):
    return {
        feature_id: 0
        for feature_id in features
    }


class FeatureConfigTestCase(unittest.TestCase):
    def test_every_feature_has_label_and_boundaries(self):
        profile = _load_profile()
        features = profile["features"]

        self.assertGreater(len(features), 0)

        for feature_id, feature in features.items():
            self.assertTrue(
                str(feature.get("label", "")).strip(),
                msg=f"{feature_id} missing label",
            )
            self.assertTrue(
                str(feature.get("description", "")).strip(),
                msg=f"{feature_id} missing description",
            )
            self.assertTrue(
                str(feature.get("include_when", "")).strip(),
                msg=f"{feature_id} missing include_when",
            )
            self.assertTrue(
                str(feature.get("exclude_when", "")).strip(),
                msg=f"{feature_id} missing exclude_when",
            )
            self.assertIn("weight", feature)


class QuestionConstructionTestCase(unittest.TestCase):
    def test_one_score_per_feature_plus_importance(self):
        profile = _load_profile()
        features = profile["features"]
        questions = _build_questions(features)

        self.assertEqual(
            len(questions),
            len(features) + 1,
        )
        self.assertEqual(
            set(questions.keys()),
            set(features.keys()) | {IMPORTANCE_ID},
        )
        self.assertEqual(
            list(questions.keys()).count(IMPORTANCE_ID),
            1,
        )

        for feature_id in features:
            question = questions[feature_id]
            self.assertEqual(question.type, "score")
            self.assertEqual(len(question.criteria), 3)
            self.assertIn(
                CONSERVATIVE_FEATURE_RUBRIC,
                question.instructions,
            )
            self.assertIn(
                "LEVELS:",
                question.instructions,
            )
            self.assertIn(
                features[feature_id]["include_when"].strip().split("\n")[0][:40],
                question.instructions,
            )
            self.assertIn(
                features[feature_id]["exclude_when"].strip().split("\n")[0][:40],
                question.instructions,
            )

        importance = questions[IMPORTANCE_ID]
        self.assertEqual(len(importance.criteria), 4)
        self.assertEqual(
            importance.instructions,
            IMPORTANCE_INSTRUCTIONS,
        )
        self.assertIn("3 should be rare", importance.instructions)
        self.assertIn(
            "choose the lower level",
            importance.instructions,
        )


class DiscretizationTestCase(unittest.TestCase):
    def test_feature_score_boundaries(self):
        self.assertEqual(discretize_feature_score(0.00), 0)
        self.assertEqual(discretize_feature_score(0.49), 0)
        self.assertEqual(discretize_feature_score(0.63), 0)
        self.assertEqual(discretize_feature_score(0.74), 0)
        self.assertEqual(discretize_feature_score(0.75), 1)
        self.assertEqual(discretize_feature_score(1.49), 1)
        self.assertEqual(discretize_feature_score(1.50), 2)
        self.assertEqual(discretize_feature_score(2.00), 2)

    def test_importance_boundaries(self):
        self.assertEqual(discretize_importance(0.00), 0)
        self.assertEqual(discretize_importance(0.49), 0)
        self.assertEqual(discretize_importance(0.50), 1)
        self.assertEqual(discretize_importance(1.49), 1)
        self.assertEqual(discretize_importance(1.50), 2)
        self.assertEqual(discretize_importance(2.49), 2)
        self.assertEqual(discretize_importance(2.50), 3)
        self.assertEqual(discretize_importance(3.00), 3)


class WhyAndTopicsTestCase(unittest.TestCase):
    def test_why_max_three_positive_by_contribution(self):
        profile = _load_profile()
        features = profile["features"]
        strengths = _zero_strengths(features)
        strengths["major_ai_model_development"] = 2
        strengths["ai_agents"] = 2
        strengths["developer_tools"] = 2
        strengths["software_engineering_practices"] = 1
        strengths["crypto_web3"] = 2
        strengths["generic_consumer_tech"] = 1

        top_ids = select_top_positive_features(
            feature_strengths=strengths,
            features=features,
        )
        why, topics = _build_why_and_topics(
            feature_strengths=strengths,
            features=features,
        )

        self.assertEqual(
            top_ids,
            [
                "major_ai_model_development",
                "ai_agents",
                "developer_tools",
            ],
        )
        self.assertEqual(
            why,
            "AI models · AI agents · Developer tools",
        )
        self.assertEqual(topics, why.split(" · "))
        self.assertEqual(len(topics), 3)
        self.assertNotIn("Crypto & Web3", why)
        self.assertNotIn("Generic consumer tech", why)
        self.assertNotIn("Software engineering", why)

    def test_strength_zero_and_negatives_omitted(self):
        profile = _load_profile()
        features = profile["features"]
        strengths = _zero_strengths(features)
        strengths["crypto_web3"] = 2
        strengths["security"] = 0

        why, topics = _build_why_and_topics(
            feature_strengths=strengths,
            features=features,
        )

        self.assertEqual(why, "")
        self.assertEqual(topics, [])

    def test_tie_breaks_by_id(self):
        profile = _load_profile()
        features = profile["features"]
        strengths = _zero_strengths(features)
        strengths["ai_agents"] = 2
        strengths["software_engineering_practices"] = 2
        strengths["developer_tools"] = 2

        why, topics = _build_why_and_topics(
            feature_strengths=strengths,
            features=features,
        )

        self.assertEqual(
            why,
            "AI agents · Software engineering · Developer tools",
        )
        self.assertEqual(topics, why.split(" · "))


class ClassifyArticleTestCase(unittest.TestCase):
    def setUp(self):
        self.profile = _load_profile()
        self.env_patcher = patch.dict(
            os.environ,
            {
                "TYPESAFE_API_KEY": "test-key",
                "TYPESAFE_MODEL": "jev-1.13.0",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def _mock_client(self, mock_get_client, scores):
        mock_client = MagicMock(spec=TypeSafeClient)
        mock_client.system_one.return_value = (
            _fake_response(scores)
        )
        mock_get_client.return_value = mock_client
        return mock_client

    @patch("app.classifier._get_client")
    def test_classify_article_state_and_shape(
        self,
        mock_get_client,
    ):
        features = self.profile["features"]
        scores = _complete_scores(
            features,
            {
                "ai_agents": _fake_score_answer(1.6),
                "software_engineering_practices": (
                    _fake_score_answer(1.4)
                ),
                "crypto_web3": _fake_score_answer(1.8),
                IMPORTANCE_ID: _fake_score_answer(2.1),
            },
        )
        mock_client = self._mock_client(
            mock_get_client,
            scores,
        )

        result = classify_article(
            title="Agent tooling for developers",
            content="Article body text.",
            profile=self.profile,
        )

        mock_client.system_one.assert_called_once()
        call_kwargs = mock_client.system_one.call_args.kwargs

        self.assertEqual(
            call_kwargs["state"],
            {
                "title": "Agent tooling for developers",
                "content": "Article body text.",
            },
        )
        self.assertNotIn("source", call_kwargs["state"])
        self.assertEqual(
            set(call_kwargs["state"].keys()),
            {"title", "content"},
        )
        self.assertEqual(
            set(call_kwargs["questions"].keys()),
            set(features.keys()) | {IMPORTANCE_ID},
        )
        self.assertEqual(
            call_kwargs["model"],
            "jev-1.13.0",
        )

        self.assertEqual(
            set(result.keys()),
            {
                "feature_strengths",
                "importance",
                "why_interesting",
                "topics",
            },
        )
        self.assertEqual(
            set(result["feature_strengths"].keys()),
            set(features.keys()),
        )
        self.assertEqual(
            result["feature_strengths"]["ai_agents"],
            2,
        )
        self.assertEqual(
            result["feature_strengths"][
                "software_engineering_practices"
            ],
            1,
        )
        self.assertEqual(
            result["feature_strengths"]["crypto_web3"],
            2,
        )
        self.assertEqual(result["importance"], 2)
        self.assertEqual(
            result["why_interesting"],
            "AI agents · Software engineering",
        )
        self.assertEqual(
            result["topics"],
            ["AI agents", "Software engineering"],
        )
        self.assertNotIn(
            "Crypto & Web3",
            result["why_interesting"],
        )

    @patch("app.classifier._get_client")
    def test_classify_article_reads_model_from_env(
        self,
        mock_get_client,
    ):
        scores = _complete_scores(
            self.profile["features"]
        )
        mock_client = self._mock_client(
            mock_get_client,
            scores,
        )

        with patch.dict(
            os.environ,
            {"TYPESAFE_MODEL": "jev-pinned-test"},
            clear=False,
        ):
            classify_article(
                title="T",
                content="C",
                profile=self.profile,
            )

        self.assertEqual(
            mock_client.system_one.call_args.kwargs[
                "model"
            ],
            "jev-pinned-test",
        )

    @patch("app.classifier._get_client")
    def test_missing_feature_score_raises(
        self,
        mock_get_client,
    ):
        features = self.profile["features"]
        scores = _complete_scores(features)
        del scores["ai_agents"]
        self._mock_client(mock_get_client, scores)

        with self.assertRaises(ValueError) as context:
            classify_article(
                title="T",
                content="C",
                profile=self.profile,
            )

        self.assertIn("ai_agents", str(context.exception))
        self.assertIn("Missing", str(context.exception))

    @patch("app.classifier._get_client")
    def test_non_numeric_score_raises(
        self,
        mock_get_client,
    ):
        features = self.profile["features"]
        scores = _complete_scores(
            features,
            {
                "ai_agents": _fake_score_answer("high"),
            },
        )
        self._mock_client(mock_get_client, scores)

        with self.assertRaises(ValueError) as context:
            classify_article(
                title="T",
                content="C",
                profile=self.profile,
            )

        self.assertIn("ai_agents", str(context.exception))

    def test_missing_api_key_raises(self):
        with patch.dict(
            os.environ,
            {"TYPESAFE_API_KEY": ""},
            clear=False,
        ):
            with self.assertRaises(ValueError) as context:
                classify_article(
                    title="T",
                    content="C",
                    profile=self.profile,
                )

            self.assertEqual(
                str(context.exception),
                "TYPESAFE_API_KEY is not configured",
            )


class DigestWhyPrefixTestCase(unittest.TestCase):
    def test_render_prefixes_why_interesting(self):
        output = render_text_digest(
            digest={
                "lookback_hours": 24,
                "articles": [
                    {
                        "title": "Example",
                        "relevance_score": 80,
                        "why_interesting": (
                            "AI agents · Developer tools"
                        ),
                        "source": "Test",
                        "topics": ["AI agents"],
                        "url": "https://example.com",
                    }
                ],
            },
            show_score=False,
            show_topics=False,
        )

        self.assertIn(
            "Why: AI agents · Developer tools",
            output,
        )

    def test_render_skips_empty_why(self):
        output = render_text_digest(
            digest={
                "lookback_hours": 24,
                "articles": [
                    {
                        "title": "Example",
                        "relevance_score": 80,
                        "why_interesting": "   ",
                        "source": "Test",
                        "topics": [],
                        "url": "https://example.com",
                    }
                ],
            },
            show_score=False,
            show_topics=False,
        )

        self.assertNotIn("Why:", output)


class RuntimeCleanupTestCase(unittest.TestCase):
    def test_runtime_has_no_ollama_or_batch_artifacts(self):
        paths = [
            Path("compose.yaml"),
            Path("requirements.txt"),
            *Path("app").glob("*.py"),
        ]

        forbidden = (
            "OLLAMA_URL",
            "OLLAMA_MODEL",
            "PROCESSING_BATCH_SIZE",
            "ROW_NUMBER() OVER",
            "PARTITION BY source",
            "qwen3.5:9b",
            "CLASSIFIER_BACKEND",
        )

        for path in paths:
            source = path.read_text()

            for token in forbidden:
                self.assertNotIn(
                    token,
                    source,
                    msg=f"{token} found in {path}",
                )


if __name__ == "__main__":
    unittest.main()
