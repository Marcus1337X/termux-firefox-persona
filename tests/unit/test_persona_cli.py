"""CLI lifecycle routing does not bypass requalification policy."""
import unittest
from unittest import mock

from src.persona import cli


class PersonaCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_requalify_routes_saved_id_without_experimental_start(self):
        identity = "persona_" + "a" * 32
        args = cli.parser().parse_args(["--root", "/private/personas", "requalify", identity])
        manager = mock.Mock()
        report = mock.Mock(to_dict=mock.Mock(return_value={"passed": True}))
        manager.requalify = mock.AsyncMock(return_value=report)
        manager.start = mock.AsyncMock()
        with mock.patch.object(cli, "PersonaManager", return_value=manager):
            self.assertEqual(await cli.run(args), {"passed": True})
        manager.requalify.assert_awaited_once_with(identity)
        manager.start.assert_not_awaited()
        manager.create.assert_not_called()
