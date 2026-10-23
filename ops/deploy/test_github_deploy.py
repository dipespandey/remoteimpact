import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('github-deploy')
SHA = 'a' * 40
COMMAND = f'cd /opt/easyclaw && git -C repo fetch origin {SHA} && /opt/easyclaw/deploy.sh --ref {SHA}'

class GithubDeployTests(unittest.TestCase):
    def invoke(self, command, main=SHA):
        with patch.dict(os.environ, {'SSH_ORIGINAL_COMMAND': command}), patch('subprocess.run') as run, patch('subprocess.check_output', return_value=main + '\n'):
            runpy.run_path(str(SCRIPT), run_name='__main__')
            return [c.args[0] for c in run.call_args_list]

    def test_exact_main_sha_is_deployed(self):
        calls = self.invoke(COMMAND)
        self.assertEqual(calls[-1], ['/opt/easyclaw/deploy.sh', '--ref', SHA])

    def test_migration_approval_is_explicit(self):
        calls = self.invoke(COMMAND + ' --allow-migrations')
        self.assertEqual(calls[-1][-1], '--allow-migrations')

    def test_superseded_sha_is_rejected(self):
        with self.assertRaisesRegex(SystemExit, 'superseded'):
            self.invoke(COMMAND, main='b' * 40)

    def test_arbitrary_command_is_rejected(self):
        for command in ['id', COMMAND + '; id', COMMAND.replace(SHA, 'main'), COMMAND.replace('--ref', '--allow-migrations --ref')]:
            with self.subTest(command=command), self.assertRaisesRegex(SystemExit, 'Only the Remote Impact'):
                self.invoke(command)
