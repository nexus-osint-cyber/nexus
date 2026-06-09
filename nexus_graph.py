"""
NEXUS – Graph-Analyse  (Palantir-Ebene: Netzwerkanalyse)
=========================================================
Analysiert Beziehungen zwischen Entitäten mit NetworkX.

Features:
  • Betweenness-Zentralität (wer ist der wichtigste Knoten?)
  • Community-Erkennung (welche Gruppen bilden sich?)
  • Kürzester Pfad zwischen zwei Akteuren (6-Grad-Verbindung)
  • Netzwerk-Statistiken (Dichte, Cluster)
  • vis.js Export mit Farb-Kodierung nach Typ

Abhängigkeiten:
  pip install networkx --break-system-packages
  (optional: python-louvain für bessere Community-Erkennung)

Öffentliche API:
  get_graph()                         → NexusGraph
  NexusGraph.get_vis_data(...)        → Dict (nodes/edges für vis.js)
  NexusGraph.analyze_entity(id)       → Dict (vollständige Analyse)
  NexusGraph.get_network_stats()      → Dict
  NexusGraph.find_path(a, b)          → List[str] | None
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from nexus_entities import get_tracker, ENTITY_TYPES, EntityTracker

# NetworkX optional – graceful degradation wenn nicht installiert
try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False

# Farben je Entitätstyp (NEXUS Dark-Theme)
TYPE_COLORS: Dict[str, Dict] = {
    "PERSON":       {"bg": "#3b82f6", "border": "#1d4ed8", "highlight": "#60a5fa"},
    "ORGANIZATION": {"bg": "#8b5cf6", "border": "#6d28d9", "highlight": "#a78bfa"},
    "LOCATION":     {"bg": "#10b981", "border": "#065f46", "highlight": "#34d399"},
    "VEHICLE":      {"bg": "#f59e0b", "border": "#b45309", "highlight": "#fbbf24"},
    "AIRCRAFT":     {"bg": "#06b6d4", "border": "#0891b2", "highlight": "#22d3ee"},
    "VESSEL":       {"bg": "#0ea5e9", "border": "#0284c7", "highlight": "#38bdf8"},
    "WEAPON":       {"bg": "#ef4444", "border": "#b91c1c", "highlight": "#f87171"},
    "EVENT":        {"bg": "#f97316", "border": "#c2410c", "highlight": "#fb923c"},
    "UNKNOWN":      {"bg": "#64748b", "border": "#475569", "highlight": "#94a3b8"},
}


class NexusGraph:
    """Maltego-Style Graphanalyse auf NEXUS-Entitätsdaten."""

    def __init__(self):
        self.tracker: EntityTracker = get_tracker()

    # ── NetworkX-Graph erstellen ──────────────────────────────────────────────

    def _build_nx_graph(
        self,
        entity_ids: List[str] = None,
        min_strength: float = 0.25,
        max_nodes: int = 200,
    ) -> Optional["nx.Graph"]:
        if not HAS_NX:
            return None

        G = nx.Graph()
        vis = self.tracker.get_entity_network(
            entity_id=entity_ids[0] if entity_ids and len(entity_ids) == 1 else None,
            min_strength=min_strength,
            max_nodes=max_nodes,
        )

        for node in vis["nodes"]:
            G.add_node(
                node["id"],
                label=node["label"],
                entity_type=node.get("group", "UNKNOWN"),
                weight=node.get("value", 1),
            )
        for edge in vis["edges"]:
            if edge["from"] in G and edge["to"] in G:
                G.add_edge(
                    edge["from"], edge["to"],
                    rel_type=edge.get("label", ""),
                    weight=max(0.01, edge.get("value", 0.3)),
                )
        return G

    # ── Analysemethoden ───────────────────────────────────────────────────────

    def get_centrality(
        self, G: "nx.Graph" = None
    ) -> Dict[str, float]:
        """Betweenness-Zentralität: wer ist der Schlüssel-Knoten?"""
        if not HAS_NX:
            return {}
        if G is None:
            G = self._build_nx_graph()
        if G is None or len(G) < 2:
            return {}
        try:
            return nx.betweenness_centrality(G, weight="weight", normalized=True)
        except Exception:
            return {}

    def get_communities(
        self, G: "nx.Graph" = None
    ) -> List[List[str]]:
        """Community-Erkennung: welche Gruppen bilden sich?"""
        if not HAS_NX:
            return []
        if G is None:
            G = self._build_nx_graph()
        if G is None or len(G) < 3:
            return []
        try:
            # Louvain falls vorhanden, sonst greedy modularity
            try:
                from networkx.algorithms.community import louvain_communities
                comms = louvain_communities(G, weight="weight", seed=42)
            except Exception:
                from networkx.algorithms.community import greedy_modularity_communities
                comms = greedy_modularity_communities(G)
            return [list(c) for c in sorted(comms, key=len, reverse=True)]
        except Exception:
            return []

    def find_path(
        self, entity_a: str, entity_b: str
    ) -> Optional[List[str]]:
        """Kürzester Verbindungsweg zwischen zwei Entitäten."""
        G = self._build_nx_graph(min_strength=0.1)
        if not HAS_NX or G is None:
            return None
        try:
            path = nx.shortest_path(G, entity_a, entity_b, weight=None)
            # IDs → Namen auflösen
            result = []
            for eid in path:
                e = self.tracker.get_entity(eid)
                result.append(e["name"] if e else eid)
            return result
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def get_influential_nodes(self, top_n: int = 10) -> List[Dict]:
        """Die einflussreichsten Knoten im Netzwerk."""
        G = self._build_nx_graph()
        centrality = self.get_centrality(G)
        if not centrality:
            # Fallback: nach mention_count sortieren
            return self.tracker.get_all_entities(limit=top_n)

        top = sorted(centrality.items(), key=lambda x: -x[1])[:top_n]
        result = []
        for eid, score in top:
            e = self.tracker.get_entity(eid)
            if e:
                e["centrality"] = round(score, 4)
                result.append(e)
        return result

    # ── vis.js Export ─────────────────────────────────────────────────────────

    def get_vis_data(
        self,
        focus_entity: str = None,
        min_strength: float = 0.2,
        max_nodes: int = 120,
        highlight_communities: bool = True,
    ) -> Dict:
        """Gibt vis.js-kompatible nodes/edges zurück."""
        raw = self.tracker.get_entity_network(
            entity_id=focus_entity,
            min_strength=min_strength,
            max_nodes=max_nodes,
        )

        nodes = raw["nodes"]
        edges = raw["edges"]
        node_ids = {n["id"] for n in nodes}

        # Community-Farben überlagernd wenn gewünscht
        community_map: Dict[str, int] = {}
        if highlight_communities and HAS_NX and len(nodes) >= 3:
            G = self._build_nx_graph(min_strength=min_strength, max_nodes=max_nodes)
            comms = self.get_communities(G)
            for ci, comm in enumerate(comms):
                for eid in comm:
                    community_map[eid] = ci

        # Farbpalette für Communities
        comm_palette = [
            "#3b82f6", "#8b5cf6", "#10b981", "#f59e0b",
            "#ef4444", "#06b6d4", "#f97316", "#ec4899",
            "#14b8a6", "#84cc16",
        ]

        styled_nodes = []
        for n in nodes:
            etype  = n.get("group", "UNKNOWN")
            colors = TYPE_COLORS.get(etype, TYPE_COLORS["UNKNOWN"])
            node   = {**n}

            if highlight_communities and n["id"] in community_map:
                ci  = community_map[n["id"]] % len(comm_palette)
                col = comm_palette[ci]
                node["color"] = {
                    "background":  col + "cc",
                    "border":      col,
                    "highlight":   {"background": col, "border": "#ffffff"},
                }
            else:
                node["color"] = {
                    "background":  colors["bg"] + "cc",
                    "border":      colors["border"],
                    "highlight":   {"background": colors["highlight"], "border": "#ffffff"},
                }

            # Focus-Entität hervorheben
            if focus_entity and n["id"] == focus_entity:
                node["font"] = {"size": 16, "bold": True}
                node["borderWidth"] = 3

            styled_nodes.append(node)

        # Kanten filtern
        styled_edges = [
            e for e in edges
            if e["from"] in node_ids and e["to"] in node_ids
        ]

        return {
            "nodes": styled_nodes,
            "edges": styled_edges,
            "meta": {
                "node_count":      len(styled_nodes),
                "edge_count":      len(styled_edges),
                "community_count": max(community_map.values()) + 1 if community_map else 0,
                "has_networkx":    HAS_NX,
                "focus":           focus_entity,
            },
        }

    # ── Entität-Detail-Analyse ────────────────────────────────────────────────

    def analyze_entity(self, entity_id: str) -> Dict:
        """Vollständige Analyse einer einzelnen Entität."""
        e = self.tracker.get_entity(entity_id)
        if not e:
            return {"error": f"Entität '{entity_id}' nicht gefunden"}

        pol      = self.tracker.get_pattern_of_life(entity_id)
        timeline = self.tracker.get_entity_timeline(entity_id, days=30)
        network  = self.get_vis_data(focus_entity=entity_id, min_strength=0.15,
                                     max_nodes=50, highlight_communities=False)

        # Kürzeste Pfade zu Top-5-Entitäten
        top_ents   = self.tracker.get_all_entities(limit=6)
        connections: List[Dict] = []
        for te in top_ents:
            if te["id"] == entity_id:
                continue
            path = self.find_path(entity_id, te["id"])
            if path:
                connections.append({
                    "target": te["name"],
                    "target_id": te["id"],
                    "hops": len(path) - 1,
                    "path": path,
                })
        connections.sort(key=lambda x: x["hops"])

        return {
            "entity":          e,
            "pattern_of_life": pol,
            "recent_sightings": timeline[:12],
            "network":         network,
            "connections":     connections[:5],
        }

    # ── Netzwerk-Statistiken ──────────────────────────────────────────────────

    def get_network_stats(self) -> Dict:
        """Gesamtstatistik des Netzwerks."""
        stats = self.tracker.get_stats()

        if HAS_NX:
            G = self._build_nx_graph(min_strength=0.15)
            if G and len(G) > 1:
                try:
                    centrality = self.get_centrality(G)
                    top_central = sorted(centrality.items(), key=lambda x: -x[1])[:5]
                    top_named = []
                    for eid, sc in top_central:
                        e = self.tracker.get_entity(eid)
                        if e:
                            top_named.append({
                                "name": e["name"], "type": e["type"],
                                "centrality": round(sc, 4),
                            })

                    comms = self.get_communities(G)
                    stats.update({
                        "graph_nodes":        len(G.nodes),
                        "graph_edges":        len(G.edges),
                        "network_density":    round(nx.density(G), 5),
                        "community_count":    len(comms),
                        "top_central":        top_named,
                        "networkx_available": True,
                    })
                except Exception:
                    pass
        else:
            stats["networkx_available"] = False

        return stats

    # ── Maltego-Stil: Schnellsuche ────────────────────────────────────────────

    def expand_entity(self, entity_id: str, depth: int = 1) -> Dict:
        """Expandiert Knoten bis zu `depth` Hops (Maltego-Stil)."""
        visited: set = {entity_id}
        frontier: List[str] = [entity_id]

        for _ in range(depth):
            next_frontier: List[str] = []
            net = self.tracker.get_entity_network(entity_id=frontier[0] if len(frontier)==1 else None,
                                                   min_strength=0.2, max_nodes=50)
            for e in net["edges"]:
                for eid in (e["from"], e["to"]):
                    if eid not in visited:
                        visited.add(eid)
                        next_frontier.append(eid)
            frontier = next_frontier
            if not frontier:
                break

        # Vollständiges Sub-Netzwerk aller gefundenen Entitäten
        all_ids = list(visited)
        return self.get_vis_data(
            focus_entity=entity_id,
            min_strength=0.1,
            max_nodes=len(all_ids) + 10,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Globale Singleton-Instanz
# ─────────────────────────────────────────────────────────────────────────────

_graph_instance: Optional[NexusGraph] = None


def get_graph() -> NexusGraph:
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = NexusGraph()
    return _graph_instance


# ─────────────────────────────────────────────────────────────────────────────
# CLI-Test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("NEXUS Graph-Analyse Test")
    print("=" * 40)
    g = get_graph()

    stats = g.get_network_stats()
    print(f"  Entitäten: {stats['total_entities']}")
    print(f"  Beziehungen: {stats['total_relationships']}")
    print(f"  NetworkX: {'✅' if HAS_NX else '❌ (pip install networkx)'}")

    if stats.get("top_central"):
        print("\n  Schlüssel-Akteure (Betweenness-Zentralität):")
        for e in stats["top_central"]:
            icon = ENTITY_TYPES.get(e["type"], "❓")
            print(f"    {icon} {e['name']} – {e['centrality']:.4f}")

    vis = g.get_vis_data(max_nodes=20)
    print(f"\n  vis.js Export: {vis['meta']['node_count']} Knoten, "
          f"{vis['meta']['edge_count']} Kanten, "
          f"{vis['meta']['community_count']} Communities")
