"""Episode-scoped graph edges avoid joining facts from unrelated repairs.

Shared entity nodes (machine, asset, catalogue_hazard) connect episodes; every
edge still carries its episode/evidence IDs. Catalogue hazard links come from A4
(provenance.entity_links) and mean "this warning may involve hazard Hxx".
"""

import networkx as nx
from project.store.models import VerifiedEpisode, label_key

KINDS = {"machine", "asset", "fault", "cause", "fix", "hazard", "catalogue_hazard", "failed_attempt", "outcome"}
SHARED = {"machine", "asset", "catalogue_hazard"}


class KnowledgeGraph:
    def __init__(self, episodes: list[VerifiedEpisode]):
        self.graph = nx.MultiDiGraph()
        for episode in episodes:
            self._add(episode)

    def _add(self, episode):
        r = episode.record
        g = self.graph
        machine = ("machine", label_key(r.machine_type))
        g.add_node(machine, kind="machine", label=r.machine_type)
        root = ("episode", episode.episode_id)
        g.add_node(root, kind="episode", label=episode.episode_id)
        edge = {"episode_id": episode.episode_id, "evidence_id": r.evidence_id}
        g.add_edge(machine, root, relation="has_episode", **edge)
        if r.machine_id:
            asset = ("asset", label_key(r.machine_id))
            g.add_node(asset, kind="asset", label=r.machine_id)
            g.add_edge(machine, asset, relation="has_asset", **edge)
            g.add_edge(asset, root, relation="has_episode", **edge)

        def node(kind, index, label):
            key = (kind, episode.episode_id, str(index))
            g.add_node(key, kind=kind, label=label)
            g.add_edge(root, key, relation=f"has_{kind}", **edge)
            return key

        cause = node("cause", 0, r.root_cause)
        for index, symptom in enumerate(r.symptoms):
            fault = node("fault", index, symptom)
            g.add_edge(machine, fault, relation="exhibited", **edge)
            g.add_edge(fault, cause, relation="caused_by", **edge)
        fixes = [node("fix", i, step.description) for i, step in enumerate(r.steps)]
        for fix in fixes:
            g.add_edge(cause, fix, relation="addressed_by", **edge)
        hazards = []
        for index, warning in enumerate(r.safety_warnings):
            hazard = node("hazard", index, warning)
            hazards.append(hazard)
            for fix in fixes:
                g.add_edge(fix, hazard, relation="has_episode_warning", **edge)
        for link in episode.provenance.entity_links:
            if link.kind == "hazard" and link.status == "resolved":
                index = int(link.field_path.rsplit(".", 1)[1])
                if index < len(hazards):
                    ref = ("catalogue_hazard", label_key(link.entity_id))
                    g.add_node(ref, kind="catalogue_hazard", label=link.entity_id)
                    g.add_edge(hazards[index], ref, relation="may_involve", method=link.method, **edge)
        for index, attempt in enumerate(r.failed_attempts):
            key = node("failed_attempt", index, attempt.action)
            g.nodes[key].update(outcome=attempt.outcome, why_failed=attempt.why_failed)
        if episode.outcome:
            node("outcome", 0, episode.outcome)

    def find(self, kind: str, label: str) -> set[str]:
        if kind not in KINDS:
            raise ValueError(f"Unknown graph kind: {kind}")
        result = set()
        for key, data in self.graph.nodes(data=True):
            if data["kind"] == kind and label_key(data["label"]) == label_key(label):
                if kind in SHARED:  # Shared node: collect episodes from incident edges.
                    for _, _, edge in self.graph.in_edges(key, data=True):
                        result.add(edge["episode_id"])
                    for _, _, edge in self.graph.out_edges(key, data=True):
                        result.add(edge["episode_id"])
                else:
                    result.add(key[1])
        return result
