import unittest

from claude_usage.collector import UsageStats
from claude_usage.notifier import CrossingDetector, UsageNotifier


class TestCrossingDetector(unittest.TestCase):
    def test_first_call_never_fires(self):
        d = CrossingDetector([0.75, 0.90])
        self.assertEqual(d.check("session", 0.95), [])

    def test_single_threshold_crossed(self):
        d = CrossingDetector([0.75, 0.90])
        d.check("session", 0.50)
        self.assertEqual(d.check("session", 0.80), [0.75])

    def test_multiple_thresholds_crossed_in_one_step(self):
        d = CrossingDetector([0.75, 0.90])
        d.check("session", 0.50)
        self.assertEqual(d.check("session", 0.95), [0.75, 0.90])

    def test_no_repeat_when_staying_above_threshold(self):
        d = CrossingDetector([0.75, 0.90])
        d.check("session", 0.50)
        d.check("session", 0.80)
        self.assertEqual(d.check("session", 0.85), [])

    def test_reset_re_arms_threshold(self):
        d = CrossingDetector([0.75])
        d.check("session", 0.50)
        d.check("session", 0.80)  # fires
        d.check("session", 0.05)  # reset, no fire
        self.assertEqual(d.check("session", 0.80), [0.75])

    def test_scopes_are_independent(self):
        d = CrossingDetector([0.75])
        d.check("session", 0.50)
        d.check("weekly", 0.50)
        self.assertEqual(d.check("session", 0.80), [0.75])
        self.assertEqual(d.check("weekly", 0.60), [])

    def test_invalid_thresholds_are_dropped(self):
        d = CrossingDetector([0.0, 0.5, 1.5, -0.1])
        self.assertEqual(d.thresholds, [0.5])

    def test_exact_threshold_value_counts_as_crossing(self):
        d = CrossingDetector([0.75])
        d.check("session", 0.50)
        self.assertEqual(d.check("session", 0.75), [0.75])


class TestUsageNotifier(unittest.TestCase):
    def _notifier(self, **overrides):
        cfg = {"notifications_enabled": True, "notify_thresholds": [0.75]}
        cfg.update(overrides)
        sent = []
        n = UsageNotifier(cfg, sender=lambda title, body: sent.append((title, body)))
        return n, sent

    def test_fires_for_session_and_weekly(self):
        n, sent = self._notifier(notify_thresholds=[0.75])
        n.check_stats(UsageStats(session_utilization=0.50, weekly_utilization=0.50))
        n.check_stats(UsageStats(session_utilization=0.80, weekly_utilization=0.80))
        titles = [t for t, _ in sent]
        self.assertEqual(len(titles), 2)
        self.assertTrue(any("Session" in t for t in titles))
        self.assertTrue(any("Weekly" in t for t in titles))

    def test_disabled_sends_nothing(self):
        n, sent = self._notifier(notifications_enabled=False)
        n.check_stats(UsageStats(session_utilization=0.50))
        n.check_stats(UsageStats(session_utilization=0.95))
        self.assertEqual(sent, [])

    def test_no_fire_on_first_observation(self):
        n, sent = self._notifier()
        n.check_stats(UsageStats(session_utilization=0.95, weekly_utilization=0.95))
        self.assertEqual(sent, [])

    def test_message_body_mentions_threshold(self):
        n, sent = self._notifier(notify_thresholds=[0.75])
        n.check_stats(UsageStats(session_utilization=0.50))
        n.check_stats(UsageStats(session_utilization=0.80))
        self.assertEqual(len(sent), 1)
        title, body = sent[0]
        self.assertIn("80%", title)
        self.assertIn("75%", body)


class TestCheckSnapshots(unittest.TestCase):
    def _notifier(self, **overrides):
        cfg = {"notifications_enabled": True, "notify_thresholds": [0.75]}
        cfg.update(overrides)
        sent = []
        n = UsageNotifier(cfg, sender=lambda title, body: sent.append((title, body)))
        return n, sent

    def _snaps(self, premium):
        from claude_usage.providers.base import Meter, ProviderSnapshot
        return [
            ProviderSnapshot("anthropic", "CLAUDE", meters=[
                Meter("session", "Session", utilization=0.5),
            ]),
            ProviderSnapshot("copilot", "COPILOT", meters=[
                Meter("premium", "Premium", utilization=premium),
            ]),
        ]

    def test_copilot_premium_crossing_fires(self):
        n, sent = self._notifier()
        n.check_snapshots(self._snaps(0.50))
        n.check_snapshots(self._snaps(0.80))
        titles = [t for t, _ in sent]
        self.assertEqual(len(titles), 1)
        self.assertIn("Copilot", titles[0])
        self.assertIn("Premium", titles[0])

    def test_unlimited_meter_never_fires(self):
        from claude_usage.providers.base import Meter, ProviderSnapshot
        n, sent = self._notifier()
        snaps = [ProviderSnapshot("copilot", "COPILOT", meters=[
            Meter("premium", "Premium", unlimited=True),
        ])]
        n.check_snapshots(snaps)
        n.check_snapshots(snaps)
        self.assertEqual(sent, [])

    def test_provider_scopes_are_independent(self):
        # Anthropic session and Copilot premium must not share crossing state.
        from claude_usage.providers.base import Meter, ProviderSnapshot
        n, sent = self._notifier()

        def snaps(sess, prem):
            return [
                ProviderSnapshot("anthropic", "CLAUDE",
                                 meters=[Meter("session", "Session", utilization=sess)]),
                ProviderSnapshot("copilot", "COPILOT",
                                 meters=[Meter("premium", "Premium", utilization=prem)]),
            ]

        n.check_snapshots(snaps(0.50, 0.50))
        n.check_snapshots(snaps(0.80, 0.60))  # only session crosses 0.75
        self.assertEqual(len(sent), 1)
        self.assertIn("Claude", sent[0][0])


if __name__ == "__main__":
    unittest.main()
