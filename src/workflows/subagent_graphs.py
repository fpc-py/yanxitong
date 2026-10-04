# Sub-agent graph definitions for individual agent workflows.

from langgraph.graph import StateGraph, END
from src.workflows.state import ResearchState
from src.agents.retriever.agent import RetrieverAgent
from src.agents.kg_builder.agent import KGBuilderAgent


async def retrieve_and_build_kg(state: ResearchState) -> ResearchState:
    agent = RetrieverAgent()
    result = await agent.execute(state)
    if result.success and result.data:
        state["literature_results"] = result.data.get("papers", [])
        if result.citations:
            state["citation_chain"].extend(result.citations)
    if state.get("literature_results"):
        kg_agent = KGBuilderAgent()
        await kg_agent.execute(state)
    return state


def build_literature_pipeline() -> StateGraph:
    workflow = StateGraph(ResearchState)
    workflow.add_node("retrieve_and_kg", retrieve_and_build_kg)
    workflow.set_entry_point("retrieve_and_kg")
    workflow.add_edge("retrieve_and_kg", END)
    return workflow.compile()