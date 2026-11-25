"""Dynamic agent registry and LangGraph builder for Factory Memory.

Constructs LangGraph workflows directly from declarative YAML configuration
(project/core/config.yaml). Adding a new agent requires only declaring its
class and edge definitions in YAML—no graph code edits required.
"""

import importlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from langgraph.graph import END, START, StateGraph
from project.agents.base import BaseAgent
from project.core.state import BlackboardState

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and parse the declarative YAML configuration file."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def instantiate_agent(agent_cfg: Dict[str, Any]) -> BaseAgent:
    """Dynamically load and instantiate an agent class defined in config.yaml."""
    module_name = agent_cfg["module"]
    class_name = agent_cfg["class"]
    params = agent_cfg.get("params", {})
    name = agent_cfg.get("name")

    try:
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name)
        init_params = dict(params)
        if name and "name" not in init_params:
            import inspect
            sig = inspect.signature(cls.__init__)
            if "name" in sig.parameters:
                init_params["name"] = name
        agent = cls(**init_params)
        if agent_cfg.get("stub", False) or ("stubs" in module_name and class_name != "RetrievalNode"):
            agent.is_stub = True
        return agent
    except (ImportError, AttributeError) as e:
        raise ImportError(
            f"Failed to load agent '{agent_cfg.get('name')}' from {module_name}.{class_name}: {e}"
        ) from e


def is_pipeline_stub(
    pipeline: str = "B",
    config_path: Optional[str] = None,
    config_override: Optional[Dict[str, Any]] = None,
) -> bool:
    """Check if the specified pipeline contains any stub / mock agents."""
    cfg = config_override if config_override is not None else load_config(config_path)
    pipe_cfg = cfg.get("pipelines", {}).get(pipeline, {})
    retrieval_backend = (
        "stub"
        if os.getenv("FACTORY_STUB_RETRIEVAL") == "1"
        else cfg.get("system", {}).get("retrieval_backend", "real")
    )
    for a in pipe_cfg.get("agents", []):
        if a.get("enabled", True):
            if a.get("class") == "RetrievalNode":
                if a.get("stub", False) or retrieval_backend == "stub":
                    return True
            elif a.get("stub", False) or "stubs" in a.get("module", ""):
                return True
    return False


def build_graph(
    pipeline: str = "B",
    config_path: Optional[str] = None,
    config_override: Optional[Dict[str, Any]] = None,
):
    """Construct and compile a LangGraph StateGraph from declarative configuration.

    Args:
        pipeline: Target pipeline key ('A' or 'B').
        config_path: Optional custom path to config.yaml.
        config_override: Optional in-memory dictionary overriding the YAML config.

    Returns:
        Compiled LangGraph instance ready for invocation.
    """
    cfg = config_override if config_override is not None else load_config(config_path)
    pipelines = cfg.get("pipelines", {})
    if pipeline not in pipelines:
        raise ValueError(f"Pipeline '{pipeline}' not defined in configuration.")

    pipe_cfg = pipelines[pipeline]
    agents_cfg = pipe_cfg.get("agents", [])
    edges_cfg = pipe_cfg.get("edges", [])
    entrypoint = pipe_cfg.get("entrypoint")

    enabled_agents: Dict[str, BaseAgent] = {}
    for a in agents_cfg:
        if a.get("enabled", True):
            name = a["name"]
            enabled_agents[name] = instantiate_agent(a)

    if not enabled_agents:
        raise ValueError(f"No enabled agents configured for pipeline '{pipeline}'.")

    builder = StateGraph(BlackboardState)

    # Register each agent as a graph node
    for name, agent_instance in enabled_agents.items():
        builder.add_node(name, agent_instance.run)

    # Set workflow entrypoint
    if not entrypoint or entrypoint not in enabled_agents:
        entrypoint = list(enabled_agents.keys())[0]

    builder.add_edge(START, entrypoint)

    # Connect configured edges
    for edge in edges_cfg:
        src = edge["from"]
        if src not in enabled_agents:
            continue

        if "condition" in edge:
            cond_type = edge["condition"]
            if cond_type == "check_clarification":
                raw_true = edge.get("on_true", "__end__")
                raw_false = edge.get("on_false", "__end__")
                true_target = END if raw_true in ("__end__", "END") else raw_true
                false_target = END if raw_false in ("__end__", "END") else raw_false

                def route_clarification(state: BlackboardState) -> str:
                    return "clarify" if state.get("clarification_needed") else "continue"

                builder.add_conditional_edges(
                    src,
                    route_clarification,
                    {
                        "clarify": true_target,
                        "continue": false_target,
                    },
                )
            elif cond_type == "check_abstention":
                raw_true = edge.get("on_true", "__end__")
                raw_false = edge.get("on_false", "__end__")
                true_target = END if raw_true in ("__end__", "END") else raw_true
                false_target = END if raw_false in ("__end__", "END") else raw_false

                def route_abstention(state: BlackboardState) -> str:
                    return "abstain" if state.get("abstained") else "proceed"

                builder.add_conditional_edges(
                    src,
                    route_abstention,
                    {
                        "abstain": true_target,
                        "proceed": false_target,
                    },
                )
            elif cond_type == "check_safety_verdict":
                raw_approve = edge.get("on_approve", "__end__")
                raw_revise = edge.get("on_revise", "__end__")
                raw_exhausted = edge.get("on_exhausted", "__end__")
                max_loops = int(cfg.get("system", {}).get("safety_max_revision_loops", 2))

                approve_target = END if raw_approve in ("__end__", "END") else raw_approve
                revise_target = END if raw_revise in ("__end__", "END") else raw_revise
                exhausted_target = END if raw_exhausted in ("__end__", "END") else raw_exhausted

                def route_safety(state: BlackboardState) -> str:
                    if state.get("safety_approved"):
                        return "approve"
                    loop_count = int(state.get("safety_loop_count", 0))
                    if loop_count < max_loops and not state.get("escalated_to_supervisor"):
                        return "revise"
                    return "exhausted"

                builder.add_conditional_edges(
                    src,
                    route_safety,
                    {
                        "approve": approve_target,
                        "revise": revise_target,
                        "exhausted": exhausted_target,
                    },
                )
        else:
            dst = edge["to"]
            if dst == "__end__" or dst == "END":
                builder.add_edge(src, END)
            elif dst in enabled_agents:
                builder.add_edge(src, dst)

    return builder.compile()
