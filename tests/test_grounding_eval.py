"""
Opt-in live test to evaluate whether the agent correctly uses `retrieve_content`.
"""

import os
import json
import unittest

import config

_SKIP_REASON = "set RUN_LIVE_GROQ_CHECKS=1 to run live grounding eval"

@unittest.skipUnless(os.environ.get("RUN_LIVE_GROQ_CHECKS"), _SKIP_REASON)
class TestGroundingEval(unittest.TestCase):
    def setUp(self):
        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "grounding_eval.json")
        with open(fixture_path, "r") as f:
            self.cases = json.load(f)

    def test_grounding_calibration(self):
        from graph.workflow import tutor_graph
        from services.turn_service import build_initial_state
        
        passes = 0
        total = len(self.cases)
        failures = []

        import uuid
        from db.supabase_client import db
        test_user_id = "8f25e78e-114b-4731-b9b9-6a30354581f1"

        for case in self.cases:
            query = case["query"]
            should_retrieve = case["should_retrieve"]
            
            # Create a fresh session for each test case
            session = db.create_session(test_user_id)
            test_session_id = session["session_id"]
            
            # Build initial state
            state = build_initial_state(
                user_id=test_user_id,
                session_id=test_session_id,
                query=query,
                carry={},
                graded_overrides={},
                roadmap_node_id=None,
                roadmap_concept_id=None,
                study_stage=None,
                journey_type=None,
                focus_chapters=None
            )
            
            # Invoke the graph
            result = tutor_graph.invoke(state)
            trace_path = result.get("agent_trace_path", [])
            
            # Check if retrieve_content was called
            did_retrieve = "tool:retrieve_content" in trace_path
            
            passed = (did_retrieve == should_retrieve)
            
            if passed:
                passes += 1
                print(f"PASS [{case['id']}]: '{query}' -> retrieved={did_retrieve} (expected {should_retrieve})")
            else:
                failures.append(case['id'])
                print(f"FAIL [{case['id']}]: '{query}' -> retrieved={did_retrieve} (expected {should_retrieve})")
                
        pass_rate = passes / total if total > 0 else 0.0
        print(f"\n--- GROUNDING EVAL RESULTS ---")
        print(f"Pass Rate: {pass_rate:.1%} ({passes}/{total})")
        if failures:
            print(f"Failed cases: {', '.join(failures)}")
            
        # Assert a 90% pass rate
        self.assertGreaterEqual(pass_rate, 0.90, f"Grounding pass rate below 90%: {pass_rate:.1%}")

if __name__ == "__main__":
    unittest.main()
