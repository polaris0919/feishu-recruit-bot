"""v3.8.5: lib.recruit_paths 多环境查找顺序回归。"""
import os
import unittest
from unittest import mock

from lib import recruit_paths


class TestEnvSuffixed(unittest.TestCase):
    def test_prod_returns_original(self):
        self.assertEqual(
            recruit_paths._env_suffixed("talent-db-config.json", "prod"),
            "talent-db-config.json",
        )

    def test_empty_env_returns_original(self):
        self.assertEqual(
            recruit_paths._env_suffixed("talent-db-config.json", ""),
            "talent-db-config.json",
        )

    def test_dev_suffix_inserted_before_extension(self):
        self.assertEqual(
            recruit_paths._env_suffixed("talent-db-config.json", "dev"),
            "talent-db-config.dev.json",
        )

    def test_staging_suffix_inserted(self):
        self.assertEqual(
            recruit_paths._env_suffixed("openclaw.json", "staging"),
            "openclaw.staging.json",
        )

    def test_no_extension_appends(self):
        self.assertEqual(
            recruit_paths._env_suffixed("config", "dev"),
            "config.dev",
        )


class TestRecruitEnv(unittest.TestCase):
    def test_default_is_prod(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RECRUIT_ENV", None)
            self.assertEqual(recruit_paths.recruit_env(), "prod")

    def test_explicit_dev(self):
        with mock.patch.dict(os.environ, {"RECRUIT_ENV": "dev"}):
            self.assertEqual(recruit_paths.recruit_env(), "dev")

    def test_whitespace_and_case_normalized(self):
        with mock.patch.dict(os.environ, {"RECRUIT_ENV": "  Staging  "}):
            self.assertEqual(recruit_paths.recruit_env(), "staging")

    def test_empty_falls_back_to_prod(self):
        with mock.patch.dict(os.environ, {"RECRUIT_ENV": ""}):
            self.assertEqual(recruit_paths.recruit_env(), "prod")


class TestConfigCandidates(unittest.TestCase):
    def test_prod_returns_two_paths(self):
        # prod 下没有 env 后缀, 期望恰好 2 个路径（scripts 根 + config/）
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RECRUIT_ENV", None)
            paths = recruit_paths.config_candidates("talent-db-config.json")
        names = [p.name for p in paths]
        self.assertEqual(names.count("talent-db-config.json"), 2)
        self.assertNotIn("talent-db-config.prod.json", names)

    def test_dev_returns_four_paths_env_first(self):
        with mock.patch.dict(os.environ, {"RECRUIT_ENV": "dev"}):
            paths = recruit_paths.config_candidates("talent-db-config.json")
        names = [p.name for p in paths]
        # 期望顺序：env-suffix (scripts) → 通用 (scripts) → env-suffix (config) → 通用 (config)
        self.assertEqual(names, [
            "talent-db-config.dev.json",
            "talent-db-config.json",
            "talent-db-config.dev.json",
            "talent-db-config.json",
        ])
        # 前 2 路径在 scripts 根, 后 2 路径在 config 目录
        self.assertEqual(paths[0].parent, paths[1].parent)
        self.assertEqual(paths[2].parent, paths[3].parent)
        self.assertNotEqual(paths[0].parent, paths[2].parent)


if __name__ == "__main__":
    unittest.main()
