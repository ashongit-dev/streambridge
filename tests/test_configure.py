import json
from pathlib import Path
import tempfile
import unittest
from configure import configure


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'mediamtx.yml').write_text('api: yes\npaths:\n  desk:\n    source: publisher\n')

    def render(self, password):
        (self.root / '.env').write_text('INGEST_PASSWORD=' + password + '\n')
        path = configure(self.root)
        users = json.loads(path.read_text().split('authInternalUsers: ', 1)[1])
        return path, users

    def test_explicit_roles_and_permissions(self):
        path, users = self.render('a' * 48)
        self.assertEqual([u['user'] for u in users], ['broadcaster', 'relay'])
        self.assertEqual(users[0]['permissions'], [{'action':'publish','path':'desk'}, {'action':'publish','path':'irl'}])
        self.assertEqual(users[1]['permissions'], [{'action':'read'}, {'action':'api'}])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_credentials_are_escaped_without_changing_the_password(self):
        password = 'password-with-"quotes"-and-\\backslash'
        _, users = self.render(password)
        self.assertTrue(all(u['pass'] == password for u in users))

    def test_short_password_rejected_before_file_is_created(self):
        (self.root / '.env').write_text('INGEST_PASSWORD=short\n')
        with self.assertRaises(ValueError):
            configure(self.root)
        self.assertFalse((self.root / '.runtime/mediamtx.yml').exists())

    def test_password_update_preserves_bind_mount_inode(self):
        path, _ = self.render('a' * 48)
        inode = path.stat().st_ino
        _, users = self.render('b' * 48)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertTrue(all(u['pass'] == 'b' * 48 for u in users))


if __name__ == '__main__':
    unittest.main()
