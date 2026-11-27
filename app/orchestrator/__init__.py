from ..agents import AgentRegistry
from ..audit import AuditLogger
from ..config import Settings
from ..llm import LLM
from .collator import ResponseCollator
from .executor import Executor
from .graph import END, StateGraph
from .orchestrator import Orchestrator
from .state import OrchestrationState


def build_graph(llm: LLM, registry: AgentRegistry, audit: AuditLogger, cfg: Settings) -> StateGraph:
    """orchestrator -> executor -> response_collator -> (orchestrator | END)"""
    collator = ResponseCollator(llm, registry, audit, cfg.max_orchestrator_loops, cfg.max_task_attempts)
    g = StateGraph()
    g.add_node("orchestrator", Orchestrator(audit, cfg.max_task_attempts))
    g.add_node("executor", Executor(registry, audit, cfg.agent_timeout_s))
    g.add_node("response_collator", collator)
    g.set_entry_point("orchestrator")
    g.add_edge("orchestrator", "executor")
    g.add_edge("executor", "response_collator")
    g.add_conditional_edges("response_collator", collator.route)
    return g


__all__ = ["END", "OrchestrationState", "StateGraph", "build_graph"]
