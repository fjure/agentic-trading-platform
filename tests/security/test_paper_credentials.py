"""Synthetic-only POSIX checks; runnable with stdlib unittest inside offline Linux."""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from services.execution.paper_credentials import (
    PaperCredentialsUnavailable,
    PaperGenerationReader,
    PrivatePaperCredentials,
)
from services.setup.store import ProviderStore, SetupInvalid, SetupUnavailable, generation_filename

CANARY = "synthetic-generation-canary-not-a-provider-key"
ACCOUNT = "11111111-2222-4333-8444-555555555555"
VALUE = {"apiKey": CANARY, "apiSecret": CANARY + "-secret",
         "expectedAccountId": ACCOUNT, "paperOnly": True}


class GenerationValueTests(unittest.TestCase):
    def test_names_reject_paths_bool_noninteger_and_unbounded_generation(self):
        for value in [0, -1, True, "1", "../../credentials", 9007199254740992, None]:
            with self.subTest(value=value), self.assertRaises(SetupInvalid):
                generation_filename(value)
        self.assertEqual(generation_filename(1), "credentials-generation-0000000000000001.json")

    def test_private_value_is_immutable_and_repr_is_redacted(self):
        value = PrivatePaperCredentials(1, CANARY, CANARY + "-secret", ACCOUNT)
        self.assertNotIn(CANARY, repr(value))
        self.assertNotIn(ACCOUNT, repr(value))
        for key in ["api_key", "api_secret", "expected_account_id"]:
            with self.assertRaises(AttributeError):
                setattr(value, key, "changed")
        for secret in ["", " space ", "line\nbreak", "unicode\u202econtrol", 42]:
            with self.assertRaises(PaperCredentialsUnavailable) as error:
                PrivatePaperCredentials(1, secret, CANARY, ACCOUNT)
            self.assertEqual(str(error.exception), "CREDENTIALS_MISSING")


