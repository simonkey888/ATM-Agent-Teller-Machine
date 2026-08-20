from __future__ import annotations

import os
import unittest
from pathlib import Path


class PublisherStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.server = (root / "src" / "atm_publish_server.py").read_text()
        cls.client = (root / "src" / "atm_publish_client.py").read_text()
        cls.runtime = (root / "src" / "atm_v2_oci.py").read_text()
        cls.service = (root / "deploy" / "oci" / "atm-publisher.service").read_text()
        cls.cloudinit = (root / "deploy" / "oci" / "cloud-init.sh").read_text()
        cls.configure = (root / "scripts" / "oci-configure.sh").read_text()
        cls.worker_prompt = (root / "prompts" / "WORKER.md").read_text()

    def test_token_lives_only_in_privileged_publisher_side(self):
        self.assertIn('EnvironmentFile=/etc/atm/control.env', self.service)
        self.assertIn('GITHUB_TOKEN', self.server)
        self.assertNotIn('GITHUB_TOKEN', self.client)
        self.assertNotIn('GITHUB_TOKEN', self.runtime)
        self.assertIn('Never search for or use host GitHub credentials', self.worker_prompt)

    def test_publisher_only_accepts_atm_worker_workspace_shape(self):
        self.assertIn('WORKSPACES = Path("/var/lib/atm/state/workspaces")', self.server)
        self.assertIn('path.name != "worker"', self.server)
        self.assertIn('workspace outside ATM workspaces', self.server)
        self.assertIn('MAX_TOTAL_BYTES', self.server)
        self.assertIn('EXCLUDED_FILES', self.server)

    def test_publisher_repo_is_deterministic_public_and_non_destructive(self):
        self.assertIn('atm-deliverable-', self.server)
        self.assertIn('"private": False', self.server)
        self.assertIn('"auto_init": True', self.server)
        self.assertIn('"force": False', self.server)
        self.assertIn('deterministic repository name collision', self.server)

    def test_unix_socket_is_group_restricted_and_traversable(self):
        self.assertIn('os.chmod(SOCKET_PATH, 0o660)', self.server)
        self.assertIn('grp.getgrnam("atm")', self.server)
        self.assertIn('RuntimeDirectoryMode=0755', self.service)
        self.assertIn('User=root', self.service)
        self.assertIn('User=atm', (Path(__file__).resolve().parents[1] / 'deploy' / 'oci' / 'atm-supervisor.service').read_text())

    def test_bootstrap_installs_and_starts_publisher(self):
        self.assertIn('atm-publisher.service', self.cloudinit)
        self.assertIn('enable --now atm-publisher.service atm-controller.service', self.configure)
        self.assertIn('public_repo scope', self.configure)

    def test_local_file_handoff_is_replaced_before_check(self):
        self.assertIn('raw.startswith("file://")', self.runtime)
        self.assertIn('publish_workspace(', self.runtime)
        self.assertIn('state.active_opportunity.deliverable_url = url', self.runtime)
        self.assertIn('file://<absolute worker workspace path>', self.worker_prompt)


@unittest.skipIf(os.name == "nt", "publisher server is Linux-only")
class PublisherLogicTests(unittest.TestCase):
    def test_repo_name_is_stable_and_bounded(self):
        import atm_publish_server as server
        a = server.repo_name('workprotocol:abc')
        b = server.repo_name('workprotocol:abc')
        self.assertEqual(a, b)
        self.assertTrue(a.startswith('atm-deliverable-'))
        self.assertLessEqual(len(a), 40)


if __name__ == "__main__":
    unittest.main()
