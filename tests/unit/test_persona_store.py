import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from src.persona import (
    CapabilitySnapshot,
    IncompatibleVersionError,
    PersonaGenerator,
    PersonaNotFoundError,
    PersonaStore,
    SchemaVersionError,
    TemplateCatalog,
)


class PersonaStoreTests(unittest.TestCase):
    def test_atomic_private_persona_round_trip_and_list(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PersonaStore(directory)
            self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
            persona = PersonaGenerator(TemplateCatalog.default()).create(seed=12, experimental=True)
            path = store.save(persona)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            loaded = store.load(persona.persona_id)
            self.assertEqual(loaded.to_dict(), persona.to_dict())
            self.assertEqual([item.persona_id for item in store.list()], [persona.persona_id])

    def test_schema_and_generator_incompatibility_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PersonaStore(directory)
            persona = PersonaGenerator(TemplateCatalog.default()).create(seed=13, experimental=True)
            path = store.save(persona)
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            data["schema_version"] = 999
            Path(path).write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(SchemaVersionError):
                store.load(persona.persona_id)

            data["schema_version"] = 1
            data["generator_version"] = "99.0.0"
            Path(path).write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(IncompatibleVersionError):
                store.load(persona.persona_id)

    def test_missing_persona_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PersonaStore(directory)
            with self.assertRaises(PersonaNotFoundError):
                store.load("persona_" + "0" * 32)

    def test_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PersonaStore(directory)
            snapshot = CapabilitySnapshot(
                environment={"firefox_version": "154.0.1", "backend": "software/native"},
                capabilities={"ua": "native"},
            )
            store.save_snapshot(snapshot)
            restored = store.load_snapshot(snapshot.fingerprint)
            self.assertEqual(restored.fingerprint, snapshot.fingerprint)
            self.assertEqual(restored.environment, snapshot.environment)


if __name__ == "__main__":
    unittest.main()
