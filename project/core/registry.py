"""Dynamic agent registry and LangGraph builder for Factory Memory.

Constructs LangGraph workflows directly from declarative YAML configuration
(project/core/config.yaml). Adding a new agent requires only declaring its
class and edge definitions in YAML—no graph code edits required.
"""

import importlib
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

    try:
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name)
        agent = cls()
        if agent_cfg.get("stub", False) or "stubs" in module_name:
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
    for a in pipe_cfg.get("agents", []):
        if a.get("enabled", True):
            if a.get("stub", False) or "stubs" in a.get("module", ""):
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
        dst = edge["to"]

        if src not in enabled_agents:
            continue

        if dst == "__end__" or dst == "END":
            builder.add_edge(src, END)
        elif dst in enabled_agents:
            builder.add_edge(src, dst)

    return builder.compile()