@unittest.skipUnless(os.name == "posix", "POSIX checks run in the offline Linux container")
class GenerationFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "alpaca").mkdir(mode=0o700)
        self.store = ProviderStore(self.root, owner_uid=os.getuid())
        self.reader = PaperGenerationReader(self.root, owner_uid=os.getuid())

    def target(self, generation=1):
        return self.root / "alpaca" / generation_filename(generation)

    def save(self, generation=1, value=VALUE):
        return self.store.save_generation("alpaca", generation, value)

    def test_new_generation_preserves_legacy_and_older_recovery_files(self):
        self.store.save("alpaca", VALUE)
        legacy = self.root / "alpaca" / "credentials.json"
        previous = legacy.read_bytes()
        self.save()
        old_generation = self.target().read_bytes()
        self.save(2, VALUE | {"apiKey": "synthetic-rotated-canary"})
        self.assertEqual(legacy.read_bytes(), previous)
        self.assertEqual(self.target().read_bytes(), old_generation)
        self.assertEqual(self.reader.load(1).api_key, CANARY)
        self.assertEqual(self.reader.load(2).api_key, "synthetic-rotated-canary")
        self.assertEqual(self.target().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.target().stat().st_nlink, 1)
        self.assertEqual(list(self.target().parent.glob(".*.tmp")), [])

    def test_existing_generation_is_never_replaced(self):
        self.save()
        old = self.target().stat().st_ino, self.target().read_bytes()
        self.assertEqual(self.save(value=VALUE | {"apiKey": "synthetic-other"}),
                         {"status": "CONFIGURED", "credentialGeneration": 1})
        self.assertEqual((self.target().stat().st_ino, self.target().read_bytes()), old)

    def test_concurrent_retries_preserve_exactly_one_published_value(self):
        def attempt(index):
            try:
                self.save(value=VALUE | {"apiKey": "synthetic-" + str(index)})
                return index
            except SetupUnavailable:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = [index for index in pool.map(attempt, [1, 2]) if index is not None]
        self.assertGreaterEqual(len(winners), 1)
        self.assertIn(self.reader.load(1).api_key, {"synthetic-1", "synthetic-2"})
        self.assertEqual(self.target().stat().st_nlink, 1)
        self.assertEqual(list(self.target().parent.glob(".*.tmp")), [])

    def test_missing_generation_does_not_fall_back_to_legacy(self):
        self.store.save("alpaca", VALUE)
        with self.assertRaises(PaperCredentialsUnavailable):
            self.reader.load(1)

    def test_unsafe_final_targets_cannot_be_read_or_overwritten(self):
        for kind in ["symlink", "hardlink", "mode", "directory", "fifo", "empty", "oversized"]:
            with self.subTest(kind=kind):
                protected = self.root / "protected"
                protected.write_text(CANARY)
                protected.chmod(0o600)
                target = self.target()
                if kind == "symlink":
                    target.symlink_to(protected)
                elif kind == "hardlink":
                    os.link(protected, target)
                elif kind == "directory":
                    target.mkdir(mode=0o700)
                elif kind == "fifo":
                    os.mkfifo(target, mode=0o600)
                else:
                    target.write_text("" if kind == "empty" else "x" * (
                        16385 if kind == "oversized" else 1
                    ))
                    target.chmod(0o644 if kind == "mode" else 0o600)
                with self.assertRaises(PaperCredentialsUnavailable):
                    self.reader.load(1)
                with self.assertRaises(SetupUnavailable):
                    self.save()
                self.assertEqual(protected.read_text(), CANARY)
                if target.is_dir():
                    target.rmdir()
                else:
                    target.unlink()

    def test_private_directory_owner_and_symlinks_fail_closed(self):
        with self.assertRaises(PaperCredentialsUnavailable):
            PaperGenerationReader(self.root, owner_uid=os.getuid() + 1).load(1)
        directory = self.root / "alpaca"
        directory.chmod(0o750)
        with self.assertRaises(SetupUnavailable):
            self.save()
        directory.chmod(0o700)
        directory.rename(self.root / "other")
        directory.symlink_to(self.root / "other", target_is_directory=True)
        with self.assertRaises(SetupUnavailable):
            self.save()
        with self.assertRaises(PaperCredentialsUnavailable):
            self.reader.load(1)

    def test_parse_corruption_and_extra_fields_are_redacted(self):
        self.save()
        for raw in ['{"api_key":"' + CANARY + '","api_key":"duplicate"}',
                    json.dumps({"api_key": CANARY, "api_secret": CANARY,
                                "expected_account_id": ACCOUNT, "unexpected": CANARY}),
                    CANARY, "[]"]:
            self.target().write_text(raw)
            with self.assertRaises(PaperCredentialsUnavailable) as error:
                self.reader.load(1)
            self.assertNotIn(CANARY, str(error.exception))

    def test_failure_before_publication_preserves_prior_generation(self):
        self.save()
        original = self.target().read_bytes()
        with patch("services.setup.store.os.link", side_effect=OSError(CANARY)):
            with self.assertRaises(SetupUnavailable) as error:
                self.save(2)
        self.assertNotIn(CANARY, str(error.exception))
        self.assertEqual(self.target().read_bytes(), original)
        self.assertFalse(self.target(2).exists())
        self.assertEqual(list(self.target().parent.glob(".*.tmp")), [])

    def test_incomplete_link_publication_is_unreadable(self):
        self.save()
        os.link(self.target(), self.root / "alpaca" / ".interrupted.tmp")
        with self.assertRaises(PaperCredentialsUnavailable):
            self.reader.load(1)

    def test_mutation_during_read_cannot_produce_credentials(self):
        self.save()
        original_read = os.read
        mutated = False

        def read(descriptor, size):
            nonlocal mutated
            result = original_read(descriptor, size)
            if not mutated:
                mutated = True
                self.target().write_text("{}")
            return result

        with patch("services.execution.paper_credentials.os.read", side_effect=read):
            with self.assertRaises(PaperCredentialsUnavailable):
                self.reader.load(1)


if __name__ == "__main__":
    unittest.main()
