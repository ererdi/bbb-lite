"""Tests for Stage N's role hooks: hooks/role_declare.py and the role check
added to hooks/pretooluse_log.py.

mc's role functions default to db_path=mc.DB_PATH, and that default is bound
at function-definition time, so patching mc.DB_PATH after import has no
effect on it. _bind_to_test_db() replaces the functions themselves with
functools.partial wrappers pinned to a per-test temp db instead.
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))

import mc
import pretooluse_log
import role_declare


def _bind_to_test_db(db_path: Path) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    for name in ("set_role", "get_role", "can_write_code", "can_write_data"):
        bound = functools.partial(getattr(mc, name), db_path=db_path)
        stack.enter_context(mock.patch(f"mc.{name}", bound))
    return stack


class FindDeclaredRoleTestCase(unittest.TestCase):
    def test_matches_you_are_the_architect(self) -> None:
        self.assertEqual(
            "architect", role_declare.find_declared_role("Ok, you are the architect now.")
        )

    def test_matches_you_are_the_builder_case_insensitively(self) -> None:
        self.assertEqual(
            "builder", role_declare.find_declared_role("YOU ARE THE BUILDER, go implement it")
        )

    def test_matches_turkish_mimarsin(self) -> None:
        self.assertEqual("architect", role_declare.find_declared_role("Artık sen mimarsın."))

    def test_matches_turkish_builder_sin(self) -> None:
        self.assertEqual("builder", role_declare.find_declared_role("sen builder'sın, başla"))

    def test_returns_none_for_unrelated_prompt(self) -> None:
        self.assertIsNone(role_declare.find_declared_role("fix the bug in mc.py please"))


class RoleDeclareMainTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_main_declares_role_for_session(self) -> None:
        payload = json.dumps({"session_id": "s1", "prompt": "you are the builder"})
        with _bind_to_test_db(self.db_path), mock.patch("sys.stdin", io.StringIO(payload)), \
            mock.patch("role_declare.mc.init_db"):
            role_declare.main()
        self.assertEqual("builder", mc.get_role("s1", db_path=self.db_path))

    def test_main_is_silent_with_no_role_match(self) -> None:
        payload = json.dumps({"session_id": "s1", "prompt": "just do the thing"})
        with _bind_to_test_db(self.db_path), mock.patch("sys.stdin", io.StringIO(payload)), \
            mock.patch("role_declare.mc.init_db"), mock.patch("role_declare.mc.set_role") as set_role:
            role_declare.main()
        set_role.assert_not_called()


class PreToolUseRoleCheckTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_path.unlink()
        mc.init_db(self.db_path)

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_default_brozzer_session_is_denied(self) -> None:
        with _bind_to_test_db(self.db_path), mock.patch("pretooluse_log.mc.init_db"):
            result = pretooluse_log.check({"file_path": "foo.py"}, session_id="s1")
        self.assertIsNotNone(result)
        self.assertEqual("deny", result["hookSpecificOutput"]["permissionDecision"])
        self.assertIn("brozzer", result["reason"])

    def test_builder_session_is_allowed(self) -> None:
        mc.set_role("s1", "builder", db_path=self.db_path)
        with _bind_to_test_db(self.db_path), mock.patch("pretooluse_log.mc.init_db"):
            result = pretooluse_log.check({"file_path": "foo.py"}, session_id="s1")
        self.assertIsNone(result)

    def test_no_session_id_falls_back_to_mc_db_check_only(self) -> None:
        result = pretooluse_log.check({"file_path": "foo.py"}, session_id=None)
        self.assertIsNone(result)

    def test_mc_db_is_still_denied_regardless_of_role(self) -> None:
        mc.set_role("s1", "builder", db_path=self.db_path)
        with _bind_to_test_db(self.db_path), mock.patch("pretooluse_log.mc.init_db"):
            result = pretooluse_log.check({"file_path": "mc.db"}, session_id="s1")
        self.assertIsNotNone(result)
        self.assertIn("mc.db", result["reason"])


if __name__ == "__main__":
    unittest.main()
