import json
import tempfile
import unittest
from pathlib import Path

from src.mini_agent import UserProfile
from src.persona_factory import PersonaFactory, load_population_spec, summarize
from src.problematic_use import activity_baseline


class PersonaFactoryTests(unittest.TestCase):
    def test_cohort_mix_is_proportional_and_profiles_are_valid(self):
        profiles=PersonaFactory({"student":.25,"office_worker":.50,"retired":.25},seed=4).generate(20)
        summary=summarize(profiles)
        self.assertEqual(summary["cohorts"],{"student":5,"office_worker":10,"retired":5})
        self.assertEqual(len({profile["user_id"] for profile in profiles}),20)
        for payload in profiles:
            profile=UserProfile.from_dict(payload)
            self.assertEqual(len(profile.hourly_activity_baseline),24)
            self.assertTrue(profile.identity_summary)
            self.assertEqual(set(profile.personality_traits),{"openness","conscientiousness","extraversion","agreeableness","neuroticism"})

    def test_generation_is_reproducible_but_individuals_vary(self):
        first=PersonaFactory({"student":1},seed=11).generate(10)
        second=PersonaFactory({"student":1},seed=11).generate(10)
        self.assertEqual(first,second)
        self.assertGreater(len({profile["self_control"] for profile in first}),1)
        self.assertGreater(len({profile["demographics"]["age"] for profile in first}),1)

    def test_shift_worker_baseline_treats_late_use_as_more_normal(self):
        shift=PersonaFactory({"shift_worker":1},seed=2).generate(1)[0]
        office=PersonaFactory({"office_worker":1},seed=2).generate(1)[0]
        self.assertGreater(activity_baseline(shift,1),activity_baseline(office,1))

    def test_demographics_do_not_directly_determine_control_traits(self):
        profiles=PersonaFactory({"office_worker":1},seed=8).generate(30)
        by_gender={}
        for profile in profiles: by_gender.setdefault(profile["demographics"]["gender"],set()).add(profile["self_control"])
        common=[values for values in by_gender.values() if len(values)>=3]
        self.assertTrue(common)
        self.assertTrue(all(len(values)>1 for values in common))

    def test_editable_population_spec_overrides_sampling_space(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"population.json"
            path.write_text(json.dumps({"cohort_mix":{"student":1},"cohort_overrides":{"student":{"curiosity":.2,"media":{"autoplay_susceptibility":.1}}}}),encoding="utf-8")
            mix,definitions=load_population_spec(path)
            profiles=PersonaFactory(mix,3,definitions).generate(4)
            self.assertTrue(all(profile["cohort"]=="student" for profile in profiles))
            self.assertLess(sum(profile["curiosity"] for profile in profiles)/4,.6)


if __name__=="__main__": unittest.main()
