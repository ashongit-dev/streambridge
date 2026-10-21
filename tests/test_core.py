import unittest
from app.policy import choose_source
from app.health import FeedHealth

class RoutingTests(unittest.TestCase):
    def test_all_routing_combinations(self):
        for preferred in ('desk', 'irl', 'fallback'):
            for automatic in (True, False):
                for desk in (True, False):
                    for irl in (True, False):
                        with self.subTest(preferred=preferred, automatic=automatic, desk=desk, irl=irl):
                            healthy = dict(desk=desk, irl=irl)
                            selected = choose_source(preferred, automatic, healthy)
                            if preferred == 'fallback':
                                self.assertEqual(selected, 'fallback')
                            elif healthy[preferred]:
                                self.assertEqual(selected, preferred)
                            elif automatic and healthy['irl' if preferred == 'desk' else 'desk']:
                                self.assertEqual(selected, 'irl' if preferred == 'desk' else 'desk')
                            else:
                                self.assertEqual(selected, 'fallback')

class HealthTests(unittest.TestCase):
    def test_stable_recovery(self):
        h = FeedHealth()
        self.assertFalse(h.update(True, 100, True, 0))
        self.assertFalse(h.update(True, 200, True, 1))
        self.assertTrue(h.update(True, 300, True, 2))
    def test_stall_even_when_publisher_is_connected(self):
        h = FeedHealth()
        h.update(True, 100, True, 0)
        self.assertTrue(h.update(True, 200, True, 2))
        self.assertFalse(h.update(True, 200, True, 6))
    def test_disconnection_immediate_and_recovery_delayed(self):
        h = FeedHealth()
        h.update(True, 100, True, 0)
        self.assertTrue(h.update(True, 200, True, 2))
        self.assertFalse(h.update(False, 200, True, 3))
        self.assertFalse(h.update(True, 10, True, 4))
        self.assertTrue(h.update(True, 300, True, 6))
    def test_decoder_must_be_playing(self):
        h = FeedHealth()
        self.assertFalse(h.update(True, 100, False, 0))
        self.assertFalse(h.update(True, 200, False, 5))
    def test_no_data_is_unhealthy(self):
        h = FeedHealth()
        self.assertFalse(h.update(True, 0, True, 0))
        self.assertFalse(h.update(True, 0, True, 3))
    def test_flapping_does_not_recover(self):
        h = FeedHealth()
        for i in range(20):
            self.assertFalse(h.update(i % 2 == 0, i * 100, True, i))

if __name__ == '__main__':
    unittest.main()
