"""
Visualization for BehaviorGraph.

Renders a sample's behavior graph to a PNG using NetworkX + matplotlib.
Auto-selects rendering strategy by graph size:

    < 200 nodes     full graph, all labels, spring layout
    200-1000 nodes  full graph, labels on top-degree only, kamada_kawai
    > 1000 nodes    k-hop subgraph from highest-out-degree node, labeled

Color discipline: stable per-type palette, colorblind-safe (Okabe-Ito)
for nodes. Edge colors use tab10 (no 12-color colorblind palette exists;
edge type is secondary signal in most figures).

Usage:
    from graph.visualize import render
    render(graph, "out.png")

Or via to_networkx if you want to customize:
    nxg = graph.to_networkx()
    # ... your own matplotlib code ...
"""
from __future__ import annotations

# Backend MUST be set before pyplot import. We render to PNG, never to
# an interactive window; Agg is the right call for scripted use on
# headless and Windows alike.
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402

from pathlib import Path  # noqa: E402

from graph.builder import BehaviorGraph  # noqa: E402
from graph.types import EDGE_TYPES, NODE_TYPES  # noqa: E402


# Okabe-Ito colorblind-safe palette. 8 distinct hues + grey for "other".
# Reference: Okabe & Ito (2008), "Color Universal Design".
_OKABE_ITO = [
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#009E73",  # bluish green
    "#F0E442",  # yellow
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#CC79A7",  # reddish purple
    "#000000",  # black
]
_GREY = "#999999"

# Stable assignments. Order chosen so the most-common types get the
# most-distinct hues, and "external" (rare, conceptually 'other') gets
# the grey fallback.
NODE_COLORS: dict[str, str] = {
    "process":      _OKABE_ITO[4],  # blue
    "file":         _OKABE_ITO[0],  # orange
    "registry_key": _OKABE_ITO[2],  # green
    "domain":       _OKABE_ITO[6],  # purple
    "ip":           _OKABE_ITO[1],  # sky blue
    "module":       _OKABE_ITO[5],  # vermillion
    "directory":    _OKABE_ITO[3],  # yellow
    "named_pipe":   _OKABE_ITO[7],  # black
    "external":     _GREY,
}

# Edge colors: tab10 cycled. Less critical than node colors -- edges
# are typically the secondary signal in a behavior graph figure. We
# accept the colorblind compromise here; if it becomes a problem we
# can switch to a 12-color hand-designed palette.
_TAB10 = plt.get_cmap("tab10").colors
EDGE_COLORS: dict[str, tuple] = {
    et: _TAB10[i % len(_TAB10)]
    for i, et in enumerate(sorted(EDGE_TYPES))
}

# Sanity: every known type must have a color.
assert set(NODE_COLORS.keys()) == NODE_TYPES, "NODE_COLORS out of sync with NODE_TYPES"
assert set(EDGE_COLORS.keys()) == EDGE_TYPES, "EDGE_COLORS out of sync with EDGE_TYPES"


# Tier thresholds.
_SMALL_MAX = 200
_MEDIUM_MAX = 1000


