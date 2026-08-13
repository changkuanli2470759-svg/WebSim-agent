import unittest

from src.addiction_metrics import control_failure_probability, score, stop_pressure


def evidence(value: int) -> dict:
    return {
        "exit_intention_opportunities": 5, "failed_exit_count": value,
        "low_drive_opportunities": 5, "low_drive_continuations": value,
        "fatigue_opportunities": 5, "fatigue_continuations": value,
        "low_value_opportunities": 5, "low_value_continuations": value,
        "boredom_opportunities": 5, "boredom_continuations": value,
        "over_budget_opportunities": 5, "over_budget_continuations": value,
        "negative_mood_opportunities": 5, "mood_relief_count": value,
    }


class AddictionMetricTests(unittest.TestCase):
    def test_problematic_persistence_scores_above_controlled_use(self):
        controlled={"addiction_evidence":evidence(0),"high_engagement_streak":0,"value_ema":.7}
        persistent={"addiction_evidence":evidence(5),"high_engagement_streak":10,"value_ema":.2}
        low=score(controlled,action_count=5,session_budget=8)
        high=score(persistent,action_count=20,session_budget=8)
        self.assertLess(low["risk_score"],high["risk_score"])
        self.assertEqual(low["risk_label"],"low_risk")
        self.assertEqual(high["risk_label"],"persistent_high_risk")

    def test_high_engagement_without_control_loss_is_not_high_risk(self):
        behavior={"addiction_evidence":evidence(0),"high_engagement_streak":8,"value_ema":.8}
        report=score(behavior,action_count=12,session_budget=8)
        self.assertEqual(report["risk_label"],"high_engagement")

    def test_unobserved_withdrawal_and_relapse_remain_null(self):
        report=score({"addiction_evidence":evidence(0)},action_count=8,session_budget=8)
        self.assertIsNone(report["dimensions"]["withdrawal"])
        self.assertIsNone(report["dimensions"]["relapse"])
        self.assertLess(report["evidence_coverage"],1)

    def test_stop_pressure_and_control_failure_are_bounded(self):
        pressure=stop_pressure({"fatigue":.9,"boredom":.8,"satisfaction":.1},{"perceived_value":.1},{"consecutive_skips":3})
        probability=control_failure_probability({"self_control":.2,"exploration_tendency":.9},{"engagement":.9},{"consecutive_clicks":5},pressure)
        self.assertGreater(pressure,.6)
        self.assertTrue(0<=probability<=1)


if __name__=="__main__": unittest.main()
