import os
import time
from aria.main import bootstrap
bootstrap()
from aria.core.agent import agent
from aria.introspection.meta import run_meta_introspection
from aria.rootcause.hypotheses import generate_hypotheses

def generate_real_data():
    print("Generating failures...")
    # Run the sabotaged tools a few times to generate failure history
    for _ in range(5):
        try:
            agent.run_tool("search_tool", {"query": "test"})
        except Exception:
            pass
        try:
            agent.run_tool("weather_tool", {"city": "London"})
        except Exception:
            pass
        
    print("Running meta introspection...")
    run_meta_introspection(n_cycles=10)
    
    print("Generating hypotheses...")
    generate_hypotheses()
    
    print("Triggering improvement cycle...")
    # Disable actual git deployments to keep things simple for the script if we want,
    # but the checklist requires observing a real cycle. We'll let it do its thing.
    # The agent will pick the hypothesis generated above and fix the tool.
    
    # We will trigger the improvement engine on whatever it selects next.
    # We just run one auto cycle.
    success = agent.run_improvement_cycle(target_tool=None)
    print(f"Cycle completed. Success={success}")

if __name__ == "__main__":
    generate_real_data()