def render(
    graph: BehaviorGraph,
    output_path: str | Path,
    *,
    title: str | None = None,
    subgraph_root: int | None = None,
    k_hops: int = 2,
    figsize: tuple[float, float] = (12.0, 9.0),
    dpi: int = 120,
) -> Path:
    """Render `graph` to `output_path` as PNG.

    Auto-selects strategy by graph size unless `subgraph_root` is given,
    in which case a k-hop subgraph centered on that node_id is rendered
    regardless of total size.

    Returns the resolved output path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    nxg = graph.to_networkx()
    n = nxg.number_of_nodes()

    if subgraph_root is not None:
        nxg, sub_note = _k_hop_subgraph(nxg, subgraph_root, k_hops)
        tier = "subgraph"
    elif n == 0:
        tier = "empty"
        sub_note = ""
    elif n < _SMALL_MAX:
        tier = "small"
        sub_note = ""
    elif n < _MEDIUM_MAX:
        tier = "medium"
        sub_note = ""
    else:
        # Auto k-hop from highest-out-degree node (usually root process).
        root = max(nxg.nodes, key=lambda nid: nxg.out_degree(nid))
        nxg, sub_note = _k_hop_subgraph(nxg, root, k_hops)
        tier = "large_truncated"

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    if tier == "empty":
        ax.text(
            0.5, 0.5, "(empty graph)",
            ha="center", va="center", transform=ax.transAxes,
            fontsize=14, color="#666666",
        )
        ax.set_axis_off()
    else:
        _draw(nxg, ax, tier=tier)

    full_title = title or f"BehaviorGraph: {graph.sample_id or '(unnamed)'}"
    if sub_note:
        full_title = f"{full_title}\n{sub_note}"
    full_title = (
        f"{full_title}\n"
        f"nodes={graph.n_nodes}, edges={graph.n_edges}, tier={tier}"
    )
    ax.set_title(full_title, fontsize=10)

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _k_hop_subgraph(
    nxg: nx.MultiDiGraph, root: int, k: int,
) -> tuple[nx.MultiDiGraph, str]:
    """Return a k-hop ego subgraph (forward edges) plus a caption note."""
    # Forward k-hop: BFS over out-edges. We use the underlying directed
    # graph; ego_graph respects directionality with `undirected=False`.
    sub_nodes = nx.ego_graph(
        nxg, root, radius=k, undirected=False,
    ).nodes
    sub = nxg.subgraph(sub_nodes).copy()
    note = (
        f"showing {k}-hop subgraph from node {root} "
        f"({sub.number_of_nodes()}/{nxg.number_of_nodes()} nodes)"
    )
    return sub, note


def _draw(nxg: nx.MultiDiGraph, ax, *, tier: str) -> None:
    """Draw nxg onto ax using tier-specific styling."""
    n = nxg.number_of_nodes()

    # Layout choice:
    #   small     -> spring (good for cluster reveal at low n)
    #   medium    -> kamada_kawai (smoother for dense graphs)
    #   subgraph/ -> spring with k tuned for compactness
    #   large
    # Spring layout across all tiers. Originally medium used kamada_kawai
    # for "smoother dense graphs", but it produced collapsed wedge layouts
    # on samples with disconnected components (2 stray processes pull the
    # rest of the graph into a corner). Spring handles disconnected
    # components more gracefully and gives more predictable output.
    # `k` controls ideal spring length; smaller k = tighter clusters.
    k_param = None if n < 50 else 1.5 / (n ** 0.5)
    pos = nx.spring_layout(nxg, k=k_param, seed=42)

    node_colors = [
        NODE_COLORS.get(nxg.nodes[nid]["type"], _GREY)
        for nid in nxg.nodes
    ]
    edge_colors = [
        EDGE_COLORS.get(nxg.edges[u, v, k]["edge_type"], _GREY)
        for u, v, k in nxg.edges(keys=True)
    ]

    # Edge alpha: full for small graphs, faded for crowded ones.
    edge_alpha = 0.85 if tier == "small" else 0.35

    # Node size: scale down as graph grows so labels can fit.
    if tier == "small":
        node_size = 380
    elif tier == "medium":
        node_size = 140
    else:  # subgraph / large_truncated
        node_size = 220

    nx.draw_networkx_nodes(
        nxg, pos, ax=ax, node_color=node_colors,
        node_size=node_size, linewidths=0.5, edgecolors="#222222",
    )
    nx.draw_networkx_edges(
        nxg, pos, ax=ax, edge_color=edge_colors,
        alpha=edge_alpha, arrowsize=8, width=0.8,
        connectionstyle="arc3,rad=0.08",  # curve parallel edges apart
    )

    # Labels: all for small, top-degree only for bigger tiers.
    if tier == "small":
        labels = {
            nid: _short_label(nxg.nodes[nid])
            for nid in nxg.nodes
        }
    else:
        # Top 15 by total degree.
        top = sorted(
            nxg.nodes,
            key=lambda nid: nxg.degree(nid),
            reverse=True,
        )[:15]
        labels = {nid: _short_label(nxg.nodes[nid]) for nid in top}

    nx.draw_networkx_labels(
        nxg, pos, labels=labels, ax=ax, font_size=7,
    )

    _draw_legends(ax, nxg)
    ax.set_axis_off()


def _short_label(node_attrs: dict) -> str:
    """Human-readable node label: type abbrev + entity_id prefix.

    The full canonical name isn't on the networkx node (we only ship
    type + entity_id through to_networkx). Six chars of the entity_id
    plus the type initial keeps labels readable without overflowing.
    """
    t = node_attrs.get("type", "?")
    eid = node_attrs.get("entity_id", "?")
    return f"{t[:3]}:{eid[:6]}"


def _draw_legends(ax, nxg: nx.MultiDiGraph) -> None:
    """Two legends: node type (top-right), edge type (bottom-right).

    Only includes types actually present in this graph -- a 21-entry
    legend on a graph with 4 types is noise.
    """
    present_node_types = sorted({
        nxg.nodes[nid]["type"] for nid in nxg.nodes
    })
    present_edge_types = sorted({
        nxg.edges[u, v, k]["edge_type"] for u, v, k in nxg.edges(keys=True)
    })

    node_handles = [
        plt.Line2D(
            [0], [0], marker="o", color="w",
            markerfacecolor=NODE_COLORS.get(t, _GREY),
            markeredgecolor="#222222",
            markersize=8, label=t,
        )
        for t in present_node_types
    ]
    edge_handles = [
        plt.Line2D(
            [0], [0], color=EDGE_COLORS.get(t, _GREY),
            linewidth=2, label=t,
        )
        for t in present_edge_types
    ]

    if node_handles:
        leg1 = ax.legend(
            handles=node_handles, title="node type",
            loc="upper right", fontsize=7, title_fontsize=8,
            framealpha=0.9,
        )
        ax.add_artist(leg1)
    if edge_handles:
        ax.legend(
            handles=edge_handles, title="edge type",
            loc="lower right", fontsize=7, title_fontsize=8,
            framealpha=0.9,
        )


__all__ = ["render", "NODE_COLORS", "EDGE_COLORS"]