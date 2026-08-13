import unittest

from src.mini_agent import default_activity_baseline, default_goals
from src.problematic_use import (
    build_daily_session_schedule, commit_session, default_memory, longitudinal_score,
    paired_recommendation_effect, record_step, simulation_clock,
)


def profile():
    return {"hourly_activity_baseline": default_activity_baseline(), "goals": default_goals()}


class ProblematicUseTests(unittest.TestCase):
    def test_daily_schedule_prefers_authored_active_hour_and_is_reproducible(self):
        p=profile(); p["hourly_activity_baseline"]=[.001]*24; p["hourly_activity_baseline"][18]=1.0; p["goals"]=[]
        first=build_daily_session_schedule(p,agent_id="a",seed=9,max_timesteps=24,start_hour=0,timestep_minutes=60,sessions_min=1,sessions_max=1,minimum_gap_steps=1)
        second=build_daily_session_schedule(p,agent_id="a",seed=9,max_timesteps=24,start_hour=0,timestep_minutes=60,sessions_min=1,sessions_max=1,minimum_gap_steps=1)
        self.assertEqual(first,second)
        self.assertEqual(first,[19])

    def record(self, memory, timestep, *, stop=False, continued=True, hour=23.0):
        return record_step(
            memory, agent_id="agent_001", timestep=timestep,
            simulation_time=simulation_clock(timestep, hour, 1), profile=profile(),
            exit_intention=stop, actual_continue=continued, action="click",
            item_id="movie_1", reason="test", recommendation_condition="personalized",
            social_signal_visible=False,
        )

    def test_activity_abnormality_alone_is_high_engagement(self):
        memory=default_memory(); self.record(memory,1,stop=False,continued=True,hour=8)
        report=longitudinal_score(memory)
        self.assertEqual(report["risk_label"],"high_engagement")
        self.assertEqual(report["stop_failure_rate"],None)

    def test_stop_failure_without_goal_conflict_is_watch_state(self):
        memory=default_memory(); self.record(memory,1,stop=True,continued=True,hour=20)
        report=longitudinal_score(memory)
        self.assertEqual(report["risk_label"],"watch_state")
        self.assertEqual(report["stop_failure_rate"],1.0)

    def test_goal_conflict_alone_is_watch_not_high_engagement(self):
        memory=default_memory(); self.record(memory,1,stop=False,continued=True,hour=23)
        self.assertEqual(longitudinal_score(memory)["risk_label"],"watch_state")

    def test_repeated_stop_failure_and_goal_conflict_is_high_risk(self):
        memory=default_memory()
        first=self.record(memory,1,stop=True,continued=True,hour=23)
        self.assertFalse(first["propositions"]["stop_plan_followed"]["holds"])
        self.assertTrue(first["goal_conflict"])
        commit_session(memory,timestep=1,end_reason="temporary_sleep")
        self.record(memory,2,stop=True,continued=True,hour=23)
        commit_session(memory,timestep=2,end_reason="exit")
        report=longitudinal_score(memory)
        self.assertEqual(report["risk_label"],"problematic_use_high_risk")
        self.assertEqual(report["cross_session_persistence"],1.0)

    def test_paired_recommendation_effect_uses_rate_difference(self):
        control={"a":{"stop_failure_rate":.25},"b":{"stop_failure_rate":.50}}
        treatment={"a":{"stop_failure_rate":.75},"b":{"stop_failure_rate":.75}}
        report=paired_recommendation_effect(control,treatment)
        self.assertEqual(report["mean_effect"],.375)
        self.assertTrue(report["recommendation_amplified_risk"])


if __name__ == "__main__": unittest.main()
